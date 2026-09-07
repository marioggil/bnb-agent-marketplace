"""Hire API tests: happy (status=pending), unknown agent (404), auth.

Spec: `sdd/marketplace-scaffold-tests/spec` favorites-hires-tests R5, R6.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.config import _settings_cache, get_settings
from app.db.models.agent import (
    BSC_CHAIN_ID,
    BSC_IDENTITY_REGISTRY,
    AgentCache,
    build_agent_id,
)
from app.db.models.hired_agent import HiredAgent
from app.services.auth import issue_csrf
from tests.conftest import _now, _sign_in, payai_header


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


# ---------------------------------------------------------------------------
# x402-agent-hire: create_hire uses the probed agent offer, or flat fallback,
# and persists the 4 evidence columns (AC-4/AC-5/AC-6, design §8 + §10).
# ---------------------------------------------------------------------------

_OFFER_PAYTO = "0x71C7656EC7ab88b098defB751B7401B5f6d8976F"
_OFFER_AMOUNT_WEI = 10000
_A2A_ENDPOINT = "https://example.com/a2a/card"


async def _seed_with_endpoint(session, token_id: int, *, endpoint: str | None = None) -> str:
    aid = build_agent_id(56, BSC_IDENTITY_REGISTRY, token_id)
    session.add(
        AgentCache(
            agent_id=aid,
            chain_id=BSC_CHAIN_ID,
            token_id=token_id,
            registry_address=BSC_IDENTITY_REGISTRY,
            name=f"A{token_id}",
            agent_wallet="0x" + "77" * 20,
            x402_supported=True,
            a2a_endpoint=endpoint,
            supported_protocols=[],
            cross_chain_versions=[],
            raw={},
            created_at=_now(),
            updated_at=_now(),
        )
    )
    await session.commit()
    return aid


def _supported_header() -> str:
    settings = get_settings()
    return payai_header(
        asset=settings.x402_u_token_address,
        network=f"eip155:{settings.x402_chain_id}",
    )


# T14 RED — offer available: accepts[0] uses the agent's real payTo/amount,
# accepts[1] is the marketplace fee, evidence columns are populated.
async def test_create_hire_uses_agent_offer_when_available(
    client, db, respx_mock, monkeypatch
):
    monkeypatch.setenv("X402_FEE_WALLET", "0x" + "88" * 20)
    _settings_cache.cache_clear()
    try:
        address, cookie = _sign_in(client)
        aid = await _seed_with_endpoint(db, 7, endpoint=_A2A_ENDPOINT)
        respx_mock.get(_A2A_ENDPOINT).respond(
            402, headers={"payment-required": _supported_header()}
        )
        r = client.post("/api/hires", json={"agent_id": aid}, headers=_ch(cookie))
        assert r.status_code == 201, r.text
        body = r.json()
        accepts = body["challenge"]["accepts"]
        # AC-4: real offer values for accepts[0].
        assert accepts[0]["payTo"] == _OFFER_PAYTO
        assert int(accepts[0]["amount"]) == _OFFER_AMOUNT_WEI
        # AC-6: fee still applies on top as accepts[1].
        assert len(accepts) == 2
        assert accepts[1]["payTo"] == "0x" + "88" * 20
        assert int(accepts[1]["amount"]) == int(0.03 * 10**18)
        # Evidence echo in the response (T19 fills the schema).
        assert body["pay_to_agent"] == _OFFER_PAYTO
        assert body["amount_agent"] is not None
        assert body["asset_agent"] == get_settings().x402_u_token_address
        assert body["network_agent"] == f"eip155:{get_settings().x402_chain_id}"

        # AC-5: evidence columns persisted on the row.
        row = await db.scalar(select(HiredAgent).where(HiredAgent.agent_id == aid))
        assert row is not None
        assert row.pay_to_agent == _OFFER_PAYTO
        assert int(row.amount_agent * 10**18) == _OFFER_AMOUNT_WEI
        assert row.asset_agent == get_settings().x402_u_token_address
        assert row.network_agent == f"eip155:{get_settings().x402_chain_id}"
    finally:
        _settings_cache.cache_clear()


# T16 RED — no offer (no endpoint): flat price + agent wallet, evidence None.
async def test_create_hire_falls_back_to_flat_without_offer(client, db, respx_mock):
    address, cookie = _sign_in(client)
    aid = await _seed_with_endpoint(db, 8, endpoint=None)
    r = client.post("/api/hires", json={"agent_id": aid}, headers=_ch(cookie))
    assert r.status_code == 201, r.text
    body = r.json()
    accepts = body["challenge"]["accepts"]
    assert accepts[0]["payTo"] == "0x" + "77" * 20  # agent wallet
    assert int(accepts[0]["amount"]) == int(Decimal("1.00") * 10**18)  # flat
    assert body["amount_agent"] is None
    assert body["pay_to_agent"] is None
    assert body["asset_agent"] is None
    assert body["network_agent"] is None


# T16 — probe 500 (unreachable) → flat fallback, evidence None.
async def test_create_hire_falls_back_to_flat_when_probe_unreachable(
    client, db, respx_mock
):
    address, cookie = _sign_in(client)
    aid = await _seed_with_endpoint(db, 9, endpoint=_A2A_ENDPOINT)
    respx_mock.get(_A2A_ENDPOINT).respond(500)
    r = client.post("/api/hires", json={"agent_id": aid}, headers=_ch(cookie))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["challenge"]["accepts"][0]["payTo"] == "0x" + "77" * 20
    assert body["amount_agent"] is None
    assert body["pay_to_agent"] is None


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
