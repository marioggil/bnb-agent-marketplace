"""Unit tests for onchain_indexer backfill resume logic.

Regression fix: a populated DB must resume from its highest indexed block
instead of restarting from the token creation era (~72M) and re-scanning
~47M blocks (~500 days at the backfill pace). An empty DB starts at
(head - 90 days) because the activity metric only consumes that window.
"""

from __future__ import annotations

from app.services.onchain_indexer import (
    BACKFILL_START_WINDOW_BLOCKS,
    U_FIRST_TRANSFER_BLOCK,
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
