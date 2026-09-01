"""SyncState model — singleton row holding worker checkpoint + failure log.

The PK is `SMALLINT` with a CHECK id=1 so the table can only ever hold one
row. The `failed_token_ids` array is FIFO-capped at 1000 by the worker
(design D7) — we do not enforce it at the DB level to keep the model
agnostic of that policy.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    SmallInteger,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

#: Hard cap for the JSONB failure array. Kept here so the worker and any
#: admin tooling share the constant.
FAILED_TOKEN_IDS_CAP: int = 1000


class SyncState(Base):
    """Worker checkpoint and recent failures."""

    __tablename__ = "sync_state"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, server_default=text("1"))
    last_token_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("-1")
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_token_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (CheckConstraint("id = 1", name="sync_state_singleton"),)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"SyncState(last_token_id={self.last_token_id!r})"
