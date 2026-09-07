# `wallet-activity` — exploration report

Phase: `sdd-explore` for `wallet-activity`. Change dir was empty; init guard
satisfied. Test baseline: 348 passed, 8 skipped.

This is the wallet-activity pillar (`DESIGN.md` D8): a per-agent sub-score
derived from the on-chain footprint of the creator and owner wallets,
surfaced as an independent field on `GET /api/agents/{chain}/{token}/score`
and as an additive chip on `agent_detail.html`. It does NOT enter
`activity_score` (locked decision #6).

All numbered "Decisions" are LOCKED. The only open choice is **which
on-chain table to query** (A / B / C in §2).

---

## 1. Surfaces to explore

### 1.1 `app/db/models/onchain_index.py` — two index tables

- **`OnchainAgentEvent`** (`onchain_agent_events`): one row per ERC-721
  `Transfer` on `BSC_IDENTITY_REGISTRY`. Columns: `agent_id, token_id,
  event_type, from_address, to_address, block_number, timestamp, tx_hash`.
  Indexes: `ix_onchain_agent_events_agent(agent_id)`,
  `ix_onchain_agent_events_block(block_number)`. **No index on
  `from_address` / `to_address`.**
- **`OnchainTransfer`** (`onchain_transfers`): one row per indexed ERC-20
  `$U` transfer PLUS an ERC-721 NFT transfer (the `transfer_type` enum
  distinguishes them). Indexes: `ix_onchain_transfers_from(from_address)`,
  `ix_onchain_transfers_to(to_address)`,
  `ix_onchain_transfers_agent(linked_agent_id)`,
  `ix_onchain_transfers_block(block_number)`, plus
  `uq_transfer_tx(tx_hash, from_address, to_address)`.
- **Case:** `from_address` / `to_address` are written by
  `_extract_addr(log["topics"][N])` in `onchain_indexer.py:312/359`. EVM
  topics are canonical lowercase hex on the wire — the stored columns are
  **always lowercase**.

### 1.2 `app/services/agent_score.py` — existing pillar math (REUSED)

- `TRACK_WINDOW_DAYS = 90` — wallet window matches (decision #5).
- `TrackRecord(age_months, event_count, unique_buyers, recency_days)`.
- `compute_track_record_pillar(...) -> int` is the third pillar's math.
- `fetch_track_record(session, agent_id) -> TrackRecord` already runs the
  same 90-day `COUNT(DISTINCT tx_hash) + COUNT(DISTINCT to_address)
  FILTER (from ≠ zero) + MAX(timestamp)` triple the wallet-activity
  helper needs.
- `compute_probe_pillar`, `composite_score`, `build_breakdown`,
  `materialize_score` are untouched.

The wallet-activity change adds a parallel pure helper
`compute_wallet_activity_score(...) -> Decimal` (decision #1) and an
aggregation `fetch_wallet_signals(session, agent_id)`. No new window
constant.

### 1.3 `app/db/models/agent.py` — `AgentCache` (read-side join key)

- `creator_address: Mapped[str | None]` (Text, **nullable=True**, line 74).
- `owner_address: Mapped[str | None]` (Text, **nullable=True**, line 81).

**Both columns are nullable**, contrary to the prompt's "confirm non-null
and lowercase-normalized" — this is a fact the proposal MUST defend.
Neither is lowercased at write time: `sync_worker.py::_row_from_agent`
(line 263/269) passes the 8004scan EIP-55 mixed-case address straight
through. The established read-side boundary is
`compliance_refresh.py:117-118`:
`(agent.creator_address or "").strip().lower()` and the same for owner.
The new helper **MUST apply the same `.strip().lower()` at the SQL
boundary** — changing the model contract is out of scope (it would alter
the contract shared with `flagged_sync` and OFAC mirror matching).

### 1.4 `app/routers/agents.py` — `GET /api/agents/{chain}/{token}/score`

- `get_agent_score` (line ~196) already returns `activity_score`,
  `compliance_penalty`, `displayed_activity_score`, `pillars`, `breakdown`.
  The change is **additive** (decision #7): three new fields
  (`wallet_activity_score`, `wallet_activity_breakdown`,
  `creator_is_owner`) attach at the top level.
- `Pillars` is NOT extended — wallet-activity is NOT a fourth pillar.
- The wallet aggregation parallels `fetch_track_record`:
  `async def fetch_wallet_signals(session, agent_id) -> WalletSignals`.

### 1.5 `app/routers/pages.py` — `agent_detail`

- Already runs `fetch_track_record` for the Activity card (line ~786) and
  reads `agent_compliance_flags` for the OFAC gate (line ~915-958).
- The wallet aggregation is one query per agent (detail page = one agent
  at a time). It slots into the existing `try / except` block that
  already reads `onchain_stats["transfers"]` (line ~860). No new failure
  modes beyond what `onchain_stats` already swallows.

### 1.6 `app/templates/pages/agent_detail.html`

- The `#activity-score` `<section>` (line ~264) already shows
  `displayed_activity_score`, `local_breakdown`, `latest_probe`. The new
  wallet-activity chip is **additive** (decision #8): it renders inside
  the same section, carrying `{{ wallet_activity_score }}/100` plus the
  three-pillar breakdown and `creator_is_owner` copy. Existing CSS
  classes (`badge category`, `dim-bars`, `score-dimensions`) are reused;
  no stylesheet edit required.

### 1.7 `app/schemas/score.py` — `ScoreOut`

Three additive fields on `ScoreOut`:
`wallet_activity_score: Decimal | None = None`,
`wallet_activity_breakdown: dict[str, Any] | None = None`,
`creator_is_owner: bool = False`. Decimal matches the `Numeric(5, 2)`
boundary `activity_score` already uses; float at the JSON serializer
matches the `compliance_penalty` / `displayed_activity_score` precedent.

### 1.8 `migrations/versions/` — no migration

Last revision `0012_compliance_penalty` (head); next slot `0013_*`. The
score is **derived** (decision #9). No `agent_cache` column, no new
table, no index migration. Option B reuses `ix_onchain_transfers_from` /
`ix_onchain_transfers_to` (migration `0005_onchain_index.py:38-39`).

### 1.9 `tests/` — fixture pattern

- `tests/test_agent_score.py` — pure-function tests + sqlite harness
  (`_seed_event` line 174-180 inserts `OnchainAgentEvent` rows).
- `tests/test_score_api.py` — TestClient; `_seed_agent` + `_seed_events`.
- `tests/test_indexer_linking.py` — `_seed_transfer` inserts
  `OnchainTransfer` rows.
- `tests/_compliance_fixtures.py` — `_seed_agent_with_addresses(...)` for
  the nullable-address seed pattern.

New tests live in `tests/test_wallet_activity.py` (mirroring
`test_agent_score.py`'s structure) plus additive sections in
`tests/test_score_api.py` and `tests/test_pages.py`.

---

## 2. Critical implementation question — A, B, or C?

### Option A — `OnchainAgentEvent` filtered by wallet

No wallet index — only `agent_id` and `block_number`. A query
`WHERE (from_address = X OR to_address = X) AND timestamp >= cutoff`
is a full scan over the 90-day event slice. Listing-page cost
(24 agents × 2 wallets = 48 full scans) is not viable. Semantic: "wallet
has moved agent NFTs" only.

### Option B — `OnchainTransfer` filtered by wallet ✅

Index coverage complete: `ix_onchain_transfers_from(from_address)` and
`ix_onchain_transfers_to(to_address)` (migration `0005_onchain_index`).
Query plan: b-tree seek per predicate × two predicates, then 90-day
`timestamp` filter. Tractable at listing scale (96 indexed seeks /
page). Semantic: "wallet's full on-chain participation" — `$U`
transfers (the marketplace's native payment rail) plus agent-NFT
transfers. The third pillar (`agent_track_record_score`) already
captures the NFT-only signal via `OnchainAgentEvent`, so the broader
`OnchainTransfer` signal is complementary rather than duplicative.

**Precedent:** `app/routers/onchain_stats.py::get_wallet_onchain_stats`
(line 88-139) is the EXACT live precedent — wallet-keyed query against
`OnchainTransfer` with `addr = wallet_address.lower()` and
`func.lower(OnchainTransfer.from_address) == addr` /
`func.lower(OnchainTransfer.to_address) == addr`. Established, tested,
in production. The wallet-activity helper should reuse this query shape
verbatim, not invent a new one.

### Option C — hybrid, `OnchainAgentEvent` for ERC-721 only

Same index problem as A. Strict subset of B's coverage. Adds nothing
beyond the track-record pillar the locked decision already reuses.

---

## 3. Recommended query approach — **Option B**

Commit to `OnchainTransfer` filtered by indexed wallet columns:

1. **Index coverage exists.** Same query plan `onchain_stats.py` runs
   today.
2. **Semantic alignment.** Decision #2 reads
   `creator_wallet_score = 0.5*min(1, events/30) + 0.3*min(1,
   counterparties/10) + 0.2*min(1, 30/recency_days)`. "events" and
   "counterparties" are natural counts at the `OnchainTransfer` layer.
3. **No new index, no migration.** Decision #9 trivially satisfied.

**Aggregation pattern (proposal-phase contract):**

```python
async def fetch_wallet_signals(session, agent_id) -> WalletSignals:
    """One-agent wallet signal over the 90-day window."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=TRACK_WINDOW_DAYS)  # = 90

    row = await session.scalar(
        select(AgentCache.creator_address, AgentCache.owner_address)
        .where(AgentCache.agent_id == agent_id)
    )
    creator_addr = (row.creator_address or "").strip().lower()
    owner_addr = (row.owner_address or "").strip().lower()

    # One query per wallet, two predicates OR'd via the indexed
    # from/to columns. Aggregates: events = COUNT(DISTINCT tx_hash),
    # counterparties = COUNT(DISTINCT other_side) FILTER (other != zero),
    # recency_days = (now - MAX(timestamp)).days. When
    # creator_addr == owner_addr, collapse to a single query
    # (decision #4 — solo registrar, no bonus).
    ...
```

The 90-day cutoff reuses `TRACK_WINDOW_DAYS` (decision #5). The
`ZERO_ADDRESS` exclusion in `fetch_track_record` (`agent_score.py:212`)
is the precedent for excluding zero-address counterparties from
`counterparties`. The helper signature from decision #1 stays exactly
as written.

---

## 4. Summary for proposal phase

The smallest cohesive slice:

1. **Pure helper** in `app/services/wallet_activity.py`:
   `compute_wallet_activity_score(creator_events, creator_counterparties,
   creator_recency_days, owner_events, owner_counterparties,
   owner_recency_days, track_record_score) -> Decimal` — implements the
   three-pillar math from decision #2 with the Bayesian shrinkage from
   decision #3 when total events < k=3. Returns `Decimal("0.00")` when
   both addresses are null/empty. Plus a `compute_wallet_activity_breakdown(...)`
   for the JSON shape.
2. **Aggregation** `fetch_wallet_signals(session, agent_id) ->
   WalletSignals` — two SQL queries when `creator_addr != owner_addr`,
   one when they match (decision #4). Lowercases at the boundary; uses
   the indexed `OnchainTransfer` wallet columns; filters by
   `timestamp >= TRACK_WINDOW_DAYS`.
3. **Schema additions** in `app/schemas/score.py`: three additive fields
   on `ScoreOut` (decision #7).
4. **Route additions** in `app/routers/agents.py::get_agent_score`: fetch
   signals, attach to response. No materialize step (decision #9).
5. **Route additions** in `app/routers/pages.py::agent_detail`: call the
   fetch + helper inside the existing `try / except` block; pass three
   new template locals.
6. **Template additions** in `agent_detail.html`: `<div
   class="wallet-activity-chip">` inside the existing `#activity-score`
   section (decision #8).
7. **Tests** in `tests/test_wallet_activity.py` (new) plus additive
   sections in `tests/test_score_api.py` and `tests/test_pages.py`.
8. **No migration.** No `agent_cache` column, no new table, no new index.

---

## 5. Risks

1. **`creator_address` / `owner_address` are nullable, not non-null.**
   The prompt's "confirm non-null" is wrong; the proposal MUST defend
   the null case (`events=0, counterparties=0, recency_days=None` →
   `Decimal("0.00")`; UI shows `—` matching the Endpoint health row).
2. **Addresses are NOT lowercased at write time.** Mixed-case flows
   through `sync_worker._row_from_agent`. The helper MUST apply
   `.strip().lower()` at the SQL boundary per the
   `compliance_refresh.py:117-118` precedent. Changing the sync writer
   is out of scope (touches the OFAC mirror contract).
3. **Listing-page query count is N×2.** The home + `/agents` pages
   (24 agents/page) do NOT get the wallet chip in this change
   (decision #6 / prompt scope). If a future change adds it, the
   per-page query cost becomes 48 indexed seeks / page-load — acceptable
   today but worth flagging.
4. **`recency_days is None` for a brand-new wallet.** When the wallet
   has no 90-day activity, `MAX(timestamp)` is NULL. Decision #2's third
   term `0.2*min(1, 30/recency_days)` must pin this (recommendation:
   substitute the max denominator so the term becomes 0; mirrors
   `_track_parts` at `agent_score.py:128`).
5. **Pydantic `Decimal` JSON boundary floats.** Same convention as
   `activity_score`; tests assert float equality (matches the
   `compliance_penalty` / `displayed_activity_score` test pattern in
   `tests/test_score_api.py`).
6. **Test baseline assumes sqlite-harness portability.**
   `tests/conftest.py::_patch_metadata_for_sqlite` strips only
   `ix_agent_cache_average_score_desc` (postgres-only). The
   `OnchainTransfer` indexes we depend on are NOT in that list — sqlite
   will create them. Confirmed via `_seed_transfer` in
   `tests/test_indexer_linking.py:104`. No new conftest patch needed.

---

## 6. Key Learnings

1. **`OnchainTransfer` is the only existing on-chain table with wallet
   indexes.** `OnchainAgentEvent` is keyed by `agent_id` and
   `block_number` only — wallet-keyed aggregations against it require
   a full scan. This is the load-bearing fact behind Option B.
2. **`onchain_transfers.from_address` / `to_address` are stored lowercase** by `_extract_addr`. The `func.lower(...)` pattern at the SQL boundary in `onchain_stats.py` is defensive against agent-side mixed-case addresses, not the table side.
3. **`AgentCache.creator_address` and `owner_address` are nullable, not non-null**, and NOT lowercased at write time. The `.strip().lower()` at compare time is the established boundary (`compliance_refresh.py:117-118`).
4. **`fetch_track_record` is the exact aggregation shape** the
   wallet-activity fetch should mirror: same 90-day `TRACK_WINDOW_DAYS`
   window, same `COUNT(DISTINCT tx_hash) + COUNT(DISTINCT ...) +
   MAX(timestamp)` triple, same `ZERO_ADDRESS` exclusion.
5. **`app/routers/onchain_stats.py::get_wallet_onchain_stats` is the live precedent** for wallet-keyed queries against `OnchainTransfer`. The wallet-activity helper should reuse its query shape verbatim.