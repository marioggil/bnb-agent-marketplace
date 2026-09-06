"""Compliance refresh service (OFAC penalty signal, agent-compliance domain).

Spec: `openspec/changes/compliance-flags/spec.md`. Phase 1a-i ships only the
skeleton: module constants, the `ComplianceRefreshReport` dataclass, and the
pure `compute_penalty` helper. **No DB-touching functions** in this slice —
those arrive in 1a-ii (the orchestrator + admin router).

The helper is the public surface both 1a-ii (writes `compliance_penalty` on
every refresh) and 1b (renders `displayed_activity_score`) call. Keeping it
pure and `Decimal`-typed preserves the boundary against the `Numeric(5, 2)`
column at every call site — see design §5.4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as _pg_insert

from app.db.models.agent import AgentCache
from app.db.models.agent_compliance import AgentComplianceFlag
from app.db.models.flagged_address import FlaggedAddress

_PENALTY_PER_FLAG: int = 30
_PENALTY_CAP: Decimal = Decimal("50")
_STALE_THRESHOLD_HOURS: int = 24


@dataclass(slots=True)
class ComplianceRefreshReport:
    """Outcome of one `run_compliance_refresh()` invocation.

    Defined here (1a-i) so 1a-ii can extend the module without re-exporting
    the type — the orchestrator returns this and the admin endpoint serializes
    `as_dict()` straight to JSON.
    """

    sources_total_fetched: int = 0
    sources_total_inserted: int = 0
    agents_matched: int = 0
    agents_upserted: int = 0
    penalty_distribution: dict[str, int] = field(default_factory=dict)
    # ISO-8601 UTC; None when the mirror is empty (last_refreshed_at fallback).
    last_refreshed_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """JSON-friendly shape for the admin endpoint response."""
        return {
            "sources_total_fetched": self.sources_total_fetched,
            "sources_total_inserted": self.sources_total_inserted,
            "agents_matched": self.agents_matched,
            "agents_upserted": self.agents_upserted,
            "penalty_distribution": dict(self.penalty_distribution),
            "last_refreshed_at": self.last_refreshed_at,
        }


def compute_penalty(creator_flagged: bool, owner_flagged: bool) -> Decimal:
    """Pure: 30 points per flag, capped at 50. Returns `Decimal`, never `float`.

    Spec AC-4 / design §5.1 truth table:

        (False, False) -> Decimal("0.00")
        (True,  False) -> Decimal("30.00")
        (False, True ) -> Decimal("30.00")
        (True,  True ) -> Decimal("50.00")  # cap, not 60.00

    `Decimal` matches the `Numeric(5, 2)` column type so the read-path math
    (`displayed_activity_score = max(0, activity_score - compliance_penalty)`)
    never touches IEEE-754 (design §5.4).
    """
    raw = _PENALTY_PER_FLAG * int(creator_flagged) + _PENALTY_PER_FLAG * int(owner_flagged)
    return min(Decimal(raw), _PENALTY_CAP)


async def flagged_data_stale(session, *, threshold_hours: int = _STALE_THRESHOLD_HOURS) -> bool:
    from datetime import datetime, timezone
    max_ts = await session.scalar(select(func.max(FlaggedAddress.updated_at)))
    if max_ts is None:
        return True
    return (datetime.now(timezone.utc) - max_ts).total_seconds() > threshold_hours * 3600


async def refresh_agent_compliance_flags(session) -> ComplianceRefreshReport:
    """Compute per-agent compliance flags + penalty; UPSERT into the two stores."""
    from datetime import datetime, timezone

    # 1. Read flagged_addresses once into address_sources.
    rows = (await session.execute(
        select(func.lower(FlaggedAddress.address).label("addr"), FlaggedAddress.source)
    )).all()
    address_sources: dict[str, list[str]] = {}
    for addr, src in rows:
        address_sources.setdefault(addr, []).append(src)

    # 2. Read max(flagged_addresses.updated_at) for last_refreshed_at (tz-aware).
    max_updated_at = await session.scalar(select(func.max(FlaggedAddress.updated_at)))
    if max_updated_at is not None and max_updated_at.tzinfo is None:
        max_updated_at = max_updated_at.replace(tzinfo=timezone.utc)
    last_refreshed_at = max_updated_at.isoformat() if max_updated_at is not None else None

    # 3. Compute stale flag (used in every row).
    stale = await flagged_data_stale(session)

    # 4. Iterate every AgentCache row.
    agents = (await session.scalars(select(AgentCache))).all()

    distribution: dict[str, int] = {"0": 0, "30": 0, "50": 0}
    upserts: list[dict] = []
    penalty_rows: list[tuple[str, Decimal]] = []
    now = datetime.now(timezone.utc)

    for agent in agents:
        creator = (agent.creator_address or "").strip().lower()
        owner = (agent.owner_address or "").strip().lower()
        creator_flagged = bool(creator) and creator in address_sources
        owner_flagged = bool(owner) and owner in address_sources
        creator_is_owner = bool(creator) and creator == owner
        penalty = compute_penalty(creator_flagged, owner_flagged)
        bucket = str(int(penalty))
        distribution[bucket] = distribution.get(bucket, 0) + 1
        upserts.append({
            "agent_id": agent.agent_id,
            "creator_flagged": creator_flagged,
            "creator_flag_sources": address_sources.get(creator, []),
            "owner_flagged": owner_flagged,
            "owner_flag_sources": address_sources.get(owner, []),
            "creator_is_owner": creator_is_owner,
            "flagged_data_stale": stale,
            "refreshed_at": now,
        })
        penalty_rows.append((agent.agent_id, penalty))

    # 5. UPSERT (per-row delete-then-insert for sqlite; pg ON CONFLICT for postgres).
    if upserts:
        if session.bind is not None and getattr(session.bind.dialect, "name", "") == "postgresql":
            stmt = _pg_insert(AgentComplianceFlag).values(upserts)
            update_cols = {
                "creator_flagged": stmt.excluded.creator_flagged,
                "creator_flag_sources": stmt.excluded.creator_flag_sources,
                "owner_flagged": stmt.excluded.owner_flagged,
                "owner_flag_sources": stmt.excluded.owner_flag_sources,
                "creator_is_owner": stmt.excluded.creator_is_owner,
                "flagged_data_stale": stmt.excluded.flagged_data_stale,
                "refreshed_at": stmt.excluded.refreshed_at,
            }
            stmt = stmt.on_conflict_do_update(
                index_elements=[AgentComplianceFlag.agent_id], set_=update_cols
            )
            await session.execute(stmt)
        else:
            for row in upserts:
                aid = row["agent_id"]
                await session.execute(
                    delete(AgentComplianceFlag).where(AgentComplianceFlag.agent_id == aid)
                )
                await session.execute(_pg_insert(AgentComplianceFlag).values(**row))

    # 6. Bulk UPDATE agent_cache.compliance_penalty per agent.
    for aid, penalty in penalty_rows:
        await session.execute(
            AgentCache.__table__.update()
            .where(AgentCache.agent_id == aid)
            .values(compliance_penalty=penalty)
        )

    await session.commit()

    return ComplianceRefreshReport(
        agents_matched=len(penalty_rows),
        agents_upserted=len(upserts),
        penalty_distribution=distribution,
        last_refreshed_at=last_refreshed_at,
    )


async def status_summary(session) -> tuple:
    """Return `(max(flagged_addresses.updated_at), count(agent_compliance_flags))`."""
    from datetime import timezone
    last_ts = await session.scalar(select(func.max(FlaggedAddress.updated_at)))
    if last_ts is not None and last_ts.tzinfo is None:
        last_ts = last_ts.replace(tzinfo=timezone.utc)
    row_count = await session.scalar(select(func.count()).select_from(AgentComplianceFlag))
    return last_ts, int(row_count or 0)


async def run_compliance_refresh(session) -> ComplianceRefreshReport:
    """Orchestrator: mirror phase first, agent-flag phase second."""
    from app.services.flagged_sync import refresh_flagged_addresses

    mirror_report = await refresh_flagged_addresses()
    flag_report = await refresh_agent_compliance_flags(session)

    return ComplianceRefreshReport(
        sources_total_fetched=mirror_report.total_fetched,
        sources_total_inserted=mirror_report.total_inserted,
        agents_matched=flag_report.agents_matched,
        agents_upserted=flag_report.agents_upserted,
        penalty_distribution=flag_report.penalty_distribution,
        last_refreshed_at=flag_report.last_refreshed_at,
    )


__all__ = [
    "ComplianceRefreshReport",
    "compute_penalty",
    "flagged_data_stale",
    "refresh_agent_compliance_flags",
    "run_compliance_refresh",
    "status_summary",
]
