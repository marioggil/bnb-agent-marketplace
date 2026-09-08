"""Tests for the ERC-8183 escrow hire router.

Flow:
    1. POST /api/hires/escrow         -> 201 with pre-armed calldata (3 calls)
    2. POST /api/hires/escrow/{id}/submit  -> 200, status=FUNDED
    3. GET /api/hires/escrow/{id}      -> 200 with on-chain state

Spec: docs/category-study.md §ERC-8183 (buyer-side).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models.agent import (
    BSC_CHAIN_ID,
    BSC_IDENTITY_REGISTRY,
    AgentCache,
    build_agent_id,
)
from app.db.models.hired_agent import HiredAgent, HiredStatus
from app.services.auth import issue_csrf
from tests.conftest import _now, _sign_in

# Addresses pinned to the BSC mainnet registry — must match
# `migrations/addresses.ts` (apex-contracts).
_ESCROW_COMMERCE = "0xEa4DAa3100A767e86FDed867729ae7446476EBA6"
_ESCROW_ROUTER = "0x51895229E12F9876011789B04f8698af06cCD6DA"
_ESCROW_U_TOKEN = "0xcE24439F2D9C6a2289F741120FE202248B666666"
_AGENT_WALLET = "0x" + "aa" * 20


async def _seed_agent(session, token_id: int, *, wallet: str | None = _AGENT_WALLET) -> str:
    aid = build_agent_id(56, BSC_IDENTITY_REGISTRY, token_id)
    session.add(
        AgentCache(
            agent_id=aid,
            chain_id=BSC_CHAIN_ID,
            token_id=token_id,
            registry_address=BSC_IDENTITY_REGISTRY,
            name=f"A{token_id}",
            agent_wallet=wallet,
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


# ============================================================================
# T1 RED — POST /api/hires/escrow  (happy path)
# ============================================================================


async def test_create_escrow_returns_three_prearmed_calls(client, db):
    """The 201 body must carry three pre-armed calls the browser signs in order:
    approve($U) -> createJob(...) -> fund(jobId, budget)."""
    address, cookie = _sign_in(client)
    aid = await _seed_agent(db, 1)
    payload = {
        "agent_id": aid,
        "task": "Audit my Venus position",
        "budget_wei": 10**17,  # 0.1 $U
    }
    r = client.post("/api/hires/escrow", json=payload, headers=_ch(cookie))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == HiredStatus.PENDING.value
    assert body["provider_address"].lower() == _AGENT_WALLET.lower()
    assert body["commerce_address"].lower() == _ESCROW_COMMERCE.lower()
    assert body["u_token_address"].lower() == _ESCROW_U_TOKEN.lower()
    assert body["chain_id"] == 56
    # Three calls, in order
    assert body["approve"]["description"] == "approve"
    assert body["approve"]["to"].lower() == _ESCROW_U_TOKEN.lower()
    assert body["approve"]["value"] == "0x0"
    assert body["create_job"]["description"] == "createJob"
    assert body["create_job"]["to"].lower() == _ESCROW_COMMERCE.lower()
    assert body["fund"]["description"] == "fund"
    assert body["fund"]["to"].lower() == _ESCROW_COMMERCE.lower()
    # The fund calldata references jobId which is unknown until createJob mines;
    # the router must NOT pre-fill it — placeholder or empty bytes are both ok,
    # but the data field must be valid hex.
    assert body["fund"]["data"].startswith("0x")
    # job_expiry_unix is now + N seconds
    now_unix = int(datetime.now(tz=timezone.utc).timestamp())
    assert body["job_expiry_unix"] >= now_unix + 60 * 60  # at least 1h out
    # createJob calldata encodes the agent wallet as provider
    assert _AGENT_WALLET.lower().removeprefix("0x") in body["create_job"]["data"].lower()


async def test_create_escrow_persists_row_with_rail_erc8183(client, db):
    address, cookie = _sign_in(client)
    aid = await _seed_agent(db, 11)
    r = client.post(
        "/api/hires/escrow",
        json={"agent_id": aid, "task": "t", "budget_wei": 10**17},
        headers=_ch(cookie),
    )
    assert r.status_code == 201, r.text
    hire_id = r.json()["hire_id"]
    row = await db.scalar(select(HiredAgent).where(HiredAgent.id == hire_id))
    assert row is not None
    assert row.rail == "erc8183"
    assert row.status == HiredStatus.PENDING
    assert row.provider_address.lower() == _AGENT_WALLET.lower()
    assert row.budget_wei == 10**17
    assert row.job_id is None  # not yet known — browser reports via /submit
    assert row.chain_id == 56


# ============================================================================
# T2 RED — POST /api/hires/escrow  (validation errors)
# ============================================================================


async def test_create_escrow_without_wallet_returns_503(client, db):
    """Agent without agent_wallet -> AgentOfferUnavailable -> 503."""
    cookie = _sign_in(client)[1]
    aid = await _seed_agent(db, 2, wallet=None)
    r = client.post(
        "/api/hires/escrow",
        json={"agent_id": aid, "task": "t", "budget_wei": 10**17},
        headers=_ch(cookie),
    )
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "agent_offer_unavailable"


async def test_create_escrow_unknown_agent_returns_404(client):
    cookie = _sign_in(client)[1]
    r = client.post(
        "/api/hires/escrow",
        json={
            "agent_id": f"{BSC_CHAIN_ID}:{BSC_IDENTITY_REGISTRY}:9999",
            "task": "t",
            "budget_wei": 10**17,
        },
        headers=_ch(cookie),
    )
    assert r.status_code == 404


async def test_create_escrow_zero_budget_returns_422(client, db):
    cookie = _sign_in(client)[1]
    aid = await _seed_agent(db, 3)
    r = client.post(
        "/api/hires/escrow",
        json={"agent_id": aid, "task": "t", "budget_wei": 0},
        headers=_ch(cookie),
    )
    assert r.status_code == 422


async def test_create_escrow_empty_task_returns_422(client, db):
    cookie = _sign_in(client)[1]
    aid = await _seed_agent(db, 4)
    r = client.post(
        "/api/hires/escrow",
        json={"agent_id": aid, "task": "", "budget_wei": 10**17},
        headers=_ch(cookie),
    )
    assert r.status_code == 422


async def test_create_escrow_unauth_htmx_redirect(client, db):
    aid = await _seed_agent(db, 5)
    r = client.post(
        "/api/hires/escrow",
        json={"agent_id": aid, "task": "t", "budget_wei": 10**17},
        headers={"HX-Request": "true"},
    )
    assert r.headers.get("HX-Redirect") == "/auth"


# ============================================================================
# T3 RED — POST /api/hires/escrow/{id}/submit  (records on-chain jobId + tx)
# ============================================================================


async def test_submit_records_job_id_and_marks_funded(client, db):
    cookie = _sign_in(client)[1]
    aid = await _seed_agent(db, 21)
    create = client.post(
        "/api/hires/escrow",
        json={"agent_id": aid, "task": "t", "budget_wei": 10**17},
        headers=_ch(cookie),
    )
    assert create.status_code == 201
    hire_id = create.json()["hire_id"]
    submit = client.post(
        f"/api/hires/escrow/{hire_id}/submit",
        json={"job_id": 42, "tx_hash": "0x" + "ab" * 32},
        headers=_ch(cookie),
    )
    assert submit.status_code == 200, submit.text
    body = submit.json()
    assert body["status"] == HiredStatus.PAID.value
    assert body["job_id"] == 42
    assert body["tx_hash"] == "0x" + "ab" * 32
    row = await db.scalar(select(HiredAgent).where(HiredAgent.id == hire_id))
    assert row.job_id == 42
    assert row.status == HiredStatus.PAID
    assert row.tx_hash == "0x" + "ab" * 32


async def test_submit_idempotent_on_same_job_id(client, db):
    """Re-submitting the same hire with the same job_id is a no-op (200)."""
    cookie = _sign_in(client)[1]
    aid = await _seed_agent(db, 22)
    create = client.post(
        "/api/hires/escrow",
        json={"agent_id": aid, "task": "t", "budget_wei": 10**17},
        headers=_ch(cookie),
    )
    hire_id = create.json()["hire_id"]
    body = {"job_id": 7, "tx_hash": "0x" + "cc" * 32}
    s1 = client.post(f"/api/hires/escrow/{hire_id}/submit", json=body, headers=_ch(cookie))
    s2 = client.post(f"/api/hires/escrow/{hire_id}/submit", json=body, headers=_ch(cookie))
    assert s1.status_code == 200
    assert s2.status_code == 200
    assert s1.json()["job_id"] == s2.json()["job_id"] == 7


async def test_submit_rejects_conflicting_job_id(client, db):
    """If the browser sends a different job_id for an already-submitted hire,
    the second one must 409 Conflict (prevents accidental overwrites)."""
    cookie = _sign_in(client)[1]
    aid = await _seed_agent(db, 23)
    create = client.post(
        "/api/hires/escrow",
        json={"agent_id": aid, "task": "t", "budget_wei": 10**17},
        headers=_ch(cookie),
    )
    hire_id = create.json()["hire_id"]
    client.post(
        f"/api/hires/escrow/{hire_id}/submit",
        json={"job_id": 7, "tx_hash": "0x" + "cc" * 32},
        headers=_ch(cookie),
    )
    conflict = client.post(
        f"/api/hires/escrow/{hire_id}/submit",
        json={"job_id": 8, "tx_hash": "0x" + "cc" * 32},
        headers=_ch(cookie),
    )
    assert conflict.status_code == 409


async def test_submit_csrf_required(client, db):
    """POST /escrow/{id}/submit without an X-CSRF-Token header -> 403."""
    cookie = _sign_in(client)[1]
    aid = await _seed_agent(db, 24)
    create = client.post(
        "/api/hires/escrow",
        json={"agent_id": aid, "task": "t", "budget_wei": 10**17},
        headers=_ch(cookie),
    )
    hire_id = create.json()["hire_id"]
    # Drop the CSRF header from the submit call.
    r = client.post(
        f"/api/hires/escrow/{hire_id}/submit",
        json={"job_id": 1, "tx_hash": "0x" + "ab" * 32},
        cookies={"bnb_agent_session": cookie},
    )
    assert r.status_code == 403


# ============================================================================
# T4 RED — GET /api/hires/escrow/{id}  (returns hire + on-chain state)
# ============================================================================


async def test_get_escrow_returns_pending_when_not_submitted(client, db):
    cookie = _sign_in(client)[1]
    aid = await _seed_agent(db, 31)
    create = client.post(
        "/api/hires/escrow",
        json={"agent_id": aid, "task": "t", "budget_wei": 10**17},
        headers=_ch(cookie),
    )
    hire_id = create.json()["hire_id"]
    r = client.get(f"/api/hires/escrow/{hire_id}", cookies={"bnb_agent_session": cookie})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "pending"
    assert body["job_id"] is None
    # on-chain state is null because job_id is not known yet
    assert body["onchain_status"] is None


async def test_get_escrow_unauth_returns_401(app, client, db):
    # Use a fresh client so the sign-in cookie is not sent on the unauth GET.
    from fastapi.testclient import TestClient
    cookie = _sign_in(client)[1]
    aid = await _seed_agent(db, 32)
    create = client.post(
        "/api/hires/escrow",
        json={"agent_id": aid, "task": "t", "budget_wei": 10**17},
        headers=_ch(cookie),
    )
    hire_id = create.json()["hire_id"]
    fresh = TestClient(app)
    r = fresh.get(f"/api/hires/escrow/{hire_id}")
    assert r.status_code == 401


async def test_get_escrow_other_user_forbidden(client, db):
    """One user creates a hire; another user tries to GET it -> 403."""
    owner_cookie = _sign_in(client)[1]
    aid = await _seed_agent(db, 33)
    create = client.post(
        "/api/hires/escrow",
        json={"agent_id": aid, "task": "t", "budget_wei": 10**17},
        headers=_ch(owner_cookie),
    )
    hire_id = create.json()["hire_id"]
    # Sign in as a different user (fresh address each call)
    _other, other_cookie = _sign_in(client)
    r = client.get(f"/api/hires/escrow/{hire_id}", cookies={"bnb_agent_session": other_cookie})
    assert r.status_code == 403


# ============================================================================
# T5 RED — auth + CSRF guards on every write endpoint
# ============================================================================


async def test_create_escrow_csrf_required(client, db):
    cookie = _sign_in(client)[1]
    aid = await _seed_agent(db, 41)
    r = client.post(
        "/api/hires/escrow",
        json={"agent_id": aid, "task": "t", "budget_wei": 10**17},
        cookies={"bnb_agent_session": cookie},  # no CSRF header
    )
    assert r.status_code == 403
