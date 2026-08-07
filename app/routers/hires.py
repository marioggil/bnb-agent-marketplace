"""Hire stub: POST /api/hires returns 201 with status=pending (no tx).

Spec: `sdd/marketplace-scaffold/spec/favorites-hires` (#21) R4-R6.
The real x402 payment flow is a later change.
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.agent import AgentCache
from app.db.models.hired_agent import HiredAgent, HiredStatus
from app.db.models.user import User
from app.db.session import get_db
from app.errors import NotFound
from app.schemas.hired import HireCreate, HireOut
from app.services.auth import get_current_user, require_csrf

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/hires", tags=["hires"])


@router.post("", response_model=HireOut, status_code=status.HTTP_201_CREATED)
async def create_hire(
    payload: HireCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _csrf: Annotated[None, Depends(require_csrf)] = None,
) -> HireOut:
    """Insert a HiredAgent row with status=pending (spec R5)."""
    agent = await db.scalar(
        select(AgentCache.agent_id).where(AgentCache.agent_id == payload.agent_id)
    )
    if agent is None:
        raise NotFound(f"agent {payload.agent_id!r} not cached")
    row = HiredAgent(
        address=user.address,
        agent_id=payload.agent_id,
        status=HiredStatus.PENDING,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return HireOut.model_validate(row)


__all__ = ["router"]
