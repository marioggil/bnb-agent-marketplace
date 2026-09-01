"""8004scan client contract tests: 429 retry, 404 None, chain filter, drift.

Spec: `sdd/marketplace-scaffold-tests/spec` agents-tests R5-R8.
"""

from __future__ import annotations

import time

import httpx
import pytest

from app.services.client_8004scan import Client8004Scan, UpstreamRateLimit

BASE = "https://8004scan.io/api/v1/public"


# R5 — 429 honors Retry-After; second call within the budget succeeds.
async def test_429_retry_after_honored(respx_mock):
    route = respx_mock.get(f"{BASE}/agents/56/42")
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "1"}),
        httpx.Response(
            200,
            json={
                "agent_id": "56:0x8004A169FB4a3325136EB29fA0ceB6D2e539a432:42",
                "chain_id": 56,
                "token_id": 42,
                "registry": "0x8004...",
                "name": "A",
                "x402_supported": True,
                "supported_protocols": ["oasf"],
                "average_score": 80.0,
                "total_feedbacks": 3,
                "is_verified": True,
            },
        ),
    ]
    started = time.monotonic()
    async with Client8004Scan() as client:
        result = await client.get_agent(56, 42)
    elapsed = time.monotonic() - started
    assert result is not None and result.name == "A"
    assert elapsed >= 0.8, f"429 backoff did not honor Retry-After: {elapsed:.2f}s"
    assert route.call_count == 2


async def test_429_exhausted_raises(respx_mock):
    respx_mock.get(f"{BASE}/agents/56/42").respond(429, headers={"Retry-After": "0"})
    async with Client8004Scan() as client:
        with pytest.raises(UpstreamRateLimit):
            await client.get_agent(56, 42)


# R6 — 404 returns None (NOT raise).
async def test_404_returns_none(respx_mock):
    respx_mock.get(f"{BASE}/agents/56/9999999").respond(404)
    async with Client8004Scan() as client:
        assert await client.get_agent(56, 9999999) is None


# R7 — iter_agents(chain_id=56) skips non-BSC rows.
async def test_iter_agents_filters_chain_mismatch(respx_mock):
    respx_mock.get(
        f"{BASE}/agents",
        params={"chain_id": 56, "page": 1, "page_size": 200},
    ).respond(
        200,
        json=[
            {
                "agent_id": "56:0x8004...:42",
                "chain_id": 56,
                "token_id": 42,
                "registry": "0x8004...",
                "name": "BSC",
                "x402_supported": False,
                "supported_protocols": [],
            },
            {
                "agent_id": "1:0xOther:7",
                "chain_id": 1,
                "token_id": 7,
                "registry": "0xOther",
                "name": "ETH",
                "x402_supported": False,
                "supported_protocols": [],
            },
        ],
    )
    respx_mock.get(
        f"{BASE}/agents",
        params={"chain_id": 56, "page": 2, "page_size": 200},
    ).respond(200, json=[])
    async with Client8004Scan() as client:
        out = [a async for a in client.iter_agents(chain_id=56)]
    assert len(out) == 1 and out[0].chain_id == 56 and out[0].name == "BSC"


# R8 — upstream field drift lands in `raw`.
async def test_field_drift_into_raw(respx_mock):
    respx_mock.get(f"{BASE}/agents/56/42").respond(
        200,
        json={
            "agent_id": "56:0x8004...:42",
            "chain_id": 56,
            "token_id": 42,
            "registry": "0x8004...",
            "name": "Drift",
            "x402_supported": False,
            "supported_protocols": [],
            "tvl_usd": 12345.67,
            "socials": {"twitter": "@drift"},
        },
    )
    async with Client8004Scan() as client:
        result = await client.get_agent(56, 42)
    assert result is not None and result.name == "Drift"
    assert result.raw.get("tvl_usd") == 12345.67
    assert result.raw.get("socials") == {"twitter": "@drift"}
