"""Favorite model — composite PK on (address, agent_id), cascading on delete.

A favorite is a thin join from a wallet to a cached agent. The cascade on
delete is intentionally one-way: removing a user or a cached agent drops
their favorites; a user only removes one favorite at a time via the API.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

_ADDRESS_LEN: int = 42


class Favorite(Base):
    """A user has bookmarked an agent."""

    __tablename__ = "favorites"

    address: Mapped[str] = mapped_column(
        String(_ADDRESS_LEN),
        ForeignKey("users.address", ondelete="CASCADE"),
        primary_key=True,
    )
    agent_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("agent_cache.agent_id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"Favorite(address={self.address!r}, agent_id={self.agent_id!r})"
