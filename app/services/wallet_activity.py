"""Per-agent wallet-activity sub-score (wallet-activity change).

Spec: `openspec/changes/wallet-activity/spec.md` (AC-1..AC-6).
Design: `openspec/changes/wallet-activity/design.md` §3 (pure helper) + §5 (SQL fetcher).

This module mirrors `app/services/agent_score.py`'s structure: pure math helpers
(no I/O, no module-level mutation, no logging) plus one async SQL fetcher
(`fetch_wallet_signals`). The pure helpers compose into
`compute_wallet_activity_score(...)` which the API route and the detail page
both consume.

Math contract:
  Per-pillar (per wallet):
    pillar = 0.5*min(1, events/30)
           + 0.3*min(1, counterparties/10)
           + 0.2*min(1, 30/recency_days)        # recency=None → this term = 0
  is_neutral=True → pillar = Decimal('50.00')

  Composition:
    raw = 0.50*creator + 0.30*owner + 0.20*track_record_score

  Bayesian shrinkage (k=3):
    n = creator_events + owner_events   (neutral wallets contribute 0)
    if n < 3:  score = (n/(n+3))*raw + (3/(n+3))*Decimal('50.00')
    else:      score = raw

  Both branches end with `.quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN)`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.agent import AgentCache
from app.db.models.onchain_index import OnchainTransfer
from app.services.agent_score import TRACK_WINDOW_DAYS, ZERO_ADDRESS

# ---------------------------------------------------------------------------
# Structured triple — one wallet's 90-day on-chain footprint.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WalletSignal:
    """One wallet's aggregated signals over the 90-day window.

    `is_neutral=True` indicates the wallet address was missing/whitespace-only
    on `AgentCache` and MUST be treated as a flat-50 pillar regardless of
    the numeric fields (which are zeroed).
    """

    events: int
    counterparties: int
    recency_days: int | None
    is_neutral: bool


@dataclass(frozen=True, slots=True)
class WalletSignals:
    """Both wallets' signals + the `creator_is_owner` collapse flag.

    `creator_is_owner=True` means `LOWER(creator_address) == LOWER(owner_address)`
    (both non-empty), so consumers can render the single-wallet message and the
    aggregation collapsed to one SQL query.
    """

    creator: WalletSignal
    owner: WalletSignal
    creator_is_owner: bool


def _neutral_wallet_signal() -> WalletSignal:
    """The canonical neutral placeholder for a missing address."""
    return WalletSignal(events=0, counterparties=0, recency_days=None, is_neutral=True)


# ---------------------------------------------------------------------------
# Pure pillar helpers
# ---------------------------------------------------------------------------


def is_neutral_pillar(is_neutral: bool, raw_pillar: Decimal) -> Decimal:
    """Names the 50 wash-out invariant in code (D8).

    When the wallet is neutral (address absent), the per-wallet pillar is a flat
    `Decimal('50.00')` regardless of any numeric arg. Otherwise `raw_pillar`
    passes through. Quantization happens at the per-pillar boundary so a neutral
    helper call yields the literal `Decimal('50.00')`.
    """
    if is_neutral:
        return Decimal("50.00")
    return raw_pillar


def _compute_pillar(
    events: int, counterparties: int, recency_days: int | None
) -> Decimal:
    """Per-wallet raw pillar in [0, 100] (NOT yet quantized).

    `recency_days=None` → recency sub-term = 0 (no divide-by-zero, matches
    `_track_parts` cap-defaults at `app/services/agent_score.py:128`).
    """
    events_term = Decimal(events) / Decimal(30)
    if events_term > 1:
        events_term = Decimal(1)
    cp_term = Decimal(counterparties) / Decimal(10)
    if cp_term > 1:
        cp_term = Decimal(1)
    if recency_days is None or recency_days <= 0:
        recency_term = Decimal(0)
    else:
        recency_term = Decimal(30) / Decimal(recency_days)
        if recency_term > 1:
            recency_term = Decimal(1)
    # Sum of three sub-terms is in [0, 1] — multiply by 100 so the saturated
    # pillar is exactly Decimal('100.00') (matches design §3.4 truth table).
    pillar_unit = (
        Decimal("0.5") * events_term
        + Decimal("0.3") * cp_term
        + Decimal("0.2") * recency_term
    )
    return pillar_unit * Decimal(100)


def _wallet_pillar_score(
    events: int, counterparties: int, recency_days: int | None, is_neutral: bool
) -> Decimal:
    """Per-wallet pillar (raw + neutral + quantize)."""
    raw = _compute_pillar(events, counterparties, recency_days)
    raw = is_neutral_pillar(is_neutral, raw)
    return raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def creator_wallet_pillar_score(
    events: int, counterparties: int, recency_days: int | None, is_neutral: bool
) -> Decimal:
    """Creator per-wallet pillar in [0, 100]; thin wrapper around `_wallet_pillar_score`."""
    return _wallet_pillar_score(events, counterparties, recency_days, is_neutral)


def owner_wallet_pillar_score(
    events: int, counterparties: int, recency_days: int | None, is_neutral: bool
) -> Decimal:
    """Owner per-wallet pillar in [0, 100]; thin wrapper around `_wallet_pillar_score`."""
    return _wallet_pillar_score(events, counterparties, recency_days, is_neutral)


# ---------------------------------------------------------------------------
# Top-level pure helper
# ---------------------------------------------------------------------------


def compute_wallet_activity_score(
    *,
    creator_events: int,
    creator_counterparties: int,
    creator_recency_days: int | None,
    creator_is_neutral: bool,
    owner_events: int,
    owner_counterparties: int,
    owner_recency_days: int | None,
    owner_is_neutral: bool,
    track_record_score: Decimal,
) -> Decimal:
    """Per-agent wallet-activity sub-score in [0, 100] (D8).

    Composition: `raw = 0.50*creator + 0.30*owner + 0.20*track_record_score`.
    Shrinkage: when `creator_events + owner_events < 3` (neutral wallets
    contribute 0 to n), applies Bayesian shrinkage with k=3 toward the prior
    `Decimal('50.00')`. Quantizes via `ROUND_HALF_EVEN`.
    """
    creator_pillar = _wallet_pillar_score(
        creator_events, creator_counterparties, creator_recency_days, creator_is_neutral
    )
    owner_pillar = _wallet_pillar_score(
        owner_events, owner_counterparties, owner_recency_days, owner_is_neutral
    )

    raw = (
        Decimal("0.50") * creator_pillar
        + Decimal("0.30") * owner_pillar
        + Decimal("0.20") * Decimal(track_record_score)
    )

    # Neutral wallets contribute 0 to n (their events are by definition 0).
    n = int(creator_events) + int(owner_events)
    if n < 3:
        n_dec = Decimal(n)
        score = (n_dec / (n_dec + Decimal(3))) * raw + (Decimal(3) / (n_dec + Decimal(3))) * Decimal("50.00")
    else:
        score = raw

    return score.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def compute_wallet_activity_breakdown(
    signals: WalletSignals, track_record_score: Decimal
) -> dict[str, int]:
    """Per-pillar breakdown dict for the JSON shape `{"creator", "owner", "track_record"}`.

    Pillar int conversion uses Python `int()` (truncation); the chip's `%.2f`
    re-formats to two decimals at render time.
    """
    return {
        "creator": int(
            creator_wallet_pillar_score(
                signals.creator.events,
                signals.creator.counterparties,
                signals.creator.recency_days,
                signals.creator.is_neutral,
            )
        ),
        "owner": int(
            owner_wallet_pillar_score(
                signals.owner.events,
                signals.owner.counterparties,
                signals.owner.recency_days,
                signals.owner.is_neutral,
            )
        ),
        "track_record": int(Decimal(track_record_score)),
    }


# ---------------------------------------------------------------------------
# SQL fetcher — `fetch_wallet_signals(session, agent_id)`
# ---------------------------------------------------------------------------


async def _query_wallet(
    session: AsyncSession, addr: str, cutoff: datetime, now: datetime
) -> WalletSignal:
    """Aggregate one wallet's 90-day on-chain footprint.

    Mirrors `app/routers/onchain_stats.py::get_wallet_onchain_stats` (the live
    wallet-keyed query precedent). Predicates use `func.lower(...) == addr` so
    the OnchainTransfer columns (which are always lowercase after indexer
    insertion) match AgentCache addresses regardless of stored case.

    `events` counts every matching tx (including mints FROM the zero address).
    `counterparties` excludes the zero address on both sides (a mint has no real
    counterparty). The single `case()` form picks `to_address` when the sender
    is zero (i.e. it's a mint to our wallet — the only other party is the
    `to_address`, which IS the wallet itself, so it gets dropped), and the
    `from_address` otherwise; the `!= ZERO_ADDRESS` filter on `from` and `to`
    then excludes mints entirely from the counterparty set (because their only
    other side is the wallet itself, which still gets matched). The combined
    effect is: zero-address counterparts are excluded, real counterparties
    are counted once each via DISTINCT.
    """
    row = (
        await session.execute(
            select(
                func.count(func.distinct(OnchainTransfer.tx_hash)).label("events"),
                # Counterparties: DISTINCT set of the non-zero counterparty side.
                # A mint FROM zero TO our-wallet: case picks `to` (= our wallet).
                # A real transfer: case picks `from` (= real counterparty).
                # The `.filter(NOT from == zero AND NOT to == zero)` then drops
                # mint rows where the "counterparty" resolves to our own wallet.
                func.count(
                    func.distinct(
                        case(
                            (
                                OnchainTransfer.from_address == ZERO_ADDRESS,
                                OnchainTransfer.to_address,
                            ),
                            else_=OnchainTransfer.from_address,
                        )
                    )
                )
                .filter(
                    OnchainTransfer.from_address != ZERO_ADDRESS,
                    OnchainTransfer.to_address != ZERO_ADDRESS,
                )
                .label("counterparties"),
                func.max(OnchainTransfer.timestamp).label("latest"),
            ).where(
                or_(
                    func.lower(OnchainTransfer.from_address) == addr,
                    func.lower(OnchainTransfer.to_address) == addr,
                ),
                OnchainTransfer.timestamp >= cutoff,
            )
        )
    ).one()

    latest = row.latest
    recency_days: int | None = None
    if latest is not None:
        recency_days = max(0, (now - latest).days)

    return WalletSignal(
        events=int(row.events or 0),
        counterparties=int(row.counterparties or 0),
        recency_days=recency_days,
        is_neutral=False,
    )


async def fetch_wallet_signals(session: AsyncSession, agent_id: str) -> WalletSignals:
    """Aggregate creator + owner wallet signals for one agent.

    Query-count invariants (D8 §5.2):
      - Both addresses null/empty → 0 queries, both neutral.
      - creator_is_owner (LOWER equal) → 1 query (shared WalletSignal).
      - Otherwise → 2 queries (1 per non-null address).

    Address normalization: `.strip().lower()` at the SQL boundary (verbatim
    precedent `app/services/compliance_refresh.py:117-118`). The persisted
    AgentCache columns are NOT mutated.
    """
    row = await session.execute(
        select(AgentCache.creator_address, AgentCache.owner_address).where(
            AgentCache.agent_id == agent_id
        )
    )
    fetched = row.first()
    creator_addr_raw = fetched[0] if fetched is not None else None
    owner_addr_raw = fetched[1] if fetched is not None else None
    creator_addr = (creator_addr_raw or "").strip().lower()
    owner_addr = (owner_addr_raw or "").strip().lower()

    creator_is_owner = bool(creator_addr) and bool(owner_addr) and creator_addr == owner_addr

    if not creator_addr and not owner_addr:
        return WalletSignals(
            creator=_neutral_wallet_signal(),
            owner=_neutral_wallet_signal(),
            creator_is_owner=False,
        )

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=TRACK_WINDOW_DAYS)

    if creator_is_owner:
        sig = await _query_wallet(session, creator_addr, cutoff, now)
        return WalletSignals(creator=sig, owner=sig, creator_is_owner=True)

    # Two distinct addresses (or one null) → 1 or 2 queries.
    creator_sig: WalletSignal
    owner_sig: WalletSignal
    if creator_addr and owner_addr:
        creator_sig = await _query_wallet(session, creator_addr, cutoff, now)
        owner_sig = await _query_wallet(session, owner_addr, cutoff, now)
    elif creator_addr:
        creator_sig = await _query_wallet(session, creator_addr, cutoff, now)
        owner_sig = _neutral_wallet_signal()
    else:
        # owner_addr only (creator null)
        creator_sig = _neutral_wallet_signal()
        owner_sig = await _query_wallet(session, owner_addr, cutoff, now)
    return WalletSignals(creator=creator_sig, owner=owner_sig, creator_is_owner=False)


__all__ = [
    "TRACK_WINDOW_DAYS",
    "WalletSignal",
    "WalletSignals",
    "ZERO_ADDRESS",
    "compute_wallet_activity_breakdown",
    "compute_wallet_activity_score",
    "creator_wallet_pillar_score",
    "fetch_wallet_signals",
    "is_neutral_pillar",
    "owner_wallet_pillar_score",
]
