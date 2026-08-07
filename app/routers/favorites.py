"""Favorites API: GET / POST / DELETE; session + CSRF on writes.

Spec: `sdd/marketplace-scaffold/spec/favorites-hires` (#21) R1-R3, R6.
Design: id 26 (routers/favorites.py contract).
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.agent import AgentCache
from app.db.models.favorite import Favorite
from app.db.models.user import User
from app.db.session import get_db
from app.errors import NotFound
from app.schemas.favorite import FavoriteCreate, FavoriteOut
from app.services.auth import get_current_user, require_csrf

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/favorites", tags=["favorites"])


@router.get("", response_model=list[FavoriteOut])
async def list_favorites(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> list[FavoriteOut]:
    """List the caller's favorites (spec R2)."""
    rows = (
        await db.scalars(
            select(Favorite).where(Favorite.address == user.address).order_by(Favorite.created_at.desc())
        )
    ).all()
    return [FavoriteOut.model_validate(r) for r in rows]


@router.post("", response_model=FavoriteOut, status_code=status.HTTP_201_CREATED)
async def add_favorite(
    payload: FavoriteCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _csrf: Annotated[None, Depends(require_csrf)] = None,
) -> FavoriteOut:
    """Upsert a favorite for the caller (spec R1)."""
    # 404 if the agent isn't cached.
    agent = await db.scalar(select(AgentCache.agent_id).where(AgentCache.agent_id == payload.agent_id))
    if agent is None:
        raise NotFound(f"agent {payload.agent_id!r} not cached")
    stmt = (
        pg_insert(Favorite)
        .values(address=user.address, agent_id=payload.agent_id)
        .on_conflict_do_nothing(index_elements=[Favorite.address, Favorite.agent_id])
        .returning(Favorite.created_at)
    )
    res = await db.execute(stmt)
    created = res.scalar_one()
    await db.commit()
    return FavoriteOut(
        address=user.address, agent_id=payload.agent_id, created_at=created
    )


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_favorite(
    agent_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _csrf: Annotated[None, Depends(require_csrf)] = None,
) -> None:
    """Remove the caller's favorite (spec R3). 404 if not owned."""
    row = await db.scalar(
        select(Favorite).where(
            Favorite.address == user.address, Favorite.agent_id == agent_id
        )
    )
    if row is None:
        raise NotFound("favorite not found")
    await db.delete(row)
    await db.commit()


__all__ = ["router"]
