"""Shared seeding helpers for Phase 1b compliance tests.

Spec: `openspec/changes/compliance-flags/spec.md` (Phase 1b, §4.6).

Phase 1a-i added `agent_cache.compliance_penalty` and the
`agent_compliance_flags` table. Phase 1a-ii populated them via the
`run_compliance_refresh()` orchestrator. Phase 1b's tests need both rows
to exist before the route/template assertions run.

Because the Phase 1b tests may land on a branch that hasn't yet pulled
the merged 1a-ii code (in stack-order out-of-order apply scenarios),
this helper inserts the rows via raw SQL — mirroring the production
schema shape and the orchestrator UPSERT payload — so the assertions
work whether or not the merged-in orchestrator has been called.

Used by:
- `tests/test_pages.py` — Hire CTA gate scenarios
- `tests/test_compliance_api.py` — `/score` endpoint additive fields

Single source of truth for the test seed: any drift between this
fixture and the production schema is caught by the test suite.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession


async def seed_compliance_agent(
    session: AsyncSession,
    agent_id: str,
    creator_address: str,
    owner_address: str,
    *,
    creator_flagged: bool = False,
    owner_flagged: bool = False,
    compliance_penalty: int | float = 0,
    activity_score: float = 72.50,
) -> str:
    """Insert one `agent_cache` row + an optional `agent_compliance_flags` row.

    Mirrors the production schema (`0012_compliance_penalty.py` migration
    + `refresh_agent_compliance_flags()` UPSERT payload) so the tests
    render exactly what 1a-i + 1a-ii would produce in production.

    Returns the `agent_id` for chaining further setup.
    """
    chain_id, registry, token_part = agent_id.split(":", 2)
    token_id = int(token_part)

    now = datetime.now(timezone.utc)
    # Explicit bindparam(type_=DateTime(...)) routes tz-aware datetimes through
    # SQLAlchemy's adapter, avoiding sqlite3's deprecated default datetime
    # adapter (filterwarnings=error turns that into a test failure).
    # `ON CONFLICT DO UPDATE` keeps the helper idempotent: it can be applied on
    # top of an `agent_cache` row that `_seed_one` and friends already inserted
    # without raising an IntegrityError. Supported by both sqlite and postgres.
    stmt = text(
        """
        INSERT INTO agent_cache (
            agent_id,
            chain_id,
            token_id,
            registry_address,
            creator_address,
            owner_address,
            activity_score,
            compliance_penalty,
            supported_protocols,
            cross_chain_versions,
            raw,
            tags,
            categories,
            created_at,
            updated_at
        ) VALUES (
            :agent_id,
            :chain_id,
            :token_id,
            :registry,
            :creator,
            :owner,
            :activity_score,
            :compliance_penalty,
            '[]',
            '[]',
            '{}',
            '[]',
            '[]',
            :now,
            :now
        )
        ON CONFLICT(agent_id) DO UPDATE SET
            creator_address = excluded.creator_address,
            owner_address = excluded.owner_address,
            activity_score = excluded.activity_score,
            compliance_penalty = excluded.compliance_penalty,
            updated_at = excluded.updated_at
        """
    ).bindparams(bindparam("now", type_=DateTime(timezone=True)))
    await session.execute(
        stmt,
        {
            "agent_id": agent_id,
            "chain_id": int(chain_id),
            "token_id": token_id,
            "registry": registry,
            "creator": creator_address,
            "owner": owner_address,
            "activity_score": activity_score,
            "compliance_penalty": compliance_penalty,
            "now": now,
        },
    )

    if creator_flagged or owner_flagged:
        creator_is_owner = (
            bool(creator_address)
            and bool(owner_address)
            and creator_address.strip().lower() == owner_address.strip().lower()
        )
        # Idempotent: replace any existing flag row for this agent so the helper
        # can be called twice on the same agent without IntegrityError.
        flag_stmt = text(
            """
            INSERT INTO agent_compliance_flags (
                agent_id,
                creator_flagged,
                creator_flag_sources,
                owner_flagged,
                owner_flag_sources,
                creator_is_owner,
                flagged_data_stale,
                refreshed_at
            ) VALUES (
                :agent_id,
                :creator_flagged,
                :creator_sources,
                :owner_flagged,
                :owner_sources,
                :creator_is_owner,
                0,
                :now
            )
            ON CONFLICT(agent_id) DO UPDATE SET
                creator_flagged = excluded.creator_flagged,
                creator_flag_sources = excluded.creator_flag_sources,
                owner_flagged = excluded.owner_flagged,
                owner_flag_sources = excluded.owner_flag_sources,
                creator_is_owner = excluded.creator_is_owner,
                refreshed_at = excluded.refreshed_at
            """
        ).bindparams(bindparam("now", type_=DateTime(timezone=True)))
        await session.execute(
            flag_stmt,
            {
                "agent_id": agent_id,
                "creator_flagged": 1 if creator_flagged else 0,
                "creator_sources": '["ofac-bsc"]' if creator_flagged else "[]",
                "owner_flagged": 1 if owner_flagged else 0,
                "owner_sources": '["ofac-bsc"]' if owner_flagged else "[]",
                "creator_is_owner": 1 if creator_is_owner else 0,
                "now": now,
            },
        )

    await session.commit()
    return agent_id
