"""Unit tests for onchain_indexer backfill resume logic.

Regression fix: a populated DB must resume from its highest indexed block
instead of restarting from the token creation era (~72M) and re-scanning
~47M blocks (~500 days at the backfill pace).
"""

from __future__ import annotations

from app.services.onchain_indexer import U_FIRST_TRANSFER_BLOCK, _resolve_backfill_start


def test_resolve_backfill_start_empty_db() -> None:
    """Empty DB starts at the first block with actual $U transfers."""
    assert _resolve_backfill_start(0) == U_FIRST_TRANSFER_BLOCK - 1


def test_resolve_backfill_start_resumes_from_db_max() -> None:
    """Populated DB continues from its highest indexed block."""
    assert _resolve_backfill_start(118_895_000) == 118_895_000


def test_resolve_backfill_start_near_head() -> None:
    """A DB already near the head must not restart from the creation era."""
    assert _resolve_backfill_start(119_424_301) == 119_424_301
