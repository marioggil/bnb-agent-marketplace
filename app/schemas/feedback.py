"""Pydantic schemas for the agent-feedbacks API.

`FeedbackOut` mirrors the `agent_feedbacks` row shape for the JSON response
of `GET /agents/{chain_id}/{token_id}/feedbacks` (plain callers; HTMX
callers get the `reviews_list.html` fragment instead).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class FeedbackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    feedback_id: str
    agent_id: str
    chain_id: int
    token_id: int
    user_address: str | None = None
    score: int | None = None
    comment: str | None = None
    tag1: str | None = None
    tag2: str | None = None
    tx_hash: str | None = None
    block_number: int | None = None
    submitted_at: datetime | None = None
    is_revoked: bool
    created_at: datetime
    updated_at: datetime
