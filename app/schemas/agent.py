"""Pydantic schemas for the agent cache read API."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AgentOut(BaseModel):
    """One agent row in the listing/detail responses."""

    model_config = ConfigDict(from_attributes=True)

    agent_id: str
    chain_id: int
    token_id: int
    registry_address: str
    owner_address: str | None = None
    name: str | None = None
    description: str | None = None
    image_url: str | None = None
    x402_supported: bool = False
    supported_protocols: list[str] = Field(default_factory=list)
    category: str
    average_score: Decimal | None = None
    total_feedbacks: int = 0
    is_verified: bool = False
    cross_chain_versions: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
