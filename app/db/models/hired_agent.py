"""HiredAgent model — POST /api/hires stub returning status='pending'.

Real x402 payment integration is out of scope for this slice. The status
enum is forward-compatible: `paid` and `failed` will be set by the
follow-up change that wires the B402 SDK and confirms the onchain tx.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

_ADDRESS_LEN: int = 42


class HiredStatus(str, enum.Enum):
    """Lifecycle of a hire request."""

    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    CANCELLED = "cancelled"


#: Postgres enum type name — referenced by the initial migration and by any
#: later migration that needs to ALTER it.
HIRED_STATUS_ENUM_NAME: str = "hired_status"


class HiredAgent(Base):
    """A hire request from a user for a cached agent."""

    __tablename__ = "hired_agents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    address: Mapped[str] = mapped_column(
        String(_ADDRESS_LEN),
        ForeignKey("users.address", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("agent_cache.agent_id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[HiredStatus] = mapped_column(
        SAEnum(
            HiredStatus,
            name=HIRED_STATUS_ENUM_NAME,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        server_default=text("'pending'"),
    )
    tx_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        # Helps the (rare) admin query for in-flight payments.
        Index(
            "ix_hired_agents_pending",
            "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"HiredAgent(id={self.id!r}, status={self.status!r})"
