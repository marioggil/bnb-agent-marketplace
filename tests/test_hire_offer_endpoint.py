"""Hire-offer endpoint tests (x402-agent-hire PR 2): GET /agents/{chain}/{token}/hire-offer.

Spec: x402-agent-hire R2 (a2a first, agent_url fallback), R3 (real price on
rail match), R4 (disabled otherwise), R9 (lazy HTMX contract).
TDD: RED against the missing endpoint/partial/schema, then GREEN.
"""

from __future__ import annotations

import httpx

from app.config import _settings_cache, get_settings
from app.db.models.agent import (
    BSC_CHAIN_ID,
    BSC_IDENTITY_REGISTRY,
    AgentCache,
    build_agent_id,
)
from tests.conftest import _now, payai_header

_A2A = "https://example.com/a2a/card"
_AGENT_URL = "https://example.com/chat"
_WALLET = "0x" + "77" * 20


async def _seed(
    session,
    token_id: int = 1,
    *,
    a2a: str | None = None,
    agent_url: str | None = None,
    wallet: str | None = _WALLET,
    x402: bool = True,
) -> str:
    aid = build_agent_id(56, BSC_IDENTITY_REGISTRY, token_id)
    session.add(
        AgentCache(
            agent_id=aid,
            chain_id=BSC_CHAIN_ID,
            token_id=token_id,
            registry_address=BSC_IDENTITY_REGISTRY,
            name=f"A{token_id}",
            agent_wallet=wallet,
            x402_supported=x402,
            a2a_endpoint=a2a,
            agent_url=agent_url,
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


def _offer_endpoint(client, token_id: int = 1) -> str:
    r = client.get(f"/agents/{BSC_CHAIN_ID}/{token_id}/hire-offer")
    assert r.status_code == 200, r.text
    return r.text


# T8 RED — supported offer on the rail → real price rendered, enabled.
async def test_hire_offer_renders_real_price(client, db, respx_mock, monkeypatch):
    monkeypatch.setenv("X402_FEE_WALLET", "0x" + "88" * 20)
    _settings_cache.cache_clear()
    try:
        await _seed(db, 1, a2a=_A2A)
        respx_mock.get(_A2A).respond(402, headers={"payment-required": _supported_header()})
        body = _offer_endpoint(client, 1)
        assert "Hire for $" in body
        assert "marketplace fee" in body
        assert 'data-has-offer="true"' in body
        # D-8: the fragment renders the slot content, never a second button.
        assert "<button" not in body
    finally:
        _settings_cache.cache_clear()


# T10 RED — no endpoint → "Not available" + disabled-state script, no probe.
async def test_hire_offer_disabled_when_no_endpoint(client, db, respx_mock):
    await _seed(db, 2, a2a=None, agent_url=None)
    body = _offer_endpoint(client, 2)
    assert "Not available" in body
    assert 'data-has-offer="false"' in body
    assert "disabled" in body  # the idempotent inline script sets #hire-cta.disabled
    assert respx_mock.calls == []  # no probe issued


# T12 RED — unsupported asset/network (raw PayAI fixture) → "Not available".
async def test_hire_offer_disabled_when_unsupported_asset(client, db, respx_mock):
    await _seed(db, 3, a2a=_A2A)
    # Raw PayAI fixture: eip155:84532 + base-sepolia asset → off the rail.
    respx_mock.get(_A2A).respond(402, headers={"payment-required": payai_header()})
    body = _offer_endpoint(client, 3)
    assert "Not available" in body
    assert 'data-has-offer="false"' in body
    assert "Hire for $" not in body


# D-2 — unreachable / non-402 endpoint → "Not available".
async def test_hire_offer_disabled_when_endpoint_unreachable(client, db, respx_mock):
    await _seed(db, 4, a2a=_A2A)
    respx_mock.get(_A2A).respond(500)
    body = _offer_endpoint(client, 4)
    assert "Not available" in body
    assert 'data-has-offer="false"' in body


# D-1 — a2a_endpoint preferred over agent_url when both are set.
async def test_hire_offer_prefers_a2a_over_agent_url(client, db, respx_mock):
    await _seed(db, 5, a2a=_A2A, agent_url=_AGENT_URL)
    respx_mock.get(_A2A).respond(402, headers={"payment-required": _supported_header()})
    respx_mock.get(_AGENT_URL).respond(402, headers={"payment-required": _supported_header()})
    body = _offer_endpoint(client, 5)
    assert "Hire for $" in body
    urls = [c.request.url for c in respx_mock.calls]
    assert str(urls[0]).startswith(_A2A)
    assert not any(str(u).startswith(_AGENT_URL) for u in urls)


# D-2 — agent_url fallback when a2a_endpoint is null.
async def test_hire_offer_falls_back_to_agent_url(client, db, respx_mock):
    await _seed(db, 6, a2a=None, agent_url=_AGENT_URL)
    respx_mock.get(_AGENT_URL).respond(402, headers={"payment-required": _supported_header()})
    body = _offer_endpoint(client, 6)
    assert "Hire for $" in body


# wallet-gate — agent without a payment wallet → disabled, no probe.
async def test_hire_offer_disabled_without_wallet(client, db, respx_mock):
    await _seed(db, 7, a2a=_A2A, wallet=None, x402=False)
    body = _offer_endpoint(client, 7)
    assert "Not available" in body
    assert 'data-has-offer="false"' in body
    assert respx_mock.calls == []


# Timeout / probe failure → "Not available" (probe_agent_offer returns None).
async def test_hire_offer_disabled_on_probe_timeout(client, db, respx_mock):
    await _seed(db, 8, a2a=_A2A)
    respx_mock.get(_A2A).mock(side_effect=httpx.TimeoutException("timed out"))
    body = _offer_endpoint(client, 8)
    assert "Not available" in body
    assert 'data-has-offer="false"' in body