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

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Float, and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.agent import AgentCache
from app.db.models.agent_compliance import AgentComplianceFlag
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
    ScoreOutMinimal,
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


@router.get("/score", response_model=Page[ScoreOutMinimal])
async def rank_agents_score(
    db: Annotated[AsyncSession, Depends(get_db)],
    category: str | None = Query(
        default=None,
        description=(
            "Optional category filter. Accepted values mirror `list_agents`. "
            "Unknown values → HTTP 400. Empty string is treated as no filter."
        ),
    ),
    sort: str = Query(
        default="displayed_activity_score",
        description="Sort key. v1 supports `displayed_activity_score` only.",
    ),
    limit: int = Query(
        default=20,
        description="Page size. Default 20. Clamped to 100. Negative or non-int → 400.",
    ),
    offset: int = Query(default=0, ge=0),
) -> Page[ScoreOutMinimal]:
    """Ranking endpoint — `Page[ScoreOutMinimal]` ordered by displayed score.

    Spec `openspec/changes/score-integration/spec.md` AC-1/AC-2/AC-3.
    Declared BEFORE `/{chain_id}/{token_id}` so the literal `/score` segment
    is never swallowed by the path-param routes (same key learning as the
    `/compare` route — path-param-first routes shadow literal siblings
    declared below them). Single `SELECT` against `AgentCache` with a
    `LEFT JOIN` to `agent_compliance_flags` for `creator_is_owner`; the
    `displayed_activity_score` is computed at SELECT time via portable
    `CASE WHEN a IS NULL THEN NULL WHEN a - b > 0 THEN a - b ELSE 0 END`
    (SQLite has no `GREATEST`; identical on PostgreSQL).
    """
    if sort != "displayed_activity_score":
        raise HTTPException(
            status_code=400,
            detail=f"unsupported sort key: {sort!r} (expected 'displayed_activity_score')",
        )
    if category is not None and category != "" and category not in _RANK_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unsupported category: {category!r} (expected one of "
                f"{sorted(_RANK_CATEGORIES)})"
            ),
        )

    # Manual `limit` validation so the spec-pinned HTTP 400 boundary holds
    # for negative values (FastAPI's `Query(ge=1)` would emit 422). The
    # upper bound is clamped silently to 100 per AC-1.
    if limit < 1:
        raise HTTPException(
            status_code=400,
            detail=f"invalid limit: {limit!r} (expected int >= 1)",
        )
    if limit > 100:
        limit = 100

    diff = AgentCache.activity_score - AgentCache.compliance_penalty
    displayed_expr = case(
        (AgentCache.activity_score.is_(None), None),
        (diff > 0, diff),
        else_=0,
    ).cast(Float).label("displayed_activity_score")

    stmt = (
        select(
            AgentCache.chain_id,
            AgentCache.token_id,
            AgentCache.name,
            AgentCache.activity_score,
            AgentCache.compliance_penalty,
            displayed_expr,
            AgentComplianceFlag.creator_is_owner,
        )
        .select_from(AgentCache)
        .outerjoin(
            AgentComplianceFlag,
            AgentComplianceFlag.agent_id == AgentCache.agent_id,
        )
    )
    count_stmt = select(func.count()).select_from(AgentCache)

    if category:
        stmt = stmt.where(AgentCache.category == category)
        count_stmt = count_stmt.where(AgentCache.category == category)

    stmt = (
        stmt.order_by(displayed_expr.desc().nullslast())
        .offset(offset)
        .limit(limit)
    )

    total = int(await db.scalar(count_stmt) or 0)
    rows = (await db.execute(stmt)).all()

    items = [
        ScoreOutMinimal(
            chain=row.chain_id,
            token=row.token_id,
            name=row.name,
            activity_score=row.activity_score,
            compliance_penalty=float(row.compliance_penalty or 0),
            displayed_activity_score=float(row.displayed_activity_score or 0.0),
            creator_is_owner=bool(row.creator_is_owner),
        )
        for row in rows
    ]
    return Page[ScoreOutMinimal](
        items=items,
        total=total,
        page=(offset // limit) + 1 if limit else 1,
        page_size=limit,
    )


# ---------------------------------------------------------------------------
# Score ranking surface — score-integration AC-1/AC-2/AC-3.
# ---------------------------------------------------------------------------

_RANK_CATEGORIES: frozenset[str] = frozenset({
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
})


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

    # Phase 1b: OFAC compliance adjustment surface — additive ScoreOut fields.
    # `compliance_penalty` mirrors the stored column 1a-i added and 1a-ii
    # populates; `displayed_activity_score` is the user-facing value
    # `max(0, activity_score - compliance_penalty)`. Floats at the JSON
    # boundary (Pydantic default) — the column type is Decimal but Pydantic
    # serializes these as floats, and the test asserts exact float equality.
    # design §5.4 / §6.R-10: Decimal stays canonical inside `agent_cache`,
    # float only at the serializer boundary.
    compliance_penalty_value = float(row.compliance_penalty or 0)
    score_value = float(score)

    # Phase wallet-activity: parallel, additive sub-score from creator + owner
    # wallet on-chain footprint (spec wallet-activity AC-7). The composite
    # `activity_score` is NEVER mutated; `wallet_activity_score` is the
    # computed Decimal converted to float at the JSON boundary. Failure of
    # either helper swallows → wallet_activity_score=None, breakdown=None,
    # creator_is_owner=False (mirrors the compliance-flags swallow pattern).
    wallet_activity_score_value: float | None = None
    wallet_activity_breakdown_value: dict[str, Any] | None = None
    creator_is_owner_value: bool = False
    try:
        from app.services import wallet_activity

        signals = await wallet_activity.fetch_wallet_signals(db, row.agent_id)
        track_record_pillar = pillars.track_record.score
        wa_decimal = wallet_activity.compute_wallet_activity_score(
            creator_events=signals.creator.events,
            creator_counterparties=signals.creator.counterparties,
            creator_recency_days=signals.creator.recency_days,
            creator_is_neutral=signals.creator.is_neutral,
            owner_events=signals.owner.events,
            owner_counterparties=signals.owner.counterparties,
            owner_recency_days=signals.owner.recency_days,
            owner_is_neutral=signals.owner.is_neutral,
            track_record_score=Decimal(str(track_record_pillar)),
        )
        wallet_activity_score_value = float(wa_decimal)
        wallet_activity_breakdown_value = wallet_activity.compute_wallet_activity_breakdown(
            signals, Decimal(str(track_record_pillar))
        )
        creator_is_owner_value = signals.creator_is_owner
    except Exception:
        logger.warning(
            "wallet_activity_score fetch failed for %s", row.agent_id, exc_info=True
        )

    return ScoreOut(
        chain=row.chain_id,
        token=row.token_id,
        activity_score=score,
        compliance_penalty=compliance_penalty_value,
        displayed_activity_score=max(0.0, score_value - compliance_penalty_value),
        wallet_activity_score=wallet_activity_score_value,
        wallet_activity_breakdown=wallet_activity_breakdown_value,
        creator_is_owner=creator_is_owner_value,
        pillars=pillars,
        breakdown=breakdown_for(probe, record),
    )


__all__ = ["router"]


# `case` import is used for explicit ordering fallbacks elsewhere; keep
# the import here so ruff doesn't strip it from re-exports.
_ = case  # noqa: F841
