"""GET /api/agents — search, filter, sort, paginate the local AgentCache.

Spec: `sdd/marketplace-scaffold/spec/agents-cache` (#19) + spec
`web-pages` (#22) for the listing query string.

The endpoint NEVER calls 8004scan — the cache is the only read path
(id 11 gotcha: /search is broken on the upstream). All filters and the
sort key are applied to `AgentCache` directly.
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.agent import AgentCache
from app.db.models.agent_probe import AgentProbe
from app.db.session import get_db
from app.errors import NotFound, ValidationError
from app.schemas.agent import AgentOut
from app.schemas.pagination import Page
from app.schemas.score import (
    CompareAgentOut,
    CompareOut,
    Pillars,
    ProbePillar,
    ScoreOut,
    TrackRecordPillar,
)
from app.services import agent_score

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])


# Whitelisted sort keys — defends against SQL-injection via `?sort=`.
_SORT_KEYS: dict[str, Any] = {
    "average_score": AgentCache.average_score.desc().nullslast(),
    "total_feedbacks": AgentCache.total_feedbacks.desc(),
    "created_at": AgentCache.created_at.desc(),
    "name": AgentCache.name.asc().nullslast(),
    "activity_score": AgentCache.activity_score.desc().nullslast(),  # A3
}


@router.get("", response_model=Page[AgentOut])
async def list_agents(
    db: Annotated[AsyncSession, Depends(get_db)],
    q: str | None = Query(default=None, description="Substring/ILIKE on name + description."),
    category: Literal[
        "rebalancing",
        "grid_trading",
        "yield_optimisation",
        "health_factor_monitoring",
        "dev_automation",
        "creative_design",
        "marketing_content",
        "data_analytics",
        "security_compliance",
        "admin_ops",
        "other",
    ]
    | None = Query(default=None),
    x402: bool | None = Query(default=None, description="Filter by x402_supported flag."),
    sort: str = Query(default="average_score"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=24, ge=1, le=100),
) -> Page[AgentOut]:
    """List agents with filters/sort/pagination. JSON envelope."""
    stmt = select(AgentCache)
    count_stmt = select(func.count()).select_from(AgentCache)

    if q:
        like = f"%{q}%"
        cond = or_(AgentCache.name.ilike(like), AgentCache.description.ilike(like))
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)
    if category:
        stmt = stmt.where(AgentCache.category == category)
        count_stmt = count_stmt.where(AgentCache.category == category)
    if x402 is not None:
        stmt = stmt.where(AgentCache.x402_supported.is_(x402))
        count_stmt = count_stmt.where(AgentCache.x402_supported.is_(x402))

    order_by = _SORT_KEYS.get(sort)
    if order_by is None:
        raise ValidationError(f"unsupported sort key: {sort!r}")
    stmt = stmt.order_by(order_by).offset((page - 1) * page_size).limit(page_size)

    total = int(await db.scalar(count_stmt) or 0)
    rows = (await db.scalars(stmt)).all()
    return Page[AgentOut](
        items=[AgentOut.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{chain_id}/{token_id}", response_model=AgentOut)
async def get_agent(
    chain_id: int,
    token_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AgentOut:
    """Single agent by (chain_id, token_id). 404 if not cached."""
    row = await db.scalar(
        select(AgentCache).where(AgentCache.chain_id == chain_id, AgentCache.token_id == token_id)
    )
    if row is None:
        raise NotFound(f"agent {chain_id}:{token_id} not cached")
    return AgentOut.model_validate(row)


# ---------------------------------------------------------------------------
# Activity score read model (agent-score A1/A2) — shared by /score and /compare
# ---------------------------------------------------------------------------


async def latest_probe_for(session: AsyncSession, agent_id: str) -> AgentProbe | None:
    """The agent's most recent `agent_probes` row (or None when never probed)."""
    row = await session.scalar(
        select(AgentProbe)
        .where(AgentProbe.agent_id == agent_id)
        .order_by(AgentProbe.probed_at.desc())
        .limit(1)
    )
    return row if isinstance(row, AgentProbe) else None


def probe_pillar_from_row(probe: AgentProbe | None) -> ProbePillar | None:
    """Build the probe pillar from the latest probe row (D6), None when absent."""
    if probe is None:
        return None
    return ProbePillar(
        score=agent_score.compute_probe_pillar(
            probe.responded, probe.latency_ms, probe.presence, probe.skills_count
        ),
        responded=probe.responded,
        latency_ms=probe.latency_ms,
        status=probe.status,
        presence=probe.presence,
        skills_count=probe.skills_count,
        probed_at=probe.probed_at,
    )


