# Proposal: wallet-activity — per-agent wallet-activity sub-score

## Change name
`wallet-activity`

## Domain
`wallet-activity` (new spec catalog entry — no prior canonical spec)

## Status
Draft

---

## Problem statement

`activity_score` and its three pillars (probe, payment, track_record) describe *what this agent's listing has done*. They ignore *who is behind the listing* — the creator wallet that minted or registered it, and the owner wallet that currently holds it. A brand-new agent with zero on-chain history on the agent itself can still have a creator/owner wallet with years of `$U` transfers, ERC-721 churn, and counterparties — material context a hiring user cannot see today, and material context the existing pillars were never designed to capture. Adding a fourth pillar would conflate two distinct signals and force a re-tune of the composite, which Phase 3 of this SDD chain already plans.

The creator wallet and the owner wallet are *not* the same signal. Creator activity answers "how seasoned is the hand that put this listing in front of us?"; owner activity answers "how active is the hand that currently controls it?". A solo registrar who is both creator and owner is one operator with one wallet — collapsing them to a single aggregation is correct (no double-counting, no spurious bonus), and is detected with `LOWER(creator) = LOWER(owner)` rather than scored.

A new wallet, or a wallet with very few events, needs to behave like the existing track-record pillar does: a Bayesian shrinkage toward the prior when evidence is thin. `k = 3` events across both wallets is the floor below which we shrink toward the neutral prior — the same shape `_track_parts` uses for `agent_track_record_score`, but lifted up one level so the *combined* wallet signal (not the per-wallet component) is the shrinkage target.

---

## Existing evidence

- `openspec/changes/wallet-activity/explore.md` §1 — surfaces enumerated:
  - `OnchainTransfer` is the only on-chain table with indexed wallet columns (`ix_onchain_transfers_from` / `ix_onchain_transfers_to`, migration `0005_onchain_index`). `OnchainAgentEvent` has no wallet index and is not viable for listing-scale wallet aggregation.
  - `AgentCache.creator_address` / `owner_address` are **nullable Text**, not lowercased at write time — `.strip().lower()` at the SQL boundary is the established convention (`app/services/compliance_refresh.py:117-118`).
  - `compute_track_record_pillar` + `fetch_track_record` (`app/services/agent_score.py`) is the exact aggregation shape and 90-day window to mirror.
  - `app/routers/onchain_stats.py::get_wallet_onchain_stats` (lines 88-139) is the live precedent for a wallet-keyed query against `OnchainTransfer`; the helper should reuse its query shape verbatim.
- `DESIGN.md` D8 — wallet-activity pillar definition, weights (0.5 / 0.3 / 0.2), shrinkage k=3, neutral-pillar fallback, `creator_is_owner` derivation.
- `openspec/specs/agent-compliance/spec.md` — recent precedent for additive score columns, decimal JSON boundary, and per-page additive chips.

---

## What needs to change

1. **Pure helper** in new `app/services/wallet_activity.py`:
   `compute_wallet_activity_score(creator_events, creator_counterparties,
   creator_recency_days, owner_events, owner_counterparties,
   owner_recency_days, track_record_score) -> Decimal`. Implements the three
   pillars from D8 with shrinkage k=3 over total events. Plus
   `compute_wallet_activity_breakdown(...)` for the JSON shape returned on the
   API. Null address ⇒ that pillar component = 50 (neutral). `recency_days=None`
   ⇒ recency sub-term = 0 (cap defaults). Both inputs and output `Decimal`.

2. **Aggregation** `fetch_wallet_signals(session, agent_id) -> WalletSignals`:
   one SQL query per wallet against `OnchainTransfer`, predicates on
   `from_address` OR `to_address`, both lowercased at the boundary, filtered
   by `timestamp >= now - TRACK_WINDOW_DAYS`. When
   `LOWER(creator) = LOWER(owner)`, collapse to a single query (no bonus, no
   penalty — recorded in `creator_is_owner`). Mirrors `fetch_track_record`'s
   aggregation triple (`COUNT(DISTINCT tx_hash)`,
   `COUNT(DISTINCT counterparties) FILTER (other != zero)`,
   `(now - MAX(timestamp)).days`).

3. **ScoreOut additive fields** in `app/schemas/score.py`: `wallet_activity_score: Decimal | None = None`,
   `wallet_activity_breakdown: dict[str, Any] | None = None`,
   `creator_is_owner: bool = False`. Existing `activity_score`, `breakdown`,
   `pillars` are untouched.

4. **Route read path** in `app/routers/agents.py::get_agent_score` — call
   `fetch_wallet_signals` + `compute_wallet_activity_score`; attach the three
   new fields. Failure of either must not break the existing payload
   (mirroring how `agent_compliance_flags` failures are swallowed).

5. **Page read path** in `app/routers/pages.py::agent_detail` — call the
   fetch + helper inside the existing `try / except` block that already reads
   `onchain_stats["transfers"]`. Pass three new template locals:
   `wallet_activity_score`, `wallet_activity_breakdown`, `creator_is_owner`.

6. **UI chip** in `app/templates/pages/agent_detail.html` — `<div
   class="wallet-activity-chip">` rendered inside the existing `#activity-score`
   section, near the Activity score card. Shows `wallet_activity_score/100`
   plus the three-pillar breakdown and `creator_is_owner` copy. Reuses
   existing CSS classes (`badge category`, `dim-bars`, `score-dimensions`).
   Does not replace the Activity card.

