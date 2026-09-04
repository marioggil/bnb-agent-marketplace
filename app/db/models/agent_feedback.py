"""AgentFeedback model — one individual review of a cached agent.

Mirrors the 8004scan /api/v1/feedbacks endpoint (agent-feedbacks): the
upstream `feedback_id` (e.g. "56:137:0x…:1") is the natural PK and the sync
upserts on it, so re-runs converge instead of duplicating rows. `score` is
the integer form of the upstream `value` when `value_decimals == 0`
(otherwise None — a decimal score cannot map to an int losslessly).
Rows are synced by `app/services/feedback_sync.py` and read by the
`GET /agents/{chain_id}/{token_id}/feedbacks` endpoint + the collapsible
reviews panel on the agent detail page.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

_FEEDBACK_ID_LEN: int = 160
_ADDRESS_LEN: int = 42
_TAG_LEN: int = 64
_TX_HASH_LEN: int = 66


class AgentFeedback(Base):
    """One upstream review row, keyed by its 8004scan feedback_id."""

    __tablename__ = "agent_feedbacks"

    feedback_id: Mapped[str] = mapped_column(String(_FEEDBACK_ID_LEN), primary_key=True)
    agent_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("agent_cache.agent_id", ondelete="CASCADE"),
        nullable=False,
    )
    chain_id: Mapped[int] = mapped_column(Integer, nullable=False)
    token_id: Mapped[int] = mapped_column(Integer, nullable=False)
    user_address: Mapped[str | None] = mapped_column(String(_ADDRESS_LEN), nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    tag1: Mapped[str | None] = mapped_column(String(_TAG_LEN), nullable=True)
    tag2: Mapped[str | None] = mapped_column(String(_TAG_LEN), nullable=True)
    tx_hash: Mapped[str | None] = mapped_column(String(_TX_HASH_LEN), nullable=True)
    block_number: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        # Per-agent listing, newest first (the /feedbacks listing order).
        # agent_id is the leading column, so per-agent lookups use it too.
        Index(
            "ix_agent_feedbacks_agent_submitted_at",
            "agent_id",
            text("submitted_at DESC"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"AgentFeedback(feedback_id={self.feedback_id!r})"
