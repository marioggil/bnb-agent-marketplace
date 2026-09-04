"""Re-classification service + endpoint tests.

Spec: category taxonomy fix — one-shot local pass applies the current
classifier (termix/tags/description/protocols) to every cached agent.
"""

from __future__ import annotations

import pytest

from sqlalchemy import select

from app.db.models.agent import (
    BSC_CHAIN_ID,
    BSC_IDENTITY_REGISTRY,
    AgentCache,
    build_agent_id,
)
from tests.conftest import _now

API_HEADERS = {"X-API-Key": "test-sync-api-key"}


@pytest.fixture
def sync_key_fixture(monkeypatch):
    monkeypatch.setenv("SYNC_API_KEY", "test-sync-api-key")
    return "test-sync-api-key"


async def _seed(
    db,
    token_id: int,
    *,
    description: str | None = None,
    tags: list[str] | None = None,
    protocols: list[str] | None = None,
    x402: bool = False,
    category: str = "other",
    termix_category: str | None = None,
) -> str:
    aid = build_agent_id(BSC_CHAIN_ID, BSC_IDENTITY_REGISTRY, token_id)
    raw_metadata = None
    if termix_category:
        raw_metadata = {
            "offchain_content": {"termix": {"profile": {"category": termix_category}}}
        }
    now = _now()
    db.add(
        AgentCache(
            agent_id=aid,
            chain_id=BSC_CHAIN_ID,
            token_id=token_id,
            registry_address=BSC_IDENTITY_REGISTRY,
            name=f"A{token_id}",
            description=description,
            tags=tags or [],
            supported_protocols=protocols or [],
            x402_supported=x402,
            raw_metadata=raw_metadata,
            raw={},
            category=category,
            cross_chain_versions=[],
            created_at=now,
            updated_at=now,
        )
    )
    await db.commit()
    return aid


async def _category(db, agent_id: str) -> str:
    row = await db.scalar(select(AgentCache).where(AgentCache.agent_id == agent_id))
    return row.category


async def test_reclassify_applies_description_hints(db):
    aid = await _seed(
        db, 1, description="EZCTO Deployer Agent generates websites", x402=True
    )
    report = await _reclassify(db)
    assert report.updated == 1
    assert await _category(db, aid) == "dev_automation"


async def test_reclassify_termix_wins(db):
    aid = await _seed(
        db, 2, termix_category="Code & Smart Contracts", x402=True
    )
    report = await _reclassify(db)
    assert await _category(db, aid) == "dev_automation"


async def test_reclassify_tags_when_no_description(db):
    aid = await _seed(db, 3, tags=["grid"], x402=True)
    report = await _reclassify(db)
    assert await _category(db, aid) == "grid_trading"


async def test_reclassify_x402_alone_stays_other(db):
    aid = await _seed(db, 4, x402=True, category="rebalancing")
    report = await _reclassify(db)
    assert await _category(db, aid) == "other"


async def test_reclassify_idempotent(db):
    await _seed(db, 5, description="Trading bot for grid strategies")
    first = await _reclassify(db)
    assert first.updated == 1
    second = await _reclassify(db)
    assert second.updated == 0


def test_reclassify_endpoint_requires_key(sync_key_fixture, client):
    response = client.post("/api/sync/reclassify")
    assert response.status_code == 401


async def test_reclassify_endpoint_with_key(sync_key_fixture, client, db):
    aid = await _seed(db, 6, description="Deployer agent for websites")
    response = client.post("/api/sync/reclassify", headers=API_HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["updated"] == 1
    assert body["by_category"].get("dev_automation") == 1


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _reclassify(db):
    from app.services.reclassify import reclassify_cached_agents

    return await reclassify_cached_agents(db)