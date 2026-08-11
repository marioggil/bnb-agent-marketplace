"""Model invariant tests: UNIQUE, FK cascade, raw jsonb roundtrip.

Spec: `sdd/marketplace-scaffold-tests/spec` models-tests R1, R4, R5.
R2 (GENERATED category) and R3 (trigram) are `@pytest.mark.postgres`.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tests.conftest import _now

from app.db.models.agent import (
    AgentCache, BSC_CHAIN_ID, BSC_IDENTITY_REGISTRY, build_agent_id,
)
from app.db.models.favorite import Favorite
from app.db.models.user import User


async def _insert(session, token_id: int, **overrides) -> AgentCache:
    row = AgentCache(
        agent_id=build_agent_id(BSC_CHAIN_ID, BSC_IDENTITY_REGISTRY, token_id),
        chain_id=BSC_CHAIN_ID, token_id=token_id,
        registry_address=BSC_IDENTITY_REGISTRY,
        supported_protocols=[], cross_chain_versions=[],
        created_at=_now(), updated_at=_now(),
        **overrides,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


# R1 — UNIQUE constraints reject duplicates.
async def test_unique_agent_id_rejects_duplicate(db):
    await _insert(db, 1)
    dup = AgentCache(
        agent_id=build_agent_id(BSC_CHAIN_ID, BSC_IDENTITY_REGISTRY, 1),
        chain_id=BSC_CHAIN_ID, token_id=99, registry_address="0xOther",
        name="dup", supported_protocols=[], cross_chain_versions=[], raw={}, created_at=_now(), updated_at=_now(),
    )
    db.add(dup)
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


async def test_unique_composite_chain_token(db):
    await _insert(db, 1)
    dup = AgentCache(
        agent_id="56:0xOtherRegistry:1", chain_id=BSC_CHAIN_ID, token_id=1,
        registry_address="0xOtherRegistry", name="dup",
        supported_protocols=[], cross_chain_versions=[], raw={}, created_at=_now(), updated_at=_now(),
    )
    db.add(dup)
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


# R4 — raw jsonb roundtrip is forward-compat (spec #19 R8).
async def test_raw_jsonb_roundtrip(db):
    payload = {"_meta": {"future": 1, "tags": ["a", "b"]}, "scores": [0.1, 0.9]}
    row = await _insert(db, 7, raw=payload)
    fetched = await db.scalar(select(AgentCache).where(AgentCache.agent_id == row.agent_id))
    assert fetched is not None and fetched.raw == payload


# R5 — FK cascades.
async def test_fk_cascade_user_delete_drops_favorites(db):
    address = "0x" + "11" * 20
    db.add(User(address=address)); await db.commit()
    agent = await _insert(db, 100)
    db.add(Favorite(address=address, agent_id=agent.agent_id)); await db.commit()
    await db.delete(await db.get(User, address)); await db.commit()
    assert await db.scalar(
        select(Favorite).where(Favorite.address == address, Favorite.agent_id == agent.agent_id)
    ) is None


async def test_fk_cascade_agent_delete_drops_favorites(db):
    address = "0x" + "22" * 20
    db.add(User(address=address)); await db.commit()
    agent = await _insert(db, 200)
    db.add(Favorite(address=address, agent_id=agent.agent_id)); await db.commit()
    await db.delete(agent); await db.commit()
    assert await db.scalar(
        select(Favorite).where(Favorite.address == address, Favorite.agent_id == agent.agent_id)
    ) is None


# R2 / R3 (postgres-only) — GENERATED col + trigram.
@pytest.mark.postgres
async def test_generated_category_x402_rebalancing(db):
    row = await _insert(db, 1, x402_supported=True)
    assert row.category == "rebalancing"


@pytest.mark.postgres
async def test_generated_category_other(db):
    row = await _insert(db, 3, supported_protocols=[])
    assert row.category == "other"


@pytest.mark.postgres
async def test_trigram_fuzzy_search(db):
    row = await _insert(db, 1, name="Ave.ai Trading Agent")
    found = await db.scalar(select(AgentCache).where(AgentCache.name.ilike("%traing%")))
    assert found is not None and found.agent_id == row.agent_id
