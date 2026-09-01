"""Pydantic schemas for the hire flow (challenge create, pay, status)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.hired_agent import HiredStatus


class HireCreate(BaseModel):
    agent_id: str = Field(..., description="Canonical agent_id from /api/agents.")


class HireOut(BaseModel):
    """Hire row incl. x402 payment metadata (FU-2, design id 52 Q5)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    address: str
    agent_id: str
    status: HiredStatus
    amount: Decimal | None = None
    token: str | None = None
    rail: str | None = None
    pay_to: str | None = None
    challenge_expiry: datetime | None = None
    tx_hash: str | None = None
    created_at: datetime
    updated_at: datetime


class HireCreateOut(HireOut):
    """201 response: hire row + the B402 challenge the browser signs (X1)."""

    challenge: dict | None = None


class HirePayOut(BaseModel):
    """200 response of POST /{id}/pay: status + tx_hash (H1)."""

    id: int
    status: HiredStatus
    tx_hash: str | None = None
