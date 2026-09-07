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


# T8 RED — offer-driven create: accepts[0] from the offer, exactly one accept
# (no fee), evidence persisted. Base 8453 USDC offer (x402-remove-fee-multichain).
_BASE_USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
_BASE_CHAIN = 8453


def _base_usdc_header() -> str:
    return payai_header(asset=_BASE_USDC, network=f"eip155:{_BASE_CHAIN}")


async def test_create_hire_probes_offer_and_sets_evidence(
    client, db, respx_mock, monkeypatch
):
    monkeypatch.setenv("X402_FEE_WALLET", "0x" + "88" * 20)
    _settings_cache.cache_clear()
    try:
        address, cookie = _sign_in(client)
        aid = await _seed_with_endpoint(db, 7, endpoint=_A2A_ENDPOINT)
        respx_mock.get(_A2A_ENDPOINT).respond(
            402, headers={"payment-required": _base_usdc_header()}
        )
        r = client.post("/api/hires", json={"agent_id": aid}, headers=_ch(cookie))
        assert r.status_code == 201, r.text
        body = r.json()
        accepts = body["challenge"]["accepts"]
        # AC-3/AC-2: quote comes from the offer, single accept, no fee.
        assert len(accepts) == 1
        assert accepts[0]["payTo"] == _OFFER_PAYTO
        assert int(accepts[0]["amount"]) == _OFFER_AMOUNT_WEI
        assert accepts[0]["network"] == f"eip155:{_BASE_CHAIN}"
        assert accepts[0]["asset"] == _BASE_USDC
        assert accepts[0]["extra"]["name"] == "USD Coin"
        assert accepts[0]["extra"]["version"] == "2"
        # AC-4: evidence echo in the response.
        assert body["pay_to_agent"] == _OFFER_PAYTO
        assert body["amount_agent"] is not None
        assert body["asset_agent"] == _BASE_USDC
        assert body["network_agent"] == f"eip155:{_BASE_CHAIN}"
        # AC-3/AC-4 (amount semantics): raw wei, token-agnostic, no *10**18.
        assert Decimal(body["amount"]) == Decimal(_OFFER_AMOUNT_WEI)

        # AC-4/AC-5: evidence columns persisted on the row.
        row = await db.scalar(select(HiredAgent).where(HiredAgent.agent_id == aid))
        assert row is not None
        assert row.pay_to_agent == _OFFER_PAYTO
        assert row.amount_agent == Decimal(_OFFER_AMOUNT_WEI) / Decimal(10**18)
        assert row.asset_agent == _BASE_USDC
        assert row.network_agent == f"eip155:{_BASE_CHAIN}"
        assert row.amount == Decimal(_OFFER_AMOUNT_WEI)
        assert row.token == _BASE_USDC
        assert row.pay_to == _OFFER_PAYTO
    finally:
        _settings_cache.cache_clear()


# T10 RED — no offer / unsupported offer → 503 agent_offer_unavailable (R4/Q2).
# The flat-price fallback is gone: no offer (endpoint down/absent) is an error.
async def test_create_hire_agent_offer_unavailable_503(client, db, respx_mock):
    # No endpoint at all → probe is None → 503, no hire row.
    address, cookie = _sign_in(client)
    aid = await _seed_with_endpoint(db, 8, endpoint=None)
    r = client.post("/api/hires", json={"agent_id": aid}, headers=_ch(cookie))
    assert r.status_code == 503, r.text
    assert r.json()["error"]["code"] == "agent_offer_unavailable"
    assert "challenge" not in r.json()

    # Endpoint unreachable (500) → probe None → 503.
    aid2 = await _seed_with_endpoint(db, 9, endpoint=_A2A_ENDPOINT)
    respx_mock.get(_A2A_ENDPOINT).respond(500)
    r2 = client.post("/api/hires", json={"agent_id": aid2}, headers=_ch(cookie))
    assert r2.status_code == 503, r2.text
    assert r2.json()["error"]["code"] == "agent_offer_unavailable"


# T10 — an offer on a chain outside the rail map (eip155:84532) is unsupported
# → 503 agent_offer_unavailable; no hire row, no flat price.
async def test_create_hire_unsupported_offer_503(client, db, respx_mock):
    address, cookie = _sign_in(client)
    aid = await _seed_with_endpoint(db, 10, endpoint=_A2A_ENDPOINT)
    respx_mock.get(_A2A_ENDPOINT).respond(
        402, headers={"payment-required": payai_header()}  # raw fixture: eip155:84532
    )
    r = client.post("/api/hires", json={"agent_id": aid}, headers=_ch(cookie))
    assert r.status_code == 503, r.text
    assert r.json()["error"]["code"] == "agent_offer_unavailable"
    row = await db.scalar(select(HiredAgent).where(HiredAgent.agent_id == aid))
    assert row is None


# R5 — happy POST /api/hires returns 201 with status=pending + x402 data.
# Offer-driven now: seed a supported rail offer (BSC testnet $U, eip155:97).
async def test_hire_happy_returns_pending(client, db, respx_mock):
    address, cookie = _sign_in(client)
    aid = await _seed_with_endpoint(db, 1, endpoint=_A2A_ENDPOINT)
    respx_mock.get(_A2A_ENDPOINT).respond(
        402, headers={"payment-required": _supported_header()}
    )
    r = client.post("/api/hires", json={"agent_id": aid}, headers=_ch(cookie))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["address"].lower() == address.lower() and body["agent_id"] == aid
    assert body["status"] == "pending" and body["tx_hash"] is None
    # FU-2: challenge + payment metadata (spec X1/H1) — quoted wei is echoed.
    assert body["challenge"]["accepts"][0]["payTo"] == _OFFER_PAYTO
    assert body["pay_to"] == _OFFER_PAYTO
    assert body["rail"] == "eip3009"
    assert Decimal(body["amount"]) == Decimal(_OFFER_AMOUNT_WEI)


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
