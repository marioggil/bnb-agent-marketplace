"""Compliance refresh service tests (Phase 1a-ii).

Spec: `openspec/changes/compliance-flags/spec.md` AC-3, AC-7, AC-8 + design §3.1,
§3.4. Tests cover:

- `refresh_agent_compliance_flags(session)` writes the per-agent UPSERT, mirrors
  the OFAC flag set, and writes `agent_cache.compliance_penalty` correctly.
- Idempotence: a second refresh on stable state yields byte-equivalent JSON
  payloads (DB-agnostic via `json.dumps(..., sort_keys=True)`).
- `flagged_data_stale(session)` honours the 24h threshold (fresh / backdated /
  empty mirror).
- `run_compliance_refresh()` chains `refresh_flagged_addresses()` first and
  `refresh_agent_compliance_flags(session)` second, short-circuits on mirror
  failure, and returns a combined `ComplianceRefreshReport`.
- Case-insensitive match against `flagged_addresses.address`; one-char difference
  does not promote to a match (no prefix / suffix / substring heuristic).

The `_ensure_compliance_schema` autouse fixture in `tests/conftest.py` pre-seeds
both the `agent_compliance_flags` table and the `agent_cache.compliance_penalty`
column on sqlite (mirrors migration `0012_compliance_penalty.py`).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import bindparam, delete, select, text
from sqlalchemy.dialects.postgresql import JSONB as _PG_JSONB
from sqlalchemy.schema import DefaultClause
from sqlalchemy.types import DateTime, JSON as _SQLITE_JSON

from app.db.models.agent import (
    BSC_CHAIN_ID,
    BSC_IDENTITY_REGISTRY,
    AgentCache,
    build_agent_id,
)
from app.db.models.agent_compliance import AgentComplianceFlag
from app.db.models.flagged_address import FlaggedAddress
from app.services import compliance_refresh

# ---------------------------------------------------------------------------
# SQLite patch — mirror the conftest's JSONB → JSON swap for the compliance
# table. The conftest's `_patch_metadata_for_sqlite` runs at conftest-import
# time, before this module's `AgentComplianceFlag` import, so the columns
# stay JSONB on `Base.metadata`. `Base.metadata.create_all` on sqlite would
# fail on JSONB — re-run the same JSONB → JSON + DefaultClause rewrite here.
# ---------------------------------------------------------------------------


def _patch_compliance_table_for_sqlite() -> None:
    for col in AgentComplianceFlag.__table__.columns:
        if isinstance(col.type, _PG_JSONB):
            col.type = _SQLITE_JSON()
            sd = col.server_default
            if sd is not None and hasattr(sd, "arg"):
                raw = getattr(sd.arg, "text", None)
                if raw and "::jsonb" in raw:
                    literal = raw.replace("::jsonb", "").strip().strip("'")
                    col.server_default = DefaultClause(literal)


_patch_compliance_table_for_sqlite()


# ---------------------------------------------------------------------------
# Pre-seed helpers — small, DB-backed, scoped to this file (per design §3.5).
# ---------------------------------------------------------------------------


async def _seed_flagged_addresses(db, addresses: list[tuple[str, str]]) -> None:
    """Insert `[(address, source), ...]` rows into `flagged_addresses`.

    `updated_at` is set explicitly to `datetime.now(UTC)` so the row counts as
    fresh (the conftest's sqlite-patch rewrites the server default to the
    literal `"2026-01-01T00:00:00"`, which would otherwise make every row
    stale on the date of the test run). Tests that need to backdate call this
    helper and then `UPDATE updated_at` via `_backdate_flagged_addresses`.
    """
    now = datetime.now(timezone.utc)
    db.add_all(
        FlaggedAddress(
            address=addr,
            source=src,
            created_at=now,
            updated_at=now,
        )
        for addr, src in addresses
    )
    await db.commit()


async def _backdate_flagged_addresses(db, hours: int) -> None:
    """Backdate every `flagged_addresses.updated_at` by `hours` ago.

    Uses a typed bind parameter (`DateTime(timezone=True)`) so SQLAlchemy
    serializes the datetime to a sqlite-friendly string before it reaches the
    sqlite3 driver — bypasses Python 3.12's deprecated default datetime
    adapter (filterwarnings=error treats it as a test failure).
    """
    ts = datetime.now(timezone.utc) - timedelta(hours=hours)
    stmt = text("UPDATE flagged_addresses SET updated_at = :ts").bindparams(
        bindparam("ts", type_=DateTime(timezone=True))
    )
    await db.execute(stmt, {"ts": ts})
    await db.commit()


async def _seed_agent_with_addresses(
    db,
    *,
    token_id: int,
    creator_address: str | None = None,
    owner_address: str | None = None,
) -> str:
    """Insert one `agent_cache` row keyed by token_id with the given addresses.

    Returns the canonical `agent_id`. Mirrors the seed helpers in `test_pages.py`
    / `test_pages_x402.py` / `test_flagged.py` — kept local because the
    compliance tests need `creator_address` shaped differently.
    """
    aid = build_agent_id(BSC_CHAIN_ID, BSC_IDENTITY_REGISTRY, token_id)
    db.add(
        AgentCache(
            agent_id=aid,
            chain_id=BSC_CHAIN_ID,
            token_id=token_id,
            registry_address=BSC_IDENTITY_REGISTRY,
            name=f"Agent {token_id}",
            creator_address=creator_address,
            owner_address=owner_address,
            supported_protocols=[],
            cross_chain_versions=[],
            raw={},
            tags=[],
            categories=[],
        )
    )
    await db.commit()
    return aid


async def _read_flag_row(db, agent_id: str) -> AgentComplianceFlag | None:
    return await db.scalar(
        select(AgentComplianceFlag).where(AgentComplianceFlag.agent_id == agent_id)
    )


async def _read_penalty(db, agent_id: str) -> Decimal | None:
    from app.db.models.agent import AgentCache as _AC

    return await db.scalar(select(_AC.compliance_penalty).where(_AC.agent_id == agent_id))


# ---------------------------------------------------------------------------
# T1 — refresh_agent_compliance_flags: creator match writes compliance_penalty
# ---------------------------------------------------------------------------


async def test_refresh_writes_compliance_penalty_for_creator_match(db):
    """AC-3 + design §3.4: seed creator in mirror → row written with the right
    flag booleans, source list, and `compliance_penalty = Decimal('30.00')`."""
    creator = "0x" + "ab" * 20
    owner = "0x" + "de" * 20
    await _seed_flagged_addresses(db, [(creator, "ofac-bsc")])
    aid = await _seed_agent_with_addresses(
        db, token_id=1, creator_address=creator, owner_address=owner
    )

    report = await compliance_refresh.refresh_agent_compliance_flags(db)

    assert report.agents_matched == 1
    flag_row = await _read_flag_row(db, aid)
    assert flag_row is not None
    assert flag_row.creator_flagged is True
    assert flag_row.owner_flagged is False
    assert flag_row.creator_is_owner is False
    assert flag_row.creator_flag_sources == ["ofac-bsc"]
    assert flag_row.owner_flag_sources == []
    penalty = await _read_penalty(db, aid)
    assert penalty == Decimal("30.00")


# ---------------------------------------------------------------------------
# T2 supplement — owner match + clean agent + creator_is_owner (full coverage
# of `compute_penalty`'s truth table from the DB angle). These run GREEN once
# `refresh_agent_compliance_flags` is implemented.
# ---------------------------------------------------------------------------


async def test_refresh_writes_flagged_row_for_owner_match(db):
    owner = "0x" + "de" * 20
    await _seed_flagged_addresses(db, [(owner, "ofac-eth")])
    aid = await _seed_agent_with_addresses(
        db, token_id=2, creator_address="0x" + "aa" * 20, owner_address=owner
    )

    await compliance_refresh.refresh_agent_compliance_flags(db)

    flag_row = await _read_flag_row(db, aid)
    assert flag_row is not None
    assert flag_row.creator_flagged is False
    assert flag_row.owner_flagged is True
    assert flag_row.owner_flag_sources == ["ofac-eth"]
    assert flag_row.creator_flag_sources == []
    penalty = await _read_penalty(db, aid)
    assert penalty == Decimal("30.00")


async def test_refresh_creator_is_owner_derivation(db):
    """Both flags true → cap. `creator_is_owner = True` when both addresses match."""
    same = "0x" + "ab" * 20
    await _seed_flagged_addresses(db, [(same, "ofac-bsc")])
    aid = await _seed_agent_with_addresses(
        db, token_id=3, creator_address=same, owner_address=same
    )

    await compliance_refresh.refresh_agent_compliance_flags(db)

    flag_row = await _read_flag_row(db, aid)
    assert flag_row.creator_flagged is True
    assert flag_row.owner_flagged is True
    assert flag_row.creator_is_owner is True
    penalty = await _read_penalty(db, aid)
    assert penalty == Decimal("50.00")  # cap, not 60.00


async def test_refresh_clean_agent_writes_zero_penalty(db):
    aid = await _seed_agent_with_addresses(
        db,
        token_id=4,
        creator_address="0x" + "aa" * 20,
        owner_address="0x" + "bb" * 20,
    )

    await compliance_refresh.refresh_agent_compliance_flags(db)

    flag_row = await _read_flag_row(db, aid)
    assert flag_row.creator_flagged is False
    assert flag_row.owner_flagged is False
    assert flag_row.creator_is_owner is False
    assert flag_row.creator_flag_sources == []
    assert flag_row.owner_flag_sources == []
    penalty = await _read_penalty(db, aid)
    assert penalty == Decimal("0.00")


async def test_refresh_case_insensitive_match(db):
    """AC-7: mirror stored lowercase, agent `creator_address` mixed-case → match."""
    lowercase = "0x" + "ab" * 20
    mixed = "0x" + "AB" * 20
    await _seed_flagged_addresses(db, [(lowercase, "ofac-bsc")])
    aid = await _seed_agent_with_addresses(
        db, token_id=5, creator_address=mixed, owner_address="0x" + "cd" * 20
    )

    await compliance_refresh.refresh_agent_compliance_flags(db)

    flag_row = await _read_flag_row(db, aid)
    assert flag_row.creator_flagged is True
    # The mirror row is NOT mutated (design §5.7).
    mirror_row = await db.scalar(
        select(FlaggedAddress).where(FlaggedAddress.address == lowercase)
    )
    assert mirror_row is not None
    assert mirror_row.address == lowercase  # stays lowercase as fetched


async def test_refresh_one_char_difference_does_not_match(db):
    """AC-7 + §5.7: no prefix / suffix / substring promotion."""
    a = "0x" + "ab" * 20
    b = "0x" + "ab" * 19 + "cd"  # last byte differs
    await _seed_flagged_addresses(db, [(a, "ofac-bsc")])
    aid = await _seed_agent_with_addresses(
        db, token_id=6, creator_address=b, owner_address="0x" + "ee" * 20
    )

    await compliance_refresh.refresh_agent_compliance_flags(db)

    flag_row = await _read_flag_row(db, aid)
    assert flag_row.creator_flagged is False
    assert flag_row.owner_flagged is False
    penalty = await _read_penalty(db, aid)
    assert penalty == Decimal("0.00")


# ---------------------------------------------------------------------------
# T3 — idempotence: second refresh on stable fixture yields byte-equivalent
# JSON payloads (design §3.4, §5.3). DB-agnostic via json.dumps(sort_keys=True).
# ---------------------------------------------------------------------------


async def test_refresh_idempotent_second_run_yields_same_payload(db):
    creator = "0x" + "ab" * 20
    owner = "0x" + "de" * 20
    await _seed_flagged_addresses(db, [(creator, "ofac-bsc")])
    aid = await _seed_agent_with_addresses(
        db, token_id=7, creator_address=creator, owner_address=owner
    )

    # First run.
    await compliance_refresh.refresh_agent_compliance_flags(db)
    before = await _read_flag_row(db, aid)
    penalty_before = await _read_penalty(db, aid)

    # Second run on identical state.
    await compliance_refresh.refresh_agent_compliance_flags(db)
    after = await _read_flag_row(db, aid)
    penalty_after = await _read_penalty(db, aid)

    # JSONB source-list byte-equivalence (dialect-agnostic).
    assert json.dumps(before.creator_flag_sources, sort_keys=True) == json.dumps(
        after.creator_flag_sources, sort_keys=True
    )
    assert json.dumps(before.owner_flag_sources, sort_keys=True) == json.dumps(
        after.owner_flag_sources, sort_keys=True
    )
    # Penalty change-count: identical Decimals.
    assert penalty_before == penalty_after
    # Bool flags identical.
    assert before.creator_flagged == after.creator_flagged
    assert before.owner_flagged == after.owner_flagged
    assert before.creator_is_owner == after.creator_is_owner


# ---------------------------------------------------------------------------
# T4 + T5 — flagged_data_stale: threshold_hours = 24 default
# ---------------------------------------------------------------------------


async def test_flagged_data_stale_empty_mirror_returns_true(db):
    """AC-8: empty mirror counts as stale."""
    assert await compliance_refresh.flagged_data_stale(db) is True


async def test_flagged_data_stale_fresh_mirror_returns_false(db):
    await _seed_flagged_addresses(db, [("0x" + "ab" * 20, "ofac-bsc")])

    assert await compliance_refresh.flagged_data_stale(db) is False


async def test_flagged_data_stale_true_after_24h(db):
    await _seed_flagged_addresses(db, [("0x" + "ab" * 20, "ofac-bsc")])
    await _backdate_flagged_addresses(db, hours=25)

    assert await compliance_refresh.flagged_data_stale(db) is True


async def test_flagged_data_stale_threshold_is_respected(db):
    """Custom `threshold_hours` argument: 1h threshold + 2h backdate → stale."""
    await _seed_flagged_addresses(db, [("0x" + "ab" * 20, "ofac-bsc")])
    await _backdate_flagged_addresses(db, hours=2)

    assert await compliance_refresh.flagged_data_stale(db, threshold_hours=1) is True
    # And the default 24h threshold still returns False on the same data.
    assert await compliance_refresh.flagged_data_stale(db, threshold_hours=72) is False


# ---------------------------------------------------------------------------
# T6 + T7 — run_compliance_refresh: chains both phases, mirror failure
# short-circuits, combined report.
# ---------------------------------------------------------------------------


async def test_run_compliance_refresh_chains_both_phases(db, respx_mock):
    """Mirror invoked first, agent flags second, each exactly once."""
    from app.services import flagged_sync

    bsc_url = flagged_sync.FLAGGED_SOURCES["ofac-bsc"]
    eth_url = flagged_sync.FLAGGED_SOURCES["ofac-eth"]
    respx_mock.get(bsc_url).respond(200, json=["0x" + "ab" * 20])
    respx_mock.get(eth_url).respond(200, json=[])
    creator = "0x" + "ab" * 20
    await _seed_agent_with_addresses(
        db, token_id=10, creator_address=creator, owner_address="0x" + "cd" * 20
    )

    mirror_calls = []
    flag_calls = []

    real_refresh_mirror = flagged_sync.refresh_flagged_addresses
    real_refresh_flags = compliance_refresh.refresh_agent_compliance_flags

    async def wrap_mirror(*args, **kwargs):
        mirror_calls.append((args, kwargs))
        return await real_refresh_mirror(*args, **kwargs)

    async def wrap_flags(session, *args, **kwargs):
        flag_calls.append((args, kwargs))
        return await real_refresh_flags(session, *args, **kwargs)

    with patch.object(
        flagged_sync, "refresh_flagged_addresses", side_effect=wrap_mirror
    ), patch.object(
        compliance_refresh,
        "refresh_agent_compliance_flags",
        side_effect=wrap_flags,
    ):
        report = await compliance_refresh.run_compliance_refresh(db)

    assert len(mirror_calls) == 1
    assert len(flag_calls) == 1
    # Combined report carries both phases' counts.
    assert report.sources_total_fetched >= 1
    assert report.agents_matched == 1
    assert report.last_refreshed_at is not None


async def test_run_compliance_refresh_mirror_failure_short_circuits(db, respx_mock):
    """AC-3: mirror phase raises → agent-flag phase NOT invoked, error propagates."""
    import httpx

    from app.services import flagged_sync

    bsc_url = flagged_sync.FLAGGED_SOURCES["ofac-bsc"]
    eth_url = flagged_sync.FLAGGED_SOURCES["ofac-eth"]
    respx_mock.get(bsc_url).respond(500)
    respx_mock.get(eth_url).respond(200, json=[])

    with patch.object(
        compliance_refresh,
        "refresh_agent_compliance_flags",
        AsyncMock(side_effect=AssertionError("must not be called")),
    ):
        with pytest.raises(httpx.HTTPStatusError):
            await compliance_refresh.run_compliance_refresh(db)

    # DB state unchanged: no agent_compliance_flags rows.
    rows = (await db.scalars(select(AgentComplianceFlag))).all()
    assert rows == []


async def test_run_compliance_refresh_returns_combined_report(db, respx_mock):
    """Both phases succeed → combined report carries mirror totals + agent counts."""
    from app.services import flagged_sync

    bsc_url = flagged_sync.FLAGGED_SOURCES["ofac-bsc"]
    eth_url = flagged_sync.FLAGGED_SOURCES["ofac-eth"]
    respx_mock.get(bsc_url).respond(200, json=["0x" + "ab" * 20, "0x" + "cd" * 20])
    respx_mock.get(eth_url).respond(200, json=["0x" + "ee" * 20])
    await _seed_agent_with_addresses(
        db,
        token_id=11,
        creator_address="0x" + "ab" * 20,
        owner_address="0x" + "ff" * 20,
    )

    report = await compliance_refresh.run_compliance_refresh(db)

    assert report.sources_total_fetched == 3
    assert report.sources_total_inserted == 3
    assert report.agents_matched == 1
    assert report.agents_upserted == 1
    assert report.last_refreshed_at is not None


# ---------------------------------------------------------------------------
# status_summary — Phase 1a-ii's status endpoint helper
# ---------------------------------------------------------------------------


async def test_status_summary_empty_returns_zero_rows_and_none(db):
    last_ts, row_count = await compliance_refresh.status_summary(db)
    assert last_ts is None
    assert row_count == 0


async def test_status_summary_returns_max_updated_at_and_count(db):
    await _seed_flagged_addresses(
        db,
        [("0x" + "aa" * 20, "ofac-bsc"), ("0x" + "bb" * 20, "ofac-eth")],
    )
    await _seed_agent_with_addresses(
        db, token_id=20, creator_address="0x" + "cc" * 20, owner_address="0x" + "dd" * 20
    )
    await compliance_refresh.refresh_agent_compliance_flags(db)

    last_ts, row_count = await compliance_refresh.status_summary(db)

    assert row_count == 1
    assert last_ts is not None
    # `last_ts` is the max(updated_at) from flagged_addresses — a datetime.
    assert isinstance(last_ts, datetime)
