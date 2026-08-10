"""HiredAgent model — hire lifecycle with x402 payment metadata.

FU-1 shipped the stub (status='pending', no payment data); the x402 change
(FU-2, design id 52 Q5) adds the 5 nullable payment columns + the
`(address, status)` index. All columns are nullable so no data migration is
needed; `pending` rows past `challenge_expiry` are cancelled lazily by the
TTL sweep inside create_hire (spec H3).
"""
from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Numeric, String, Text, text
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

    # ---- x402 payment metadata (FU-2, design id 52 Q5) ------------------
    #: $U units (18 decimals) — 1.000000000000000000 = $1.00 at the default
    #: price. The challenge amount in wei is `int(amount * 10**18)`.
    amount: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    #: Token address (checksummed, e.g. pinned $U for the configured chain).
    token: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Payment rail — `"eip3009"` in v1 (Q2; TEXT for future rails).
    rail: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Recipient of the settlement — echo of the agent's `agent_wallet`
    #: (design Q6: no owner fallback).
    pay_to: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: `created_at + maxTimeoutSeconds`; past this the hire is swept to
    #: `cancelled` by the lazy TTL sweep (spec H3/X7).
    challenge_expiry: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

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
        # Per-user status scans (TTL sweep + user hire lists) — design Q5.
        Index("ix_hired_agents_address_status", "address", "status"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"HiredAgent(id={self.id!r}, status={self.status!r})"
