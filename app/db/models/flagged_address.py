"""FlaggedAddress model — OFAC-sanctioned wallet addresses (T2 trust signal).

Spec: DESIGN.md "wallet risk flags". One row per (sanctioned address, source
list): the composite PK (address, source) allows the same wallet to appear in
both the BSC and ETH OFAC lists (shared EVM address space). `source` names the
list ("ofac-bsc" / "ofac-eth", see `app/services/flagged_sync.py`). Rows are
mirrored by the flagged-address sync and REPLACED per source, so a wallet
that drops off the upstream list disappears here too.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

_ADDRESS_LEN: int = 42
_SOURCE_LEN: int = 64


class FlaggedAddress(Base):
    """One OFAC-sanctioned digital-currency address, with its source list."""

    __tablename__ = "flagged_addresses"

    address: Mapped[str] = mapped_column(String(_ADDRESS_LEN), primary_key=True)
    source: Mapped[str] = mapped_column(String(_SOURCE_LEN), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"FlaggedAddress(address={self.address!r}, source={self.source!r})"