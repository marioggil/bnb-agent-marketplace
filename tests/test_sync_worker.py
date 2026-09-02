"""Sync worker default-run tests: chain filter, 429 backoff, get_agent 404.

Spec: `sdd/marketplace-scaffold-tests/spec` sync-tests R1, R3, R4, R7.
Default aiosqlite run covers the no-DB-write paths. Upsert + FIFO + checkpoint
are `@pytest.mark.postgres` and skipped by default; CI (FU-7) wires them.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import httpx

from app.services.client_8004scan import Client8004Scan

BASE = "https://8004scan.io/api/v1/public"


# R1 — chain filter via iter_agents.
async def test_iter_agents_filters_chain_mismatch(respx_mock):
    respx_mock.get(
        f"{BASE}/agents",
        params={"chain_id": 56, "page": 1, "page_size": 200},
    ).respond(
        200,
        json=[
            {
                "agent_id": "56:0x8004...:1",
                "chain_id": 56,
                "token_id": 1,
                "registry": "0x8004...",
                "name": "BSC1",
                "x402_supported": False,
                "supported_protocols": [],
            },
            {
                "agent_id": "1:0xOther:2",
                "chain_id": 1,
                "token_id": 2,
                "registry": "0xOther",
                "name": "ETH",
                "x402_supported": False,
                "supported_protocols": [],
            },
            {
                "agent_id": "56:0x8004...:3",
                "chain_id": 56,
                "token_id": 3,
                "registry": "0x8004...",
                "name": "BSC3",
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
        httpx.Response(
            200,
            json={
                "agent_id": "56:0x8004...:42",
                "chain_id": 56,
                "token_id": 42,
                "registry": "0x8004...",
                "name": "After Backoff",
                "x402_supported": False,
                "supported_protocols": [],
            },
        ),
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


# ---------------------------------------------------------------------------
# agent-score P7 / design D4 — the sync upsert MUST NOT overwrite locally-owned
# columns (health_*, activity_score, scores). The `set_` side filters them out;
# the INSERT `row` (VALUES) keeps all 10 so the conflict-target side is intact.
# ---------------------------------------------------------------------------


class _FakeExcluded:
    """Stand-in for `stmt.excluded`: every column maps to `excluded.<name>`."""

    def __getattr__(self, name: str) -> str:
        return f"excluded.{name}"


# P7/D4 — `_build_set_exprs()` drops all 10 `_LOCAL_OWNED` keys from set_.
def test_build_set_exprs_excludes_all_local_owned_keys():
    from app.services.sync_worker import (
        _LOCAL_OWNED,
        _SET_EXPRS,
        _build_set_exprs,
    )

    assert len(_LOCAL_OWNED) == 10
    set_exprs = _build_set_exprs(_FakeExcluded())
    # Every locally-owned column is absent from the conflict UPDATE side.
    assert set(set_exprs).isdisjoint(_LOCAL_OWNED)
    # Every remaining column the worker mirrors stays present.
    expected = set(_SET_EXPRS) - set(_LOCAL_OWNED)
    assert expected <= set(set_exprs)
    # `updated_at` is still touched (the mirror's own update clock).
    assert "updated_at" in set_exprs


# P7/D4 — the INSERT row (VALUES) keeps all 10 locally-owned columns.
def test_row_values_keep_local_owned_columns():
    from app.services.client_8004scan import AgentResponse
    from app.services.sync_worker import _LOCAL_OWNED, _row_from_agent

    agent = AgentResponse(
        chain_id=56,
        token_id=1,
        health_status={"overall_status": "healthy"},
        health_score=80.0,
        health_checked_at=datetime.now(timezone.utc),
        endpoint_last_checked_at=datetime.now(timezone.utc),
        endpoint_verification_error=None,
        endpoint_verified_domain="agents.example",
        is_endpoint_verified=True,
        endpoint_verified_at=datetime.now(timezone.utc),
        activity_score=87.5,
        scores={"engagement": 91.0},
    )
    row = _row_from_agent(agent, category_override="")
    assert _LOCAL_OWNED <= set(row.keys())
    assert row["activity_score"] == 87.5
    assert row["health_status"] == {"overall_status": "healthy"}


# P7 scenario "Score survives full sync" — a locally-materialized
# activity_score must survive an 8004scan-enrichment upsert.
async def test_activity_score_survives_full_sync(db, respx_mock):
    from decimal import Decimal

    from sqlalchemy import select

    from app.db.models.agent import (
        BSC_CHAIN_ID,
        BSC_IDENTITY_REGISTRY,
        AgentCache,
        build_agent_id,
    )
    from app.services.sync_worker import _enrich_and_upsert
    from tests.conftest import _now

    aid = build_agent_id(BSC_CHAIN_ID, BSC_IDENTITY_REGISTRY, 123)
    db.add(
        AgentCache(
            agent_id=aid,
            chain_id=BSC_CHAIN_ID,
            token_id=123,
            registry_address=BSC_IDENTITY_REGISTRY,
            name="Original",
            activity_score=Decimal("87.50"),
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

    # respx-mocked 8004scan detail for the same token_id.
    respx_mock.get(f"{BASE}/agents/56/123").respond(
        200,
        json={
            "agent_id": aid,
            "chain_id": BSC_CHAIN_ID,
            "token_id": 123,
            "registry": BSC_IDENTITY_REGISTRY,
            "name": "Synced Name",
            "x402_supported": False,
            "supported_protocols": [],
        },
    )

    async with Client8004Scan() as client:
        upserted, failed, last_token_id, walked = await _enrich_and_upsert(db, client, [123], 0)

    assert upserted == 1
    row = await db.scalar(select(AgentCache).where(AgentCache.agent_id == aid))
    assert row is not None
    assert row.activity_score == Decimal("87.50")
