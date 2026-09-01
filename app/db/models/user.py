"""User model — wallet-address keyed identity (design #26, spec #20).

The address is the primary key; we never store email or password. Single
session cookie is the only auth credential. Users are upserted on first
successful EIP-191 verify (spec wallet-auth R2).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

#: 0x + 40 hex chars = 42.
_ADDRESS_LEN: int = 42


class User(Base):
    """A wallet-authenticated user."""

    __tablename__ = "users"

    address: Mapped[str] = mapped_column(String(_ADDRESS_LEN), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"User(address={self.address!r})"
