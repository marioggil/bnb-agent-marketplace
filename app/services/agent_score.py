"""Composite agent activity score: probe pillar + track-record pillar.

Spec: `sdd/agent-score/spec` agent-activity-scoring S1-S4.
Design: D6 (probe pillar 0.5/0.3/0.1/0.1 weights), D7 (track pillar neutral
baselines + renormalization), D8 (composite renormalization).

The pure functions here are deterministic and DB-free; `fetch_track_record`
and `materialize_score` are the only I/O-bound entry points and run against
`agent_cache` + `onchain_agent_events` (both already migrated). Slice 2 wires
the probe worker through this module; slice 3 exposes it via /score.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.agent import AgentCache
from app.db.models.onchain_index import OnchainAgentEvent

#: Zero-address counts as "no buyer" in the unique_buyers aggregation (S1).
ZERO_ADDRESS: str = "0x" + "0" * 40

#: Track-record window (S1): events older than this are ignored.
TRACK_WINDOW_DAYS: int = 90

#: Probe pillar weights (D6): 0.5 responded + 0.3 latency + 0.1 presence + 0.1 skills.
_PROBE_WEIGHTS: dict[str, float] = {
    "responded": 0.5,
    "latency": 0.3,
    "presence": 0.1,
    "skills": 0.1,
}

#: Track pillar weights (D7): equal weight per dimension.
_TRACK_WEIGHTS: dict[str, float] = {
    "age": 0.25,
    "events": 0.25,
    "buyers": 0.25,
    "recency": 0.25,
}


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def latency_band_points(latency_ms: int | None) -> int:
    """Map latency to 0-100 points per S4.

    ≤300→100, ≤1000→75, ≤3000→50, ≤10000→25, >10000→0. `None` (unknown)
    maps to 0 per D6.
    """
    if latency_ms is None:
        return 0
    if latency_ms <= 300:
        return 100
    if latency_ms <= 1000:
        return 75
    if latency_ms <= 3000:
        return 50
    if latency_ms <= 10000:
        return 25
    return 0


def _probe_parts(
    responded: bool | None,
    latency_ms: int | None,
    presence: str | None,
    skills_count: int | None,
) -> dict[str, int] | None:
    """Per-dimension probe scores (D6), or None when never probed."""
    if responded is None:
        return None
    return {
        "responded": 100 if responded else 0,
        "latency": latency_band_points(latency_ms),
        "presence": 100 if presence == "online" else 50,
        "skills": min(skills_count or 0, 10) * 10,
    }


def _track_parts(
    age_months: float | None,
    event_count: int | None,
    unique_buyers: int | None,
    recency_days: int | None,
) -> dict[str, int]:
    """Per-dimension track scores (D7), neutral 50 when absent.

    S3: an agent with no events in the window has no track record, so every
    dimension defaults to neutral 50.
    """
    if event_count in (None, 0):
        return {"age": 50, "events": 50, "buyers": 50, "recency": 50}
    return {
        "age": round(50 if age_months is None else min(age_months / 12.0, 1.0) * 100.0),
        "events": round(min(event_count / 10.0, 1.0) * 100.0),
        "buyers": round(50 if unique_buyers is None else min(unique_buyers / 10.0, 1.0) * 100.0),
        "recency": round(50 if recency_days is None else min(30.0 / recency_days, 1.0) * 100.0),
    }


def compute_probe_pillar(
    responded: bool | None,
    latency_ms: int | None,
    presence: str | None,
    skills_count: int | None,
) -> int | None:
    """Probe pillar 0-100 (D6), or None when the agent was never probed.

    `round(0.5×responded_base + 0.3×latency_points + 0.1×presence_points
    + 0.1×skills_points)`; responded_base is 100/0; latency bands per S4;
    presence online→100 else 50; skills min(n,10)×10.
    """
    parts = _probe_parts(responded, latency_ms, presence, skills_count)
    if parts is None:
        return None
    return round(
        _PROBE_WEIGHTS["responded"] * parts["responded"]
        + _PROBE_WEIGHTS["latency"] * parts["latency"]
        + _PROBE_WEIGHTS["presence"] * parts["presence"]
        + _PROBE_WEIGHTS["skills"] * parts["skills"]
    )


def compute_track_record_pillar(
    age_months: float | None,
    event_count: int | None,
    unique_buyers: int | None,
    recency_days: int | None,
) -> int:
    """Track-record pillar 0-100 (D7).

    Neutral baselines: 6 months of age → 50; zero events → 50; absent
    signals → 50. Present signals renormalize with equal weights.
    """
    parts = _track_parts(age_months, event_count, unique_buyers, recency_days)
    return round(sum(parts.values()) / len(parts))


def composite_score(probe: int | None, track: int | None) -> int:
    """Composite activity score 0-100 per S3/D8 with sparse renormalization.

    Both pillars → 0.60×probe + 0.40×track; probe-only → probe; track-only
    → track; neither → neutral 50.
    """
    if probe is not None and track is not None:
        return round(0.60 * probe + 0.40 * track)
    if probe is not None:
        return probe
    if track is not None:
        return track
    return 50


def build_breakdown(
    *,
    responded: bool | None = None,
    latency_ms: int | None = None,
    presence: str | None = None,
    skills_count: int | None = None,
    age_months: float | None = None,
    event_count: int | None = None,
    unique_buyers: int | None = None,
    recency_days: int | None = None,
) -> list[dict[str, Any]]:
    """Flat breakdown list `[{dimension, score, weight}]` for display (A1).

    Probe dimensions (D6 weights 0.5/0.3/0.1/0.1) appear only when the agent
    has been probed; track dimensions (D7 equal weights) always appear,
    defaulting to neutral 50 when the agent has no track record.
    """
    out: list[dict[str, Any]] = []
    probe = _probe_parts(responded, latency_ms, presence, skills_count)
    if probe is not None:
        for dim in ("responded", "latency", "presence", "skills"):
            out.append({"dimension": dim, "score": probe[dim], "weight": _PROBE_WEIGHTS[dim]})
    track = _track_parts(age_months, event_count, unique_buyers, recency_days)
    for dim in ("age", "events", "buyers", "recency"):
        out.append({"dimension": dim, "score": track[dim], "weight": _TRACK_WEIGHTS[dim]})
    return out


# ---------------------------------------------------------------------------
# Track-record SQL + materialization (S1, S2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TrackRecord:
    """90-day on-chain track record of one agent (S1)."""

    age_months: float | None
    event_count: int
    unique_buyers: int
    recency_days: int | None


async def fetch_track_record(session: AsyncSession, agent_id: str) -> TrackRecord:
    """Aggregate the agent's 90-day on-chain activity (S1).

    - `event_count` = COUNT(DISTINCT tx_hash) over the 90-day window (the
      table has no unique tx constraint, so duplicates must be collapsed)
    - `unique_buyers` = COUNT(DISTINCT to_address) FILTER (from ≠ zero)
    - `recency_days` from MAX(timestamp); `age_months` from
      `agent_cache.upstream_created_at` (NOT the events)
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=TRACK_WINDOW_DAYS)

    created_at = await session.scalar(
        select(AgentCache.upstream_created_at).where(AgentCache.agent_id == agent_id)
    )
    age_months: float | None = None
    if created_at is not None:
        age_months = max(0.0, (now - created_at).total_seconds() / (30.44 * 24 * 3600))

    event_count, unique_buyers, latest = (
        await session.execute(
            select(
                func.count(func.distinct(OnchainAgentEvent.tx_hash)),
                func.count(func.distinct(OnchainAgentEvent.to_address)).filter(
                    OnchainAgentEvent.from_address != ZERO_ADDRESS
                ),
                func.max(OnchainAgentEvent.timestamp),
            ).where(
                OnchainAgentEvent.agent_id == agent_id,
                OnchainAgentEvent.timestamp >= cutoff,
            )
        )
    ).one()

    recency_days: int | None = None
    if latest is not None:
        recency_days = max(0, (now - latest).days)

    return TrackRecord(
        age_months=age_months,
        event_count=int(event_count or 0),
        unique_buyers=int(unique_buyers or 0),
        recency_days=recency_days,
    )


async def materialize_score(session: AsyncSession, agent_id: str, score: int | Decimal) -> None:
    """Write the composite activity score into `agent_cache` (S2)."""
    await session.execute(
        update(AgentCache)
        .where(AgentCache.agent_id == agent_id)
        .values(activity_score=Decimal(str(score)))
    )


__all__ = [
    "TRACK_WINDOW_DAYS",
    "TrackRecord",
    "ZERO_ADDRESS",
    "build_breakdown",
    "composite_score",
    "compute_probe_pillar",
    "compute_track_record_pillar",
    "fetch_track_record",
    "latency_band_points",
    "materialize_score",
]
