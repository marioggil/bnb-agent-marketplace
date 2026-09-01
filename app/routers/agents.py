"""GET /api/agents — search, filter, sort, paginate the local AgentCache.

Spec: `sdd/marketplace-scaffold/spec/agents-cache` (#19) + spec
`web-pages` (#22) for the listing query string.

The endpoint NEVER calls 8004scan — the cache is the only read path
(id 11 gotcha: /search is broken on the upstream). All filters and the
sort key are applied to `AgentCache` directly.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.agent import AgentCache
from app.db.session import get_db
from app.errors import ValidationError
from app.schemas.agent import AgentOut
from app.schemas.pagination import Page

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])


# Whitelisted sort keys — defends against SQL-injection via `?sort=`.
_SORT_KEYS: dict[str, Any] = {
    "average_score": AgentCache.average_score.desc().nullslast(),
    "total_feedbacks": AgentCache.total_feedbacks.desc(),
    "created_at": AgentCache.created_at.desc(),
    "name": AgentCache.name.asc().nullslast(),
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
    from app.errors import NotFound

    row = await db.scalar(
        select(AgentCache).where(AgentCache.chain_id == chain_id, AgentCache.token_id == token_id)
    )
    if row is None:
        raise NotFound(f"agent {chain_id}:{token_id} not cached")
    return AgentOut.model_validate(row)


__all__ = ["router"]


# `case` import is used for explicit ordering fallbacks elsewhere; keep
# the import here so ruff doesn't strip it from re-exports.
_ = case  # noqa: F841
