"""Tests for the per-agent wallet-activity sub-score (wallet-activity change).

Spec: `openspec/changes/wallet-activity/spec.md` (AC-1..AC-6).
Design: `openspec/changes/wallet-activity/design.md` §3 (pure helper) + §5 (SQL fetcher).

This file pins the pure helper truth table first (RED → GREEN → TRIANGULATE),
then the `fetch_wallet_signals` SQL contract (RED → GREEN), then the query-count
invariants on `creator_is_owner`. All math failures show up at the helper
boundary (literal `Decimal` equality) before any DB-level assertion runs.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.agent import (
    BSC_CHAIN_ID,
    BSC_IDENTITY_REGISTRY,
    AgentCache,
    build_agent_id,
)
from app.db.models.onchain_index import OnchainTransfer
from app.services import wallet_activity
from tests.conftest import _now


# ---------------------------------------------------------------------------
# T1+T2 — Pillar truth table — RED on ImportError, GREEN once helpers exist.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "events,counterparties,recency,is_neutral,expected",
    [
        (0, 0, None, False, Decimal("0.00")),
        (0, 0, 30, False, Decimal("20.00")),  # 0.2 unit scaled to 100
        (1, 0, 30, False, Decimal("21.67")),  # 0.5*(1/30)+0.2 → 0.2167 → 21.67 (banker)
        (30, 10, 30, False, Decimal("100.00")),
        (60, 20, 15, False, Decimal("100.00")),
        (0, 0, None, True, Decimal("50.00")),
        (30, 10, 30, True, Decimal("50.00")),
    ],
)
def test_creator_wallet_pillar_score_truth_table(
    events: int, counterparties: int, recency: int | None, is_neutral: bool, expected: Decimal
) -> None:
    """Per-wallet pillar formula (D8): pillar in [0, 100]; truth-table values scaled."""
    actual = wallet_activity.creator_wallet_pillar_score(
        events, counterparties, recency, is_neutral
    )
    assert actual == expected, (
        f"events={events} cp={counterparties} recency={recency} neutral={is_neutral} → {actual}"
    )


@pytest.mark.parametrize(
    "events,counterparties,recency,is_neutral,expected",
    [
        (0, 0, None, False, Decimal("0.00")),
        (15, 5, 60, False, Decimal("50.00")),  # 0.5*(15/30)+0.3*(5/10)+0.2*(30/60)=0.50 unit
        (30, 10, 30, True, Decimal("50.00")),
    ],
)
def test_owner_wallet_pillar_score_truth_table(
    events: int, counterparties: int, recency: int | None, is_neutral: bool, expected: Decimal
) -> None:
    actual = wallet_activity.owner_wallet_pillar_score(
        events, counterparties, recency, is_neutral
    )
    assert actual == expected


# ---------------------------------------------------------------------------
# T3 — RED: neutral pillar = 50.
# ---------------------------------------------------------------------------


def test_is_neutral_pillar_returns_50_regardless_of_args() -> None:
    assert wallet_activity.is_neutral_pillar(True, Decimal("0.00")) == Decimal("50.00")
    assert wallet_activity.is_neutral_pillar(True, Decimal("100.00")) == Decimal("50.00")
    assert wallet_activity.is_neutral_pillar(True, Decimal("99.99")) == Decimal("50.00")
    assert wallet_activity.is_neutral_pillar(False, Decimal("73.21")) == Decimal("73.21")


# ---------------------------------------------------------------------------
# T5 — RED: recency_days=None → recency sub-term = 0.
# ---------------------------------------------------------------------------


def test_creator_wallet_pillar_score_zero_events_returns_zero_pillar() -> None:
    actual = wallet_activity.creator_wallet_pillar_score(0, 0, None, False)
    assert actual == Decimal("0.00"), f"expected 0.00, got {actual}"


# ---------------------------------------------------------------------------
# T6 — RED: shrinkage wash-out at n=0 (both wallets neutral).
# ---------------------------------------------------------------------------


def test_compute_wallet_activity_score_wash_out_at_n_zero() -> None:
    actual = wallet_activity.compute_wallet_activity_score(
        creator_events=0,
        creator_counterparties=0,
        creator_recency_days=None,
        creator_is_neutral=True,
        owner_events=0,
        owner_counterparties=0,
        owner_recency_days=None,
        owner_is_neutral=True,
        track_record_score=Decimal("70.00"),
    )
    assert actual == Decimal("50.00"), f"wash-out should be 50.00, got {actual}"


# ---------------------------------------------------------------------------
# T8 — TRIANGULATE: shrinkage boundaries.
# ---------------------------------------------------------------------------


def test_shrinkage_with_one_event() -> None:
    """n=1 → shrinkage fires; quantize via ROUND_HALF_EVEN.

    creator_pillar(0, 0, 30, False) = 20 (recency-only sub-term)
    owner_pillar(1, 0, None, False) = 1.67 (events=1, banker)
    raw = 0.5*20 + 0.3*1.67 + 0.2*0 = 10.501
    n = 1 → (1/4)*10.501 + (3/4)*50 = 40.125 → 40.13
    """
    actual = wallet_activity.compute_wallet_activity_score(
        creator_events=0,
        creator_counterparties=0,
        creator_recency_days=30,
        creator_is_neutral=False,
        owner_events=1,
        owner_counterparties=0,
        owner_recency_days=None,
        owner_is_neutral=False,
        track_record_score=Decimal("0.00"),
    )
    assert actual == Decimal("40.13"), f"expected 40.13, got {actual}"


def test_shrinkage_at_k_boundary_no_shrinkage() -> None:
    """n=3 → no shrinkage; raw passes through unchanged."""
    # creator_pillar = 100 (all sub-terms saturate), owner=0, track=50
    # raw = 0.5*100 + 0.3*0 + 0.2*50 = 60; n=60 ≥ 3 → no shrinkage → 60.00
    actual = wallet_activity.compute_wallet_activity_score(
        creator_events=60,
        creator_counterparties=10,
        creator_recency_days=30,
        creator_is_neutral=False,
        owner_events=0,
        owner_counterparties=0,
        owner_recency_days=None,
        owner_is_neutral=False,
        track_record_score=Decimal("50.00"),
    )
    assert actual == Decimal("60.00"), f"expected 60.00, got {actual}"


def test_compute_wallet_activity_score_quantizes_two_decimals() -> None:
    """Helper always returns a Decimal with exactly two digits after the decimal point."""
    actual = wallet_activity.compute_wallet_activity_score(
        creator_events=0,
        creator_counterparties=0,
        creator_recency_days=None,
        creator_is_neutral=True,
        owner_events=0,
        owner_counterparties=0,
        owner_recency_days=None,
        owner_is_neutral=True,
        track_record_score=Decimal("70.00"),
    )
    assert isinstance(actual, Decimal)
    assert str(actual).count(".") == 1
    decimal_part = str(actual).split(".")[1]
    assert len(decimal_part) == 2, f"expected 2 decimal places; got {decimal_part!r}"
    assert actual == Decimal("50.00")


def test_helper_quantizes_via_bankers_rounding() -> None:
    """ROUND_HALF_EVEN preserves precision on sub-`0.005` boundaries."""
    actual = wallet_activity.compute_wallet_activity_score(
        creator_events=5,
        creator_counterparties=0,
        creator_recency_days=None,
        creator_is_neutral=False,
        owner_events=5,
        owner_counterparties=0,
        owner_recency_days=None,
        owner_is_neutral=False,
        track_record_score=Decimal("50.00"),
    )
    # pillar values: 8.33, 8.33, track=50 → raw=16.664 → quantize → 16.66
    assert actual == Decimal("16.66"), f"expected 16.66, got {actual}"


def test_helper_is_deterministic_and_side_effect_free() -> None:
    """Same inputs → equal outputs; no module-level state mutation."""
    inputs = dict(
        creator_events=10,
        creator_counterparties=2,
        creator_recency_days=15,
        creator_is_neutral=False,
        owner_events=4,
        owner_counterparties=1,
        owner_recency_days=30,
        owner_is_neutral=False,
        track_record_score=Decimal("70.00"),
    )
    a = wallet_activity.compute_wallet_activity_score(**inputs)
    b = wallet_activity.compute_wallet_activity_score(**inputs)
    assert a == b


# ---------------------------------------------------------------------------
# T9 — RED: fetch_wallet_signals SQL contract.
# ---------------------------------------------------------------------------


async def _seed_agent_with_addresses(
    db: AsyncSession,
    *,
    token_id: int,
    creator_address: str | None = None,
    owner_address: str | None = None,
    activity_score: Decimal | None = None,
) -> str:
    aid = build_agent_id(BSC_CHAIN_ID, BSC_IDENTITY_REGISTRY, token_id)
    db.add(
        AgentCache(
            agent_id=aid,
            chain_id=BSC_CHAIN_ID,
            token_id=token_id,
            registry_address=BSC_IDENTITY_REGISTRY,
            name=f"Agent {token_id}",
            creator_address=creator_address,
            owner_address=owner_address,
            activity_score=activity_score,
            supported_protocols=[],
            cross_chain_versions=[],
            raw={},
            created_at=_now(),
            updated_at=_now(),
            tags=[],
            categories=[],
        )
    )
    await db.commit()
    return aid


async def _seed_transfer(
    db: AsyncSession,
    *,
    from_address: str,
    to_address: str,
    block_number: int = 100,
    tx_hash: str = "0x" + "0" * 64,
    timestamp: Any = None,
) -> OnchainTransfer:
    row = OnchainTransfer(
        from_address=from_address,
        to_address=to_address,
        value=Decimal(1),
        block_number=block_number,
        timestamp=timestamp if timestamp is not None else _now(),
        tx_hash=tx_hash,
        transfer_type="erc20_u",
    )
    db.add(row)
    await db.commit()
    return row


async def test_fetch_wallet_signals_creator_only(db: AsyncSession) -> None:
    """creator_address set, owner_address=NULL → creator has 1 event, owner is_neutral."""
    creator = "0x" + "ab" * 20
    aid = await _seed_agent_with_addresses(
        db, token_id=1, creator_address=creator, owner_address=None
    )
    await _seed_transfer(
        db, from_address="0x" + "11" * 20, to_address=creator, tx_hash="0x" + "1" * 64
    )

    signals = await wallet_activity.fetch_wallet_signals(db, aid)

    assert signals.creator.events == 1
    assert signals.creator.counterparties == 1
    assert signals.creator.is_neutral is False
    assert signals.owner.is_neutral is True
    assert signals.owner.events == 0
    assert signals.owner.recency_days is None
    assert signals.creator_is_owner is False


async def test_fetch_wallet_signals_owner_only(db: AsyncSession) -> None:
    """owner_address set, creator_address=NULL → owner has events, creator is_neutral."""
    owner = "0x" + "de" * 20
    aid = await _seed_agent_with_addresses(
        db, token_id=2, creator_address=None, owner_address=owner
    )
    await _seed_transfer(
        db, from_address="0x" + "11" * 20, to_address=owner, tx_hash="0x" + "2" * 64
    )

    signals = await wallet_activity.fetch_wallet_signals(db, aid)

    assert signals.owner.events == 1
    assert signals.owner.is_neutral is False
    assert signals.creator.is_neutral is True
    assert signals.creator_is_owner is False


async def test_fetch_wallet_signals_both_null_neutral_no_query(db: AsyncSession) -> None:
    """Both addresses NULL → (neutral, neutral, False), ZERO queries."""
    aid = await _seed_agent_with_addresses(
        db, token_id=3, creator_address=None, owner_address=None
    )

    query_count = 0

    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
        nonlocal query_count
        if "onchain_transfers" in statement.lower():
            query_count += 1

    event.listen(db.bind.sync_engine, "before_cursor_execute", _before_cursor_execute)
    try:
        signals = await wallet_activity.fetch_wallet_signals(db, aid)
    finally:
        event.remove(db.bind.sync_engine, "before_cursor_execute", _before_cursor_execute)

    assert signals.creator.is_neutral is True
    assert signals.owner.is_neutral is True
    assert signals.creator_is_owner is False
    assert query_count == 0, f"both-null should issue 0 queries; got {query_count}"


async def test_fetch_wallet_signals_lowercases_at_sql_boundary(db: AsyncSession) -> None:
    """AgentCache stores mixed-case addresses; SQL boundary normalizes to lowercase."""
    mixed = "0xAbC" + "0" * 37
    aid = await _seed_agent_with_addresses(
        db, token_id=4, creator_address=mixed, owner_address=None
    )
    await _seed_transfer(
        db, from_address="0x" + "11" * 20, to_address="0xabc" + "0" * 37, tx_hash="0x" + "4" * 64
    )

    signals = await wallet_activity.fetch_wallet_signals(db, aid)
    assert signals.creator.events == 1, (
        "mixed-case AgentCache address must match lowercase OnchainTransfer"
    )


async def test_fetch_wallet_signals_excludes_zero_address_counterparty(db: AsyncSession) -> None:
    """ZERO_ADDRESS row contributes to events but NOT to counterparties."""
    creator = "0x" + "ab" * 20
    zero = "0x" + "0" * 40
    aid = await _seed_agent_with_addresses(
        db, token_id=5, creator_address=creator, owner_address=None
    )
    await _seed_transfer(
        db, from_address=zero, to_address=creator, tx_hash="0x" + "5" * 64
    )
    await _seed_transfer(
        db,
        from_address="0x" + "11" * 20,
        to_address=creator,
        tx_hash="0x" + "6" * 64,
        block_number=200,
    )

    signals = await wallet_activity.fetch_wallet_signals(db, aid)
    assert signals.creator.events == 2
    assert signals.creator.counterparties == 1, (
        f"expected 1 counterparty (zero-address excluded); got {signals.creator.counterparties}"
    )


async def test_fetch_wallet_signals_90_day_window_excludes_older_rows(db: AsyncSession) -> None:
    """100-day-old rows are excluded; 30-day-old rows are counted."""
    creator = "0x" + "ab" * 20
    aid = await _seed_agent_with_addresses(
        db, token_id=6, creator_address=creator, owner_address=None
    )
    now = _now()
    await _seed_transfer(
        db,
        from_address="0x" + "11" * 20,
        to_address=creator,
        tx_hash="0x" + "7" * 64,
        timestamp=now - timedelta(days=100),
    )
    await _seed_transfer(
        db,
        from_address="0x" + "11" * 20,
        to_address=creator,
        tx_hash="0x" + "8" * 64,
        timestamp=now - timedelta(days=30),
        block_number=200,
    )

    signals = await wallet_activity.fetch_wallet_signals(db, aid)
    assert signals.creator.events == 1, f"only 30-day-old row counts; got {signals.creator.events}"
    assert signals.creator.recency_days is not None
    assert 28 <= signals.creator.recency_days <= 32, (
        f"recency_days should be ~30; got {signals.creator.recency_days}"
    )


# ---------------------------------------------------------------------------
# T11+T12 — RED: creator_is_owner collapses to one SQL query.
# ---------------------------------------------------------------------------


def _count_onchain_queries(db: AsyncSession):
    """Attach a listener that counts OnchainTransfer queries; returns (listener, counter)."""
    counter = {"count": 0}

    def _listener(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
        if "onchain_transfers" in statement.lower():
            counter["count"] += 1

    event.listen(db.bind.sync_engine, "before_cursor_execute", _listener)
    return _listener, counter


async def test_fetch_wallet_signals_creator_is_owner_uses_single_query(db: AsyncSession) -> None:
    """creator_address == owner_address (mixed-case) → exactly ONE OnchainTransfer query."""
    aid = await _seed_agent_with_addresses(
        db,
        token_id=7,
        creator_address="0xAbC" + "0" * 37,
        owner_address="0xabc" + "0" * 37,
    )
    listener, counter = _count_onchain_queries(db)
    try:
        signals = await wallet_activity.fetch_wallet_signals(db, aid)
    finally:
        event.remove(db.bind.sync_engine, "before_cursor_execute", listener)

    assert counter["count"] == 1, (
        f"creator_is_owner collapse should yield 1 query; got {counter['count']}"
    )
    assert signals.creator_is_owner is True


async def test_fetch_wallet_signals_different_addresses_uses_two_queries(db: AsyncSession) -> None:
    """Different addresses → TWO OnchainTransfer queries."""
    aid = await _seed_agent_with_addresses(
        db,
        token_id=8,
        creator_address="0x" + "aa" * 20,
        owner_address="0x" + "bb" * 20,
    )
    listener, counter = _count_onchain_queries(db)
    try:
        signals = await wallet_activity.fetch_wallet_signals(db, aid)
    finally:
        event.remove(db.bind.sync_engine, "before_cursor_execute", listener)

    assert counter["count"] == 2, f"expected 2 queries; got {counter['count']}"
    assert signals.creator_is_owner is False


async def test_fetch_wallet_signals_null_owner_uses_single_query(db: AsyncSession) -> None:
    """creator_address set, owner_address=NULL → ONE query (only creator)."""
    aid = await _seed_agent_with_addresses(
        db, token_id=9, creator_address="0x" + "cc" * 20, owner_address=None
    )
    listener, counter = _count_onchain_queries(db)
    try:
        signals = await wallet_activity.fetch_wallet_signals(db, aid)
    finally:
        event.remove(db.bind.sync_engine, "before_cursor_execute", listener)

    assert counter["count"] == 1, f"only creator is queried; got {counter['count']}"
    assert signals.owner.is_neutral is True
    assert signals.creator_is_owner is False
