"""Pydantic schemas for the hire stub (status=pending)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.hired_agent import HiredStatus


class HireCreate(BaseModel):
    agent_id: str = Field(..., description="Canonical agent_id from /api/agents.")


class HireOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    address: str
    agent_id: str
    status: HiredStatus
    tx_hash: str | None = None
    created_at: datetime
    updated_at: datetime