7. **Tests** in new `tests/test_wallet_activity.py` (pure helper truth table +
   shrinkage + null-address + recency-None), plus additive sections in
   `tests/test_score_api.py` (additive fields) and `tests/test_pages.py`
   (chip render). Strict TDD: tests written first against the locked math.

---

## Scope

### In scope

- Pure helper module `app/services/wallet_activity.py` + tests
- `fetch_wallet_signals` aggregation + tests
- `ScoreOut` additive fields
- `get_agent_score` additive response fields
- `agent_detail` page read path + template chip
- Strict TDD test coverage of the helper truth table and the boundary cases

### Out of scope

- Mutation of `activity_score`, `pillars`, or `composite_score`
- Mutation of `wallet_score` (the existing 8004scan mirror column)
- Listing-page chips (`/`, `/agents`, `/agents/{chain}`) — listing aggregation
  is a future change; this change is single-row only
- Consolidation of `wallet_activity_score` into the existing composite
  (Phase 3 of this SDD chain)
- OFAC compliance signal (`agent_compliance_flags`, `compliance_penalty`) —
  separate `compliance-flags` change
- New `agent_cache` columns, new tables, new indexes, new migrations — the
  score is fully derived from existing indexed columns

---

## Acceptance criteria

1. **Helper truth table.** For inputs (creator events, counterparties,
   recency; owner same; track_record_score) covering all combinations of
   zero / thin / mid / saturated for both wallets, the helper returns the
   exact `Decimal` value dictated by D8's formula, including the 0.5 / 0.3 /
   0.2 weighting and the `0.2 * min(1, 30/recency_days)` recency sub-term.
2. **Shrinkage.** When `creator_events + owner_events < 3`, the helper
   applies Bayesian shrinkage toward the neutral prior with k=3, exactly as
   D8 specifies. Tests pin both the shrunken and un-shrunken branches.
3. **Null addresses.** When `creator_address` is null/empty, the creator
   pillar component contributes 50 (neutral); same for owner. Helper tests
   pin these branches independently. Aggregation returns
   `WalletSignals(creator=..., owner=...)` with each pillar's raw triple
   marked "neutral" so the helper can substitute 50.
4. **`creator_is_owner` derivation.** When
   `LOWER(creator_address) = LOWER(owner_address)`, the aggregation runs a
   single SQL query (no double-counting) and `creator_is_owner` is `True` in
   the response. When the addresses differ, two queries run and
   `creator_is_owner` is `False`. Edge cases: both null ⇒ `False`;
   differing case only ⇒ `True`.
5. **Additive API fields.** `GET /api/agents/{chain}/{token}/score` returns
   `wallet_activity_score`, `wallet_activity_breakdown`, `creator_is_owner`
   on every successful response. `activity_score`, `pillars`, `breakdown`,
   `compliance_penalty`, `displayed_activity_score` are byte-identical to
   the pre-change payload. Test asserts pre/post diff against a frozen
   reference response.
6. **UI chip renders.** `agent_detail.html` shows the chip inside the
   existing `#activity-score` section for any agent that returns a score;
   the chip carries the three-pillar breakdown and the
   `creator_is_owner` copy. No CSS additions.
7. **Baseline preserved.** `activity_score` math, `track_record_score`
   pillar output, and the `compliance-flags` payload are all unchanged.
   Existing 348-pass / 8-skip test baseline remains green; new tests grow
   the count without flaking.

---

## Risks

1. **Null address handling.** Both `creator_address` and `owner_address`
   are nullable in `agent_cache` — the helper must accept null inputs and
   substitute the 50-neutral pillar. Mitigated by helper unit tests pinning
   the null branch and aggregation tests pinning the SQL boundary behaviour
   against `_seed_agent_with_addresses` fixtures.

2. **Address normalization at the boundary.** Mixed-case 8004scan addresses
   flow through `sync_worker._row_from_agent` without lowercasing; the
   `OnchainTransfer` columns themselves are always lowercase. The helper
   MUST apply `.strip().lower()` at the SQL boundary per the
   `compliance_refresh.py:117-118` precedent. Changing the sync writer is
   out of scope (touches the OFAC mirror contract).

3. **`recency_days = None`.** When `MAX(timestamp)` is NULL (no events in
   the 90-day window), the recency sub-term `0.2 * min(1, 30/recency_days)`
   would divide by zero. Fix: substitute the max denominator so the term
   evaluates to 0 (mirrors `_track_parts` at `agent_score.py:128`). Helper
   test pins the `recency_days=None` branch.

4. **Query cost.** Two indexed seeks per agent on `agent_detail` — tractable.
   Listing pages are explicitly out of scope, but a future change that adds
   the chip to listing rows would push the page-load query count to 48
   indexed seeks; flagged here so the next change plans accordingly.

5. **Decimal at the JSON boundary.** Pydantic serializes `Decimal` as JSON
   float, matching the convention `activity_score` /
   `displayed_activity_score` already follow. Tests assert float equality,
   matching the `compliance_penalty` test pattern.

6. **`OnchainTransfer` data freshness.** Wallet signals depend on the
   indexer having ingested recent blocks. `flagged_data_stale` /
   `refreshed_at` discipline from the compliance-flags change does NOT
   apply here; staleness is the existing indexer's responsibility. Operator
   runs the existing backfill command if a gap is detected.

---

## Open questions

None. All product/technical decisions are locked by D8 + the four
explore-derived decisions in the parent task. Next phase (`sdd-spec`)
formalizes the helper signature, the SQL contract, and the JSON shape.
