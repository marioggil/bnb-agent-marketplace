"""Async SQLAlchemy engine + session factory + FastAPI dependency.

The runtime uses `asyncpg` for the FastAPI request loop. Alembic (D9 in design)
uses a separate `psycopg2` sync engine against the same `DATABASE_URL`, only
flipping the driver suffix; see `migrations/env.py`.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any, Final

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
    """Build the async engine. Exposed for tests that need to inject a URL.

    Pool sizing args are only valid for the Postgres/asyncpg dialect. When
    the URL targets sqlite (tests, aiosqlite in-memory), SQLAlchemy 2.0.51+
    rejects `pool_size`/`max_overflow` for StaticPool — so those kwargs are
    omitted for non-Postgres drivers.
    """
    resolved = url or _build_database_url()
    pool_kwargs: dict[str, Any] = {"pool_pre_ping": True, "future": True}
    if resolved.startswith("postgresql"):
        pool_kwargs.update(pool_size=POOL_SIZE, max_overflow=MAX_OVERFLOW)
    return create_async_engine(resolved, **pool_kwargs)


# Lazy engine + sessionmaker. Importing this module MUST NOT touch the
# network or parse DATABASE_URL; otherwise scripts that import any of the
# app.* modules before DATABASE_URL is set in the environment (Alembic's
# env.py is the canonical example) crash with
#   "Could not parse SQLAlchemy URL from given URL string".
# The engine is created the first time get_engine() is called.
_engine: AsyncEngine | None = None
_AsyncSessionLocal: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Return the lazily-initialized async engine, creating it on first call."""
    global _engine, _AsyncSessionLocal
    if _engine is None:
        _engine = make_engine()
        _AsyncSessionLocal = async_sessionmaker(
            _engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the lazily-initialized session factory (creates the engine too)."""
    get_engine()
    assert _AsyncSessionLocal is not None  # for type-checkers
    return _AsyncSessionLocal


# Back-compat aliases for callers that imported the module-level names
# directly (the FastAPI app's lifespan and get_db still use them). They are
# resolved lazily on first attribute access.
def __getattr__(name: str):
    if name == "engine":
        return get_engine()
    if name == "AsyncSessionLocal":
        return get_sessionmaker()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Re-export Base for callers that need metadata (e.g. Alembic).
# NOTE: `engine` and `AsyncSessionLocal` are intentionally NOT in `__all__`:
# they are lazy PEP 562 exports resolved by `__getattr__` above, so `from
# app.db.session import *` must not claim them as module-level bindings.
# Direct imports of both names keep working (verified: 22 call sites, zero
# star-imports).
__all__ = [
    "Base",
    "POOL_SIZE",
    "MAX_OVERFLOW",
    "get_db",
    "get_engine",
    "get_sessionmaker",
    "make_engine",
]


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a request-scoped async session."""
    async with get_sessionmaker()() as session:
        yield session
