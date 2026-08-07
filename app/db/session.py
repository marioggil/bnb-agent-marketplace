"""Async SQLAlchemy engine + session factory + FastAPI dependency.

The runtime uses `asyncpg` for the FastAPI request loop. Alembic (D9 in design)
uses a separate `psycopg2` sync engine against the same `DATABASE_URL`, only
flipping the driver suffix; see `migrations/env.py`.
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Final

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.base import Base

# Default pool sizing. Tuned for a single Dokploy container running uvicorn
# workers=2; the app code should not exceed this concurrency in a single
# request. Workers fan-out horizontally if more headroom is needed.
POOL_SIZE: Final[int] = 5
MAX_OVERFLOW: Final[int] = 10


def _build_database_url() -> str:
    """Resolve the async DATABASE_URL.

    The migration runner swaps the driver to `postgresql+psycopg2://`; runtime
    here always uses `postgresql+asyncpg://`. We rewrite the driver suffix if
    a sync-style URL was provided in the env (e.g. via the Alembic chain).
    """
    raw = os.environ.get("DATABASE_URL", "").strip()
    if not raw:
        # Local-dev default. docker-compose overrides this.
        return "postgresql+asyncpg://bnb:bnb@localhost:5432/bnb_agent"
    if raw.startswith("postgresql+psycopg2://"):
        return raw.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if raw.startswith("postgresql://"):
        return raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    return raw


def make_engine(url: str | None = None) -> AsyncEngine:
    """Build the async engine. Exposed for tests that need to inject a URL."""
    return create_async_engine(
        url or _build_database_url(),
        pool_size=POOL_SIZE,
        max_overflow=MAX_OVERFLOW,
        pool_pre_ping=True,
        future=True,
    )


# Module-level engine; created lazily so importing the module is cheap and
# tests can replace `engine` before first use.
engine: AsyncEngine = make_engine()
AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


# Re-export Base for callers that need metadata (e.g. Alembic).
__all__ = [
    "AsyncSessionLocal",
    "Base",
    "POOL_SIZE",
    "MAX_OVERFLOW",
    "engine",
    "get_db",
    "make_engine",
]


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a request-scoped async session."""
    async with AsyncSessionLocal() as session:
        yield session