def track_pillar_from_record(record: agent_score.TrackRecord) -> TrackRecordPillar:
    """Build the track-record pillar from the 90-day aggregate (D7)."""
    return TrackRecordPillar(
        score=agent_score.compute_track_record_pillar(
            record.age_months, record.event_count, record.unique_buyers, record.recency_days
        ),
        age_months=record.age_months,
        event_count=record.event_count,
        unique_buyers=record.unique_buyers,
        recency_days=record.recency_days,
    )


async def pillars_for_agent(session: AsyncSession, agent_id: str) -> Pillars:
    """Probe + track-record pillars for one agent (local reads only)."""
    probe = await latest_probe_for(session, agent_id)
    record = await agent_score.fetch_track_record(session, agent_id)
    return Pillars(
        probe=probe_pillar_from_row(probe),
        track_record=track_pillar_from_record(record),
    )


def breakdown_for(
    probe: AgentProbe | None, record: agent_score.TrackRecord
) -> list[dict[str, Any]]:
    """Flat `[{dimension, score, weight}]` breakdown for display (A1)."""
    return agent_score.build_breakdown(
        responded=probe.responded if probe is not None else None,
        latency_ms=probe.latency_ms if probe is not None else None,
        presence=probe.presence if probe is not None else None,
        skills_count=probe.skills_count if probe is not None else None,
        age_months=record.age_months,
        event_count=record.event_count,
        unique_buyers=record.unique_buyers,
        recency_days=record.recency_days,
    )


#: Ids query format for /compare (A2): "chain/token,chain/token,...
_IDS_RE = re.compile(r"^\d+/\d+(,\d+/\d+)*$")


@router.get("/compare", response_model=CompareOut)
async def compare_agents(
    db: Annotated[AsyncSession, Depends(get_db)],
    ids: str = Query(description='ids like "56/123,56/456"'),
) -> CompareOut:
    """Side-by-side local comparison of cached agents (spec A2).

    Declared BEFORE `/{chain_id}/{token_id}` — a literal `compare` first
    segment must never be swallowed by the path-param routes (design key
    learning). `ids` must match the `_IDS_RE` pattern (chain/token pairs);
    anything else is a 422 before any query runs. Uncached ids are
    silently skipped.
    """
    if not _IDS_RE.match(ids):
        raise ValidationError(f"invalid ids format: {ids!r} (expected chain/token,chain/token)")

    pairs = [tuple(int(part) for part in seg.split("/")) for seg in ids.split(",")]
    conditions = [and_(AgentCache.chain_id == c, AgentCache.token_id == t) for c, t in pairs]
    rows = (await db.scalars(select(AgentCache).where(or_(*conditions)))).all()

    agents: list[CompareAgentOut] = []
    for row in rows:
        pillars = await pillars_for_agent(db, row.agent_id)
        agents.append(
            CompareAgentOut(
                chain=row.chain_id,
                token=row.token_id,
                name=row.name,
                activity_score=row.activity_score,
                pillars=pillars,
            )
        )
    return CompareOut(agents=agents)


@router.get("/{chain_id}/{token_id}/score", response_model=ScoreOut)
async def get_agent_score(
    chain_id: int,
    token_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ScoreOut:
    """Composite activity score with pillars + breakdown (spec A1).

    D5 lazy path: when `activity_score IS NULL` (no-A2A agent, or not yet
    probed/materialized), recompute from the latest probe row + the 90-day
    track record and materialize it into `agent_cache`.
    """
    row = await db.scalar(
        select(AgentCache).where(AgentCache.chain_id == chain_id, AgentCache.token_id == token_id)
    )
    if row is None:
        raise NotFound(f"agent {chain_id}:{token_id} not cached")

    probe = await latest_probe_for(db, row.agent_id)
    record = await agent_score.fetch_track_record(db, row.agent_id)
    pillars = Pillars(
        probe=probe_pillar_from_row(probe),
        track_record=track_pillar_from_record(record),
    )

    score: Decimal | None = row.activity_score
    if score is None:
        computed = agent_score.composite_score(
            pillars.probe.score if pillars.probe is not None else None,
            pillars.track_record.score,
        )
        await agent_score.materialize_score(db, row.agent_id, computed)
        await db.commit()
        score = Decimal(str(computed))
    assert score is not None

    return ScoreOut(
        chain=row.chain_id,
        token=row.token_id,
        activity_score=score,
        pillars=pillars,
        breakdown=breakdown_for(probe, record),
    )


__all__ = ["router"]


# `case` import is used for explicit ordering fallbacks elsewhere; keep
# the import here so ruff doesn't strip it from re-exports.
_ = case  # noqa: F841
