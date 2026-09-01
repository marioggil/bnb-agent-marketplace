"""AuthNonce model — single-use 10-minute nonce store for EIP-191 verify.

The auth router (PR-C) issues a nonce, signs in with `personal_sign`, then
flips `used=true` in the same transaction as the user upsert. A periodic
cleanup deletes rows older than the TTL.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

_ADDRESS_LEN: int = 42
#: Default nonce lifetime. Single-use is enforced via the `used` flag; the
#: worker (or a follow-up cron) cleans up expired rows.
NONCE_TTL_SECONDS: int = 600


class AuthNonce(Base):
    """A nonce issued to a wallet address, consumed once or expired."""

    __tablename__ = "auth_nonce"

    address: Mapped[str] = mapped_column(String(_ADDRESS_LEN), primary_key=True)
    nonce: Mapped[str] = mapped_column(String(64), nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        # Speeds up `verify_signature` lookups by address+used status.
        Index("ix_auth_nonce_address_used", "address", "used"),
        # Helps the cleanup query that drops expired rows.
        Index("ix_auth_nonce_expires_at", "expires_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"AuthNonce(address={self.address!r}, used={self.used!r})"
