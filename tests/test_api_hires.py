"""Hire API tests: happy (status=pending), unknown agent (404), auth.

Spec: `sdd/marketplace-scaffold-tests/spec` favorites-hires-tests R5, R6.
"""

from __future__ import annotations

from app.db.models.agent import (
    AgentCache,
    BSC_CHAIN_ID,
    BSC_IDENTITY_REGISTRY,
    build_agent_id,
)
from app.services.auth import issue_csrf
from tests.conftest import _now, _sign_in


async def _seed(session, token_id: int) -> str:
    aid = build_agent_id(56, BSC_IDENTITY_REGISTRY, token_id)
    session.add(
        AgentCache(
            agent_id=aid,
            chain_id=BSC_CHAIN_ID,
            token_id=token_id,
            registry_address=BSC_IDENTITY_REGISTRY,
            name=f"A{token_id}",
            # FU-2 X2: hire creation now 422s without a payment wallet, so the
            # happy-path seed must carry one (PR-A note, WU5).
            agent_wallet="0x" + "77" * 20,
            supported_protocols=[],
            cross_chain_versions=[],
            raw={},
            created_at=_now(),
            updated_at=_now(),
        )
    )
    await session.commit()
    return aid


def _ch(cookie: str) -> dict:
    return {"bnb_agent_session": cookie, "X-CSRF-Token": issue_csrf(cookie)}


# R5 — happy POST /api/hires returns 201 with status=pending + x402 data.
async def test_hire_happy_returns_pending(client, db):
    address, cookie = _sign_in(client)
    aid = await _seed(db, 1)
    r = client.post("/api/hires", json={"agent_id": aid}, headers=_ch(cookie))
    assert r.status_code == 201
    body = r.json()
    assert body["address"].lower() == address.lower() and body["agent_id"] == aid
    assert body["status"] == "pending" and body["tx_hash"] is None
    # FU-2: challenge + payment metadata (spec X1/H1).
    assert body["challenge"]["accepts"][0]["payTo"] == "0x" + "77" * 20
    assert body["pay_to"] == "0x" + "77" * 20
    assert body["rail"] == "eip3009" and float(body["amount"]) == 1.0


# R6 — unknown agent → 404.
async def test_hire_unknown_agent_returns_404(client):
    _a, cookie = _sign_in(client)
    r = client.post(
        "/api/hires",
        json={"agent_id": "56:0x8004A169FB4a3325136EB29fA0ceB6D2e539a432:9999"},
        headers=_ch(cookie),
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


# Auth — no session + HTMX → HX-Redirect: /auth.
async def test_hire_unauth_htmx_redirect(client):
    r = client.post(
        "/api/hires",
        json={"agent_id": "56:0x8004A169FB4a3325136EB29fA0ceB6D2e539a432:1"},
        headers={"HX-Request": "true"},
    )
    assert r.headers.get("HX-Redirect") == "/auth"


# CSRF required.
async def test_hire_csrf_required(client, db):
    _a, cookie = _sign_in(client)
    aid = await _seed(db, 1)
    r = client.post(
        "/api/hires",
        json={"agent_id": aid},
        cookies={"bnb_agent_session": cookie},
    )
    assert r.status_code == 403
