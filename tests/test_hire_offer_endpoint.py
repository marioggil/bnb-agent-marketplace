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


def _supported_header_for_solana() -> str:
    return payai_header(asset="0x" + "44" * 20, network="solana:mainnet")


def _supported_header(chain_id: int | None = None) -> str:
    settings = get_settings()
    if chain_id is None:
        chain_id = settings.x402_chain_id
    rail = settings.x402_rail_for(chain_id)
    return payai_header(asset=rail.token_address, network=f"eip155:{chain_id}")


# x402-remove-fee-multichain (R8): any rail-map EVM chain is enabled and the
# partial never renders a marketplace-fee line.
_RAIL_MAP_CHAINS = [8453, 137, 43114]


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
        # R8 — the fee line is gone even when a fee wallet is configured.
        assert "marketplace fee" not in body
        assert 'data-has-offer="true"' in body
        # D-8: the fragment renders the slot content, never a second button.
        assert "<button" not in body
    finally:
        _settings_cache.cache_clear()


# T15 RED — every rail-map EVM chain (Base/Polygon/Avalanche USDC) renders
# an enabled Hire CTA with the probe price and no fee text (R8).
async def test_hire_offer_enabled_for_rail_map_evm_chains(client, db, respx_mock, monkeypatch):
    monkeypatch.setenv("X402_FEE_WALLET", "0x" + "88" * 20)
    _settings_cache.cache_clear()
    try:
        for idx, chain_id in enumerate(_RAIL_MAP_CHAINS, start=11):
            endpoint = f"{_A2A}-{chain_id}"
            await _seed(db, idx, a2a=endpoint)
            respx_mock.get(endpoint).respond(
                402, headers={"payment-required": _supported_header(chain_id)}
            )
            body = _offer_endpoint(client, idx)
            assert "Hire for $" in body, f"chain {chain_id} not enabled"
            assert "marketplace fee" not in body
            assert 'data-has-offer="true"' in body
    finally:
        _settings_cache.cache_clear()


# T15 RED — unsupported networks (non-EVM / out-of-map) stay disabled.
async def test_hire_offer_disabled_for_non_evm_and_out_of_map(client, db, respx_mock):
    await _seed(db, 21, a2a=_A2A)
    # Solana network (non-eip155) → disabled.
    respx_mock.get(_A2A).respond(
        402, headers={"payment-required": _supported_header_for_solana()}
    )
    body = _offer_endpoint(client, 21)
    assert "Not available" in body
    assert 'data-has-offer="false"' in body
    assert "Hire for $" not in body


# T18 TRIANGULATE — the rendered price is the probe estimate (display-only):
# raw-wei amount 10000 → price_usd 1e-14 renders as the probe value, never
# the fee-inflated flat price $1.03.
async def test_price_usd_is_probe_estimate_only(client, db, respx_mock, monkeypatch):
    monkeypatch.setenv("X402_FEE_WALLET", "0x" + "88" * 20)
    _settings_cache.cache_clear()
    try:
        await _seed(db, 31, a2a=_A2A)
        respx_mock.get(_A2A).respond(402, headers={"payment-required": _supported_header()})
        body = _offer_endpoint(client, 31)
        assert "Hire for $0.00" in body  # Decimal(10000)/10**18, no fee added
        assert "Hire for $1.03" not in body
        assert "marketplace fee" not in body
    finally:
        _settings_cache.cache_clear()


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