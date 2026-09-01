"""Pydantic schemas for the favorites API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FavoriteCreate(BaseModel):
    agent_id: str = Field(..., description="Canonical agent_id from /api/agents.")


class FavoriteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    address: str
    agent_id: str
    created_at: datetime
    # Optional embedded agent view so the UI can render without a second call.
    agent: dict[str, Any] | None = None
