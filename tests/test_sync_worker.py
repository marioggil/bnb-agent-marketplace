"""Sync worker default-run tests: chain filter, 429 backoff, get_agent 404.

Spec: `sdd/marketplace-scaffold-tests/spec` sync-tests R1, R3, R4, R7.
Default aiosqlite run covers the no-DB-write paths. Upsert + FIFO + checkpoint
are `@pytest.mark.postgres` and skipped by default; CI (FU-7) wires them.
"""
from __future__ import annotations

import logging
import time

import httpx

from app.services.client_8004scan import Client8004Scan

BASE = "https://8004scan.io/api/v1/public"


# R1 — chain filter via iter_agents.
async def test_iter_agents_filters_chain_mismatch(respx_mock):
    respx_mock.get(
        f"{BASE}/agents",
        params={"chain_id": 56, "page": 1, "page_size": 200},
    ).respond(200, json=[
        {"agent_id": "56:0x8004...:1", "chain_id": 56, "token_id": 1,
         "registry": "0x8004...", "name": "BSC1", "x402_supported": False,
         "supported_protocols": []},
        {"agent_id": "1:0xOther:2", "chain_id": 1, "token_id": 2,
         "registry": "0xOther", "name": "ETH", "x402_supported": False,
         "supported_protocols": []},
        {"agent_id": "56:0x8004...:3", "chain_id": 56, "token_id": 3,
         "registry": "0x8004...", "name": "BSC3", "x402_supported": False,
         "supported_protocols": []},
    ])
    respx_mock.get(
        f"{BASE}/agents",
        params={"chain_id": 56, "page": 2, "page_size": 200},
    ).respond(200, json=[])
    async with Client8004Scan() as client:
        out = [a.name async for a in client.iter_agents(chain_id=56)]
    assert out == ["BSC1", "BSC3"]


# R3 — get_agent 404 → None.
async def test_get_agent_404_returns_none(respx_mock):
    respx_mock.get(f"{BASE}/agents/56/9999999").respond(404)
    async with Client8004Scan() as client:
        assert await client.get_agent(56, 9999999) is None


# R4 — 429 with Retry-After: backoff and resume.
async def test_429_backoff_then_success(respx_mock):
    route = respx_mock.get(f"{BASE}/agents/56/42")
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "1"}),
        httpx.Response(200, json={
            "agent_id": "56:0x8004...:42", "chain_id": 56, "token_id": 42,
            "registry": "0x8004...", "name": "After Backoff",
            "x402_supported": False, "supported_protocols": [],
        }),
    ]
    started = time.monotonic()
    async with Client8004Scan() as client:
        result = await client.get_agent(56, 42)
    elapsed = time.monotonic() - started
    assert result is not None and result.name == "After Backoff"
    assert elapsed >= 0.8, f"backoff was too short: {elapsed:.2f}s"


# R7 — warning when 8004SCAN_API_KEY is missing.
async def test_api_key_missing_logs_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="app.services.client_8004scan"):
        async with Client8004Scan(api_key=None) as client:
            assert client.api_key in (None, "")
    assert any(
        "8004SCAN_API_KEY" in r.getMessage() or "rate limit" in r.getMessage().lower()
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# Category post-pass (sdd/doc-refresh TAX-3): offchain termix + tags reach
# the classifier; the UPDATE fires only when the rich mapping differs from
# the GENERATED default ({rebalancing, other}).
# ---------------------------------------------------------------------------


async def _seed_agent_row(db, token_id: int, category: str = "other") -> str:
    from app.db.models.agent import (
        BSC_CHAIN_ID,
        BSC_IDENTITY_REGISTRY,
        AgentCache,
        build_agent_id,
    )
    from tests.conftest import _now

    aid = build_agent_id(56, BSC_IDENTITY_REGISTRY, token_id)
    db.add(
        AgentCache(
            agent_id=aid,
            chain_id=BSC_CHAIN_ID,
            token_id=token_id,
            registry_address=BSC_IDENTITY_REGISTRY,
            name=f"Agent {token_id}",
            category=category,
            supported_protocols=[],
            cross_chain_versions=[],
            raw={},
            created_at=_now(),
            updated_at=_now(),
            tags=[],
            categories=[],
        )
    )
    await db.commit()
    return aid


async def _read_category(db, aid: str) -> str:
    from sqlalchemy import select

    from app.db.models.agent import AgentCache

    row = await db.scalar(select(AgentCache).where(AgentCache.agent_id == aid))
    assert row is not None
    return row.category


# TAX-3 — termix source category (priority 1) drives the UPDATE even when
# x402 would have produced the GENERATED default.
async def test_maybe_enrich_category_uses_offchain_termix(db):
    from app.services.client_8004scan import AgentResponse
    from app.services.sync_worker import _maybe_enrich_category

    aid = await _seed_agent_row(db, 7)
    agent = AgentResponse(
        chain_id=56,
        token_id=7,
        supported_protocols=["Web"],
        x402_supported=True,
        raw_metadata={
            "offchain_content": {
                "termix": {"profile": {"category": "Code & Smart Contracts"}},
            }
        },
    )
    await _maybe_enrich_category(db, agent, aid)
    assert await _read_category(db, aid) == "dev_automation"


# TAX-3 — offchain tags (priority 2) classify when termix is absent.
async def test_maybe_enrich_category_uses_offchain_tags(db):
    from app.services.client_8004scan import AgentResponse
    from app.services.sync_worker import _maybe_enrich_category

    aid = await _seed_agent_row(db, 8)
    agent = AgentResponse(
        chain_id=56,
        token_id=8,
        supported_protocols=["Web"],
        x402_supported=False,
        raw_metadata={"offchain_content": {"tags": ["yield", "staking"]}},
    )
    await _maybe_enrich_category(db, agent, aid)
    assert await _read_category(db, aid) == "yield_optimisation"


# TAX-3 — no offchain_content: fall back to the listing tags.
async def test_maybe_enrich_category_falls_back_to_listing_tags(db):
    from app.services.client_8004scan import AgentResponse
    from app.services.sync_worker import _maybe_enrich_category

    aid = await _seed_agent_row(db, 9)
    agent = AgentResponse(
        chain_id=56,
        token_id=9,
        tags=["grid", "dca"],
        supported_protocols=["Web"],
        x402_supported=False,
    )
    await _maybe_enrich_category(db, agent, aid)
    assert await _read_category(db, aid) == "grid_trading"


# TAX-3 — rich == GENERATED default (rebalancing via x402) → no UPDATE.
async def test_maybe_enrich_category_skips_generated_default(db):
    from app.services.client_8004scan import AgentResponse
    from app.services.sync_worker import _maybe_enrich_category

    aid = await _seed_agent_row(db, 10, category="other")
    agent = AgentResponse(
        chain_id=56, token_id=10, supported_protocols=["Web"], x402_supported=True
    )
    await _maybe_enrich_category(db, agent, aid)
    assert await _read_category(db, aid) == "other"

