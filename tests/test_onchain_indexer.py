"""Unit tests for onchain_indexer backfill sampling logic.

The backfill is a sampler over the last 90 days, not a sequential
scanner: a sequential scan from the first transfer block would need
~500 days to reach the head, and resuming from db_max is useless while
the realtime worker keeps db_max at the head.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.services.onchain_indexer import (
    BACKFILL_START_WINDOW_BLOCKS,
    U_FIRST_TRANSFER_BLOCK,
    _backfill_window,
    _make_ts_resolver,
    _pick_sample_start,
)

_HEAD = 119_431_222


def test_backfill_window_spans_last_90_days() -> None:
    """The sampling window is (head - 90d .. head - realtime zone)."""
    start, end = _backfill_window(_HEAD)
    assert start == _HEAD - BACKFILL_START_WINDOW_BLOCKS
    assert end == _HEAD - 75  # REALTIME_CHUNK_SIZE owns the head


def test_backfill_window_floor() -> None:
    """The window never dips below the first transfer block."""
    # Head so low that the 90-day window lands below the floor: the floor
    # clips it to a valid range instead of producing a negative window.
    start, end = _backfill_window(72_500_000)
    assert start == U_FIRST_TRANSFER_BLOCK
    assert end == 72_500_000 - 75
    # Head below the floor + realtime zone: no window at all.
    assert _backfill_window(72_000_000) is None


def test_pick_sample_start_skips_covered_blocks() -> None:
    """A random block that is covered is rejected for an uncovered one."""
    covered = {100, 101, 102, 103, 104}
    calls: list[int] = []

    def fake_randint(lo: int, hi: int) -> int:
        calls.append(1)
        return 102 if len(calls) == 1 else 105  # first pick covered, second free

    assert _pick_sample_start(100, 200, covered, randint=fake_randint) == 105


def test_pick_sample_start_all_covered() -> None:
    """A fully covered window returns None."""
    assert _pick_sample_start(100, 104, {100, 101, 102, 103, 104}) is None


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
