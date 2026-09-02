"""Unit tests for onchain_indexer backfill resume logic.

Regression fix: a populated DB must resume from its highest indexed block
instead of restarting from the token creation era (~72M) and re-scanning
~47M blocks (~500 days at the backfill pace). An empty DB starts at
(head - 90 days) because the activity metric only consumes that window.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.services.onchain_indexer import (
    BACKFILL_START_WINDOW_BLOCKS,
    U_FIRST_TRANSFER_BLOCK,
    _make_ts_resolver,
    _resolve_backfill_start,
)

_HEAD = 119_431_222


def test_resolve_backfill_start_empty_db() -> None:
    """Empty DB starts at head - 90 days, not at the token creation era."""
    assert _resolve_backfill_start(0, _HEAD) == _HEAD - BACKFILL_START_WINDOW_BLOCKS


def test_resolve_backfill_start_empty_db_floor() -> None:
    """The recent window never goes below the first transfer block."""
    assert _resolve_backfill_start(0, 75_000_000) == U_FIRST_TRANSFER_BLOCK - 1


def test_resolve_backfill_start_resumes_from_db_max() -> None:
    """Populated DB continues from its highest indexed block."""
    assert _resolve_backfill_start(118_895_000, _HEAD) == 118_895_000


def test_resolve_backfill_start_near_head() -> None:
    """A DB already near the head must not restart from the creation era."""
    assert _resolve_backfill_start(119_424_301, _HEAD) == 119_424_301


def test_ts_resolver_interpolates_between_edges() -> None:
    """Timestamps between the range edges are interpolated linearly."""

    async def fake(block: int) -> datetime | None:
        if block == 100:
            return datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
        if block == 110:
            return datetime(2026, 9, 1, 0, 5, tzinfo=timezone.utc)
        return None

    resolver = asyncio.run(_make_ts_resolver(fake, 100, 110))
    assert resolver(100) == datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    assert resolver(105) == datetime(2026, 9, 1, 0, 2, 30, tzinfo=timezone.utc)
    assert resolver(110) == datetime(2026, 9, 1, 0, 5, tzinfo=timezone.utc)


def test_ts_resolver_single_edge_suffices() -> None:
    """One working edge is enough; the missing edge mirrors it."""

    async def fake(block: int) -> datetime | None:
        return datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc) if block == 100 else None

    resolver = asyncio.run(_make_ts_resolver(fake, 100, 110))
    assert resolver(110) == datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)


def test_ts_resolver_fallback_is_constant() -> None:
    """When both edges fail, every block gets the same fallback timestamp.

    Regression: the old per-log RPC call silently stored ``now()`` with
    microseconds for every transfer; the resolver must never fabricate
    per-log timestamps.
    """

    async def fake(block: int) -> datetime | None:
        return None

    resolver = asyncio.run(_make_ts_resolver(fake, 100, 110))
    assert resolver(100) == resolver(105) == resolver(110)
