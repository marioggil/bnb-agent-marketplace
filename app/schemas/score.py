"""Pydantic schemas for the score & compare API (agent-score A1/A2).

Design contract (`sdd/agent-score/design` interfaces):

- `ProbePillar` — the latest A2A probe snapshot (D2/D9): composite pillar
  `score` plus the raw signals (`responded`, `latency_ms`, `status`,
  `presence`, `skills_count`, `probed_at`). `None` when never probed.
- `TrackRecordPillar` — 90-day on-chain activity (S1): `score` plus
  `age_months`, `event_count`, `unique_buyers`, `recency_days`.
- `Pillars` — the `pillars:{probe, track_record}` envelope from spec A1/A2.
- `ScoreOut` — `GET /api/agents/{chain}/{token}/score` response.
- `CompareAgentOut` / `CompareOut` — `GET /api/agents/compare` response.

Serialized JSON matches the spec contract exactly; `Pillars` is a typed
model instead of a bare `dict[str, Any]` so the shape cannot drift.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


class ProbePillar(BaseModel):
    """Latest A2A probe of one agent (spec A1 probe pillar)."""

    score: int | None = None
    responded: bool | None = None
    latency_ms: int | None = None
    status: str | None = None
    presence: str | None = None
    skills_count: int | None = None
    probed_at: datetime | None = None


class TrackRecordPillar(BaseModel):
    """90-day on-chain track record of one agent (spec A1 track pillar)."""

    score: int
    age_months: float | None = None
    event_count: int | None = None
    unique_buyers: int | None = None
    recency_days: int | None = None


class Pillars(BaseModel):
    """The `pillars` envelope shared by /score and /compare (spec A1/A2)."""

    probe: ProbePillar | None = None
    track_record: TrackRecordPillar


class ScoreOut(BaseModel):
    """GET /api/agents/{chain}/{token}/score response (spec A1)."""

    chain: int
    token: int
    activity_score: Decimal
    pillars: Pillars
    breakdown: list[dict[str, Any]]  # [{dimension, score, weight}]


class CompareAgentOut(BaseModel):
    """One agent row inside the /compare response (spec A2)."""

    chain: int
    token: int
    name: str | None = None
    activity_score: Decimal | None = None
    pillars: Pillars


class CompareOut(BaseModel):
    """GET /api/agents/compare response (spec A2)."""

    agents: list[CompareAgentOut]


__all__ = [
    "CompareAgentOut",
    "CompareOut",
    "Pillars",
    "ProbePillar",
    "ScoreOut",
    "TrackRecordPillar",
]