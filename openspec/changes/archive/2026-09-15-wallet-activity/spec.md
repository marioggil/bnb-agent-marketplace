# Wallet Activity — per-agent on-chain sub-score

**Change:** `wallet-activity`
**Domain:** `wallet-activity` (new spec catalog entry — no prior canonical spec)
**Scope:** Pure helper + per-agent aggregation + additive API fields + additive UI chip on the agent detail page. **NO** mutation of `activity_score`, no scheduler, no listing-page rollout, no OFAC coverage.
**Output path note:** This spec is written flat at `openspec/changes/wallet-activity/spec.md` per the parent task's explicit output path, instead of the nested `specs/{domain}/spec.md` layout. Archive will treat this as a full new domain spec and copy it verbatim to `openspec/specs/wallet-activity/spec.md`. No canonical spec for `wallet-activity` exists today — this is a new catalog entry created on archive.

---

## Purpose

The agent detail page surfaces three pillars today — probe, payment, and track_record — composited into `activity_score`. None of those pillars answer "who is behind this listing?". A brand-new agent with zero on-chain history on the agent itself can still have a creator or owner wallet with years of `$U` transfers, NFT churn, and counterparties — context that a hiring user cannot see today, and context the existing pillars were never designed to capture.

This change adds a fourth signal, `wallet_activity_score`, that lives **outside** the composite. It is derived from the on-chain footprint of the agent's `creator_address` and `owner_address` over a 90-day window that matches `TRACK_WINDOW_DAYS`. The score is exposed as an additive field on `GET /api/agents/{chain}/{token}/score` and rendered as an additive chip on the agent detail page. **The score never enters `activity_score` or `wallet_score`** — the `materialize_score()` write path is untouched. Future phases of this SDD chain can fold the signal into the composite once it has stabilized; this slice isolates it so existing scores stay canonical and the data model stays migration-free.

The math is a per-wallet pillar that scores activity (event count), breadth (counterparty count), and recency (last-event age), then a weighted sum (0.5 creator + 0.3 owner + 0.2 track_record), then Bayesian shrinkage toward 50 when total non-neutral events are below k=3. A neutral wallet (address absent) contributes a flat 50 — the same value the shrinkage prior uses — so a wallet that simply isn't recorded contributes neither a bonus nor a penalty. The per-wallet `is_neutral` flag is part of the helper signature so the helper can distinguish a missing address from a real wallet with zero events; the aggregation layer is responsible for setting the flag based on whether `AgentCache.creator_address` / `.owner_address` is non-null at the SQL boundary.

A solo registrar — creator and owner resolve to the same EVM address — collapses to a single SQL query. Detected via `LOWER(creator_address) = LOWER(owner_address)`, this optimization is correctness-preserving (no double-counting) and surfaces in the response as `creator_is_owner: true` so consumers can render a single-wallet message instead of two. The aggregation returns a structured per-wallet triple (`events, counterparties, recency_days, is_neutral`) rather than scalar args precisely because the collapse-to-one-query decision depends on aggregation-level knowledge the helper cannot recover.

---

## Non-Goals

- **No mutation of `activity_score`, `pillars`, `composite_score`, or `wallet_score`.** `wallet_activity_score` is a parallel, additive signal. The 8004scan mirror column stays canonical. `materialize_score()` in `app/services/agent_score.py` is untouched.
- **No scheduler / cron / APScheduler / Celery beat / n8n hook.** Phase 1 is read-side at request time. Per `DESIGN.md` D8 the nightly cron is deferred.
- **No listing-page integration.** Home page (`/`), `/agents`, and `/agents/{chain}` index pages MUST NOT render the chip in this change. Listing aggregation is a future change — explicitly out of scope here so the 48-indexed-seek query-count concern stays flagged but unaddressed. A negative assertion in `tests/test_pages.py` is the load-bearing guard.
- **No heuristic / cluster / prefix / suffix address matching.** Sanctions-style match logic lives in the `compliance-flags` chain. The wallet-activity change does NOT consult `flagged_addresses` and does NOT compute any `compliance_penalty`.
- **No OFAC banner changes.** The `agent_compliance_flags`-driven OFAC banners near the Activity score card stay byte-identical. The wallet-activity chip slots in alongside, never replacing, the compliance-area copy.
- **No `agent_cache` columns, no new tables, no new indexes, no new migrations.** The score is fully derived from existing indexed columns (`OnchainTransfer.from_address` / `.to_address` already carry `ix_onchain_transfers_from` / `ix_onchain_transfers_to` from migration `0005_onchain_index`).
- **No Phase 3 composite integration.** Rolling `wallet_activity_score` into the activity composite is the explicit Phase 3 of this SDD chain and is NOT done here.
- **No `agent_wallet` coverage.** The payment wallet is excluded from this slice; only `creator_address` and `owner_address` participate.

---

## Acceptance Criteria

All MUST hold after the change is applied:

1. **AC-1 — Helper returns `Decimal` quantized to 2 decimal places via `ROUND_HALF_EVEN`.** `compute_wallet_activity_score(...)` returns a `Decimal` whose string form has exactly two digits after the decimal point regardless of inputs. The quantization uses `ROUND_HALF_EVEN` (banker's rounding) to match the `Numeric(5, 2)` Postgres convention that `activity_score` / `wallet_score` already follow. Helper tests assert literal `Decimal` equality (no `pytest.approx`).
2. **AC-2 — Pillar truth table (parametrized).** For the per-wallet envelope `(events ∈ {0, 15, 30, 60}, counterparties ∈ {0, 5, 10, 20}, recency ∈ {None, 15, 30, 60}, is_neutral=False)` plus the neutral envelope `(anything, anything, anything, is_neutral=True)`, the helper returns the exact `Decimal` dictated by `0.5*min(1, events/30) + 0.3*min(1, counterparties/10) + 0.2*min(1, 30/recency_days)` per non-neutral pillar, then `raw = 0.50*creator + 0.30*owner + 0.20*track_record`. When all three sub-terms saturate at 1.0, the per-pillar score is exactly `Decimal("100.00")`. Pure-function tests in `tests/test_wallet_activity.py`.
3. **AC-3 — Neutral pillar = 50 when address is null.** When `creator_is_neutral=True`, the creator pillar component MUST equal `Decimal("50.00")` regardless of any other creator arg. Same for owner. The helper accepts the per-wallet `is_neutral` flag in its signature so it can distinguish a missing address from a real wallet with zero events. Helper tests pin the neutral branch independently for each wallet.
4. **AC-4 — Shrinkage k=3 with neutral wallets excluded from `n`.** When `creator_events + owner_events < 3` (neutral wallets contribute 0 to `n`), the helper applies Bayesian shrinkage: `wallet_activity = (n/(n+3)) * raw + (3/(n+3)) * Decimal("50.00")`. When `n >= 3`, `wallet_activity = raw`. Both paths end with `.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)`. Tests pin both the shrunken and un-shrunken branches, including the `n=0` (both addresses null) edge case where the result collapses to `Decimal("50.00")` regardless of `track_record_score`.
5. **AC-5 — `creator_is_owner` correctly derived.** `creator_is_owner = (LOWER(creator_address) = LOWER(owner_address))` is `True` when both addresses are non-null AND their lowercased strings match exactly. False cases: at least one is null, or they differ in any character. When `creator_is_owner=True`, the aggregation issues exactly one SQL query (no double-counting) and surfaces `creator_is_owner: true` in the API and UI responses. When `False`, two queries run (one per non-null wallet) and `creator_is_owner: false`.
6. **AC-6 — 90-day window matches `TRACK_WINDOW_DAYS`.** `fetch_wallet_signals(session, agent_id)` filters `OnchainTransfer` rows by `timestamp >= now - TRACK_WINDOW_DAYS` (= 90 days). The constant is reused, not redeclared. When the timestamp filter excludes a row, that row MUST NOT contribute to events, counterparties, or recency for either wallet.
7. **AC-7 — `ScoreOut` additive fields; existing fields byte-identical (fixture-driven).** `GET /api/agents/{chain}/{token}/score` returns `wallet_activity_score: float | None`, `wallet_activity_breakdown: dict[str, Any] | None`, `creator_is_owner: bool` on every successful response. Existing `activity_score`, `chain`, `token`, `pillars`, `breakdown`, `compliance_penalty`, `displayed_activity_score` are byte-identical to the pre-change payload — verified by a frozen reference response fixture captured before the change is applied.
8. **AC-8 — UI chip renders with breakdown; `creator == owner` label when applicable.** `agent_detail.html` renders a chip inside the existing `#activity-score` section carrying the format `"Wallet activity: NN/100 (creator NN, owner NN, track NN)"`. When `creator_is_owner=True`, the chip carries an additional `creator == owner` label. The chip MUST NOT replace the Activity score card, MUST NOT touch the OFAC banner area, and MUST NOT introduce new CSS classes.
9. **AC-9 — Full pytest baseline preserved at 348 passed.** `uv run pytest` reports `0 failed`, `>=348 passed`, `8 skipped` (the same Postgres-only tests, unchanged). New tests grow the passing count; no previously-passing test regresses.

---

## Requirements

### Requirement: Pure helper — `compute_wallet_activity_score` with per-wallet `is_neutral` flags

The function `compute_wallet_activity_score(creator_events, creator_counterparties, creator_recency_days, creator_is_neutral, owner_events, owner_counterparties, owner_recency_days, owner_is_neutral, track_record_score) -> Decimal` MUST live in a new `app/services/wallet_activity.py` module. It MUST be pure (no I/O, no module-level mutation, no logging). All numeric inputs are `int`, all booleans are `bool`, `track_record_score` is `Decimal` in `[0, 100]`. The per-wallet `is_neutral` flag MUST be present in the signature so the helper can distinguish a missing address from a real wallet with zero events.

Per-pillar math, for each non-neutral wallet:

```
pillar = 0.5 * min(1, events / 30) + 0.3 * min(1, counterparties / 10) + 0.2 * min(1, 30 / recency_days)
```

When `is_neutral=True`, the per-wallet pillar MUST equal `Decimal("50.00")`. When `recency_days=None`, the recency sub-term MUST be 0 (no divide-by-zero; the implementation pins a max denominator so the term evaluates to 0).

Composition: `raw = Decimal("0.50") * creator_pillar + Decimal("0.30") * owner_pillar + Decimal("0.20") * track_record_score`, where each per-pillar value is a `Decimal` in `[0, 100]`.

Shrinkage: `n = creator_events + owner_events` (neutral wallets contribute 0 to `n`). When `n < 3` (k=3): `wallet_activity = (n / (n + 3)) * raw + (3 / (n + 3)) * Decimal("50.00")`. When `n >= 3`: `wallet_activity = raw`. Both paths end with `.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)`.

The return type is `Decimal`. Tests assert literal `Decimal` equality.

#### Scenario: per-wallet non-neutral pillar saturates at 100

- GIVEN `creator_events=60, creator_counterparties=20, creator_recency_days=15, creator_is_neutral=False` (all sub-terms ≥ 1.0)
- AND `owner_events=30, owner_counterparties=10, owner_recency_days=30, owner_is_neutral=False`
- AND `track_record_score=Decimal("80.00")`
- WHEN the helper is invoked
- THEN the creator pillar MUST equal `Decimal("100.00")` exactly (not approximately)
- AND the owner pillar MUST equal `Decimal("100.00")`
- AND raw = 0.50*100 + 0.30*100 + 0.20*80 = 50 + 30 + 16 = 96
- AND n = 90 ≥ 3, so no shrinkage
- AND the final result MUST equal `Decimal("96.00")`

#### Scenario: per-wallet non-neutral pillar at zero (recency=None handled safely)

- GIVEN `creator_events=0, creator_counterparties=0, creator_recency_days=None, creator_is_neutral=False`
- AND `owner_events=0, owner_counterparties=0, owner_recency_days=None, owner_is_neutral=False`
- AND `track_record_score=Decimal("70.00")`
- WHEN the helper is invoked
- THEN the creator pillar MUST equal `Decimal("0.00")` (recency None → recency sub-term = 0; events 0 → events sub-term = 0; counterparties 0 → counterparties sub-term = 0; no divide-by-zero)
- AND the owner pillar MUST equal `Decimal("0.00")`
- AND raw = 0.50*0 + 0.30*0 + 0.20*70 = 14
- AND n = 0 < 3, so shrinkage fires
- AND the final result MUST equal `(0/3)*14 + (3/3)*50 = Decimal("50.00")` (track_record washes out at zero evidence)

#### Scenario: neutral creator wallet overrides pillar to 50

- GIVEN `creator_is_neutral=True, creator_events=15, creator_counterparties=8, creator_recency_days=20`
- AND `owner_events=10, owner_counterparties=4, owner_recency_days=45, owner_is_neutral=False`
- AND `track_record_score=Decimal("60.00")`
- WHEN the helper is invoked
- THEN the creator pillar MUST equal `Decimal("50.00")` regardless of the events / counterparties / recency values (the `is_neutral` flag wins)
- AND the owner pillar MUST equal its computed value (NOT 50, because owner is not neutral)
- AND n = 0 + 10 = 10 ≥ 3, so no shrinkage
- AND the final result MUST quantize to 2 decimal places via `ROUND_HALF_EVEN`

#### Scenario: rounding uses banker's rounding (ROUND_HALF_EVEN)

- GIVEN inputs whose exact arithmetic result lands on `.5` in the third decimal place (e.g. a raw composition where `0.50*pillar1 + 0.30*pillar2 + 0.20*pillar3` evaluates to `12.345`)
- WHEN the helper quantizes the result to 2 decimal places
- THEN the quantization MUST use `ROUND_HALF_EVEN`, so `12.345 → Decimal("12.34")` (round-to-even) NOT `Decimal("12.35")`
- AND the returned value MUST be a `Decimal` whose string form has exactly two digits after the decimal point

#### Scenario: helper is deterministic and side-effect-free

- GIVEN the same inputs
- WHEN the helper is invoked twice
- THEN both invocations MUST return equal `Decimal` values
- AND the helper MUST NOT read or write any module-level state, MUST NOT perform I/O, and MUST NOT log

---

### Requirement: Aggregation — `fetch_wallet_signals` returns a structured per-wallet triple

The function `async def fetch_wallet_signals(session, agent_id) -> WalletSignals` MUST query `OnchainTransfer` (the only on-chain table with indexed wallet columns — see explore §2 Option B) and return a structured object that carries `(events, counterparties, recency_days, is_neutral)` for each of `creator` and `owner`. Returning a structured per-wallet triple (rather than scalar args) is required because the `creator_is_owner` collapse-to-one-query optimization depends on aggregation-level knowledge.

The aggregation MUST:

1. Read `AgentCache.creator_address` and `AgentCache.owner_address` for `agent_id`.
2. Normalize both via `.strip().lower()` at the SQL boundary per the `app/services/compliance_refresh.py:117-118` precedent. The persisted columns are NOT mutated; `AgentCache.creator_address` and `.owner_address` are NOT lowercased at write time.
3. When both addresses are non-null AND `LOWER(creator) == LOWER(owner)`, issue exactly one SQL query whose predicates cover both addresses (no double-counting).
4. When addresses differ (or one is null), issue one SQL query per non-null wallet address.
5. For each query, predicates are `func.lower(OnchainTransfer.from_address) == addr OR func.lower(OnchainTransfer.to_address) == addr`, filtered by `timestamp >= now - TRACK_WINDOW_DAYS` (= 90 days). Aggregates: `events = COUNT(DISTINCT tx_hash)`, `counterparties = COUNT(DISTINCT other_side) FILTER (other_side != ZERO_ADDRESS)`, `recency_days = (now - MAX(timestamp)).days if MAX(timestamp) else None`.
6. Return `WalletSignals(creator=WalletSignal(events, counterparties, recency_days, is_neutral), owner=WalletSignal(..., is_neutral), creator_is_owner=bool)`. When an address is null / empty / whitespace-only, the corresponding `WalletSignal` MUST have `is_neutral=True` and `events=counterparties=0, recency_days=None`.

The query shape MUST mirror `app/routers/onchain_stats.py::get_wallet_onchain_stats` (the live wallet-keyed query precedent) verbatim.

#### Scenario: `creator_is_owner` collapses to a single query

- GIVEN an `AgentCache` row with `creator_address="0xAbC…"` and `owner_address="0xabc…"` (mixed-case vs lowercase, same address)
- WHEN `fetch_wallet_signals(session, agent_id)` runs
- THEN exactly ONE SQL query MUST be issued against `OnchainTransfer` (verified via a SQLAlchemy event listener / query counter in the test)
- AND the returned `creator_is_owner` MUST be `True`
- AND both the `creator` and `owner` `WalletSignal` objects MUST carry the same events / counterparties / recency values

#### Scenario: differing addresses issue two queries

- GIVEN an `AgentCache` row with `creator_address="0xaaa…"` and `owner_address="0xbbb…"`
- WHEN `fetch_wallet_signals(session, agent_id)` runs
- THEN exactly TWO SQL queries MUST be issued (one per wallet)
- AND `creator_is_owner` MUST be `False`
- AND the two `WalletSignal` objects MAY carry different events / counterparties / recency values

#### Scenario: null address sets `is_neutral=True`

- GIVEN an `AgentCache` row with `creator_address="0xabc…"` and `owner_address=NULL`
- WHEN `fetch_wallet_signals(session, agent_id)` runs
- THEN `owner.is_neutral` MUST be `True`
- AND `owner.events=0, owner.counterparties=0, owner.recency_days=None`
- AND exactly one SQL query MUST be issued (only for the non-null creator)
- AND `creator_is_owner` MUST be `False` (a null owner never matches a non-null creator)

#### Scenario: both addresses null → both neutral, `creator_is_owner=False`

- GIVEN an `AgentCache` row with `creator_address=NULL` and `owner_address=NULL`
- WHEN `fetch_wallet_signals(session, agent_id)` runs
- THEN `creator.is_neutral` MUST be `True` AND `owner.is_neutral` MUST be `True`
- AND `creator_is_owner` MUST be `False` (both-null is NOT a match)
- AND ZERO SQL queries MUST be issued against `OnchainTransfer` (nothing to look up)

#### Scenario: 90-day window excludes older transfers

- GIVEN an `OnchainTransfer` row dated 100 days in the past and an `OnchainTransfer` row dated 30 days in the past, both involving `creator_address`
- WHEN `fetch_wallet_signals(session, agent_id)` runs
- THEN `creator.events` MUST count only the 30-day-old row (= 1)
- AND `creator.recency_days` MUST reflect the 30-day-old row, NOT the 100-day-old row
- AND the 100-day-old row MUST NOT contribute to events, counterparties, or recency

#### Scenario: ZERO_ADDRESS excluded from counterparties

- GIVEN an `OnchainTransfer` row with `to_address=ZERO_ADDRESS` and another with `to_address="0xreal…"` (both in the 90-day window, both involving `creator_address`)
- WHEN `fetch_wallet_signals(session, agent_id)` runs
- THEN the ZERO_ADDRESS row MUST NOT contribute to `counterparties`
- AND the `0xreal…` row MUST contribute to `counterparties`

---

### Requirement: `ScoreOut` additive contract — three new fields, existing fields byte-identical

`app/schemas/score.py::ScoreOut` MUST be extended with three additive fields:

- `wallet_activity_score: float | None = None` — `None` when aggregation fails (mirrors the swallow-and-render pattern for `compliance_penalty` failures; on the happy path a value in `[0.00, 100.00]` with two-decimal precision).
- `wallet_activity_breakdown: dict[str, Any] | None = None` — shape `{"creator": int, "owner": int, "track_record": int}`, each in `[0, 100]`.
- `creator_is_owner: bool = False`.

Existing fields (`activity_score`, `chain`, `token`, `pillars`, `breakdown`, `compliance_penalty`, `displayed_activity_score`) MUST NOT be renamed, removed, or have their semantics altered. Field order in the JSON serializer MUST stay identical for the existing keys (verified by an ordered-key list fixture).

A fixture-driven byte-identical assertion: the pre-change response for a stable agent (frozen at `tests/_fixtures/score_response_pre_wallet_activity.json` or equivalent) MUST match the post-change response for all existing keys (key set, value, and JSON key order). Only the three new additive keys MAY differ from the frozen reference (they are absent in the pre-change fixture).

#### Scenario: response includes the three new additive keys

- GIVEN a working DB with a seeded agent and a successful wallet aggregation
- WHEN `GET /api/agents/{chain}/{token}/score` is called
- THEN the JSON body MUST include `wallet_activity_score` as a float in `[0.0, 100.0]` with two-decimal precision
- AND MUST include `wallet_activity_breakdown` as a dict with keys `creator`, `owner`, `track_record`
- AND MUST include `creator_is_owner` as a boolean

#### Scenario: existing keys are byte-identical to the frozen reference

- GIVEN the frozen reference fixture (captured BEFORE the change is applied) holding the exact pre-change response for a stable agent
- WHEN the post-change `/score` response is captured for the same agent
- THEN the existing keys MUST match the frozen reference exactly (key set, value, and JSON key order)
- AND only the three new additive keys MAY differ from the reference (they are absent in the pre-change fixture)
- AND the test assertion MUST fail loudly if any existing key changes value, key set, or order

#### Scenario: aggregation failure does not break the response

- GIVEN `fetch_wallet_signals(session, agent_id)` raises (DB unreachable, query timeout, etc.)
- WHEN `GET /api/agents/{chain}/{token}/score` is called
- THEN the response MUST still return successfully with `activity_score`, `pillars`, `breakdown`, `compliance_penalty`, `displayed_activity_score` populated as before
- AND `wallet_activity_score` MUST be `null`
- AND `wallet_activity_breakdown` MUST be `null`
- AND `creator_is_owner` MUST be `false` (the safe default)

---

### Requirement: Detail page read path — `agent_detail` calls the fetch + helper inside the existing try/except

`app/routers/pages.py::agent_detail(request, chain_id, token_id)` MUST call `fetch_wallet_signals(session, agent_id)` and `compute_wallet_activity_score(...)` inside the existing `try / except` block that already reads `onchain_stats["transfers"]`. The wallet signals MUST NOT trigger a new exception type that breaks the page; failure MUST be swallowed identically to how `onchain_stats` already swallows transfer-fetch errors.

The route MUST pass three new template locals: `wallet_activity_score`, `wallet_activity_breakdown`, `creator_is_owner`. The detail page MUST NOT do any aggregation beyond calling the helper (no second query, no recomposition).

#### Scenario: detail page renders the wallet chip when aggregation succeeds

- GIVEN a seeded `AgentCache` at token id 1 (chain 56) with both `creator_address` and `owner_address` populated
- AND `fetch_wallet_signals` returns non-neutral signals for both wallets
- WHEN `GET /agents/56/1` is rendered
- THEN the response body MUST contain the substring `Wallet activity:`
- AND the body MUST contain the per-pillar breakdown labels `creator`, `owner`, `track_record`
- AND the body MUST NOT contain the OFAC-blocking banner string (this change does NOT introduce OFAC behavior)

#### Scenario: detail page renders gracefully when aggregation fails

- GIVEN `fetch_wallet_signals` raises (mirroring the existing `onchain_stats` swallow)
- WHEN `GET /agents/56/1` is rendered
- THEN the response body MUST still render the Activity score card, the OFAC banner area (if any), and all other existing sections byte-identically
- AND the wallet chip MUST render with a sensible fallback (e.g. `Wallet activity: —/100`) or be omitted entirely — both are acceptable as long as the rest of the page is unaffected

---

### Requirement: UI chip renders inside `#activity-score` with breakdown + `creator == owner` label

`app/templates/pages/agent_detail.html` MUST render a `<div class="wallet-activity-chip">` inside the existing `#activity-score` section, near (but not replacing) the Activity score card. The chip MUST carry:

- The header text `"Wallet activity: NN/100"` where `NN` is `wallet_activity_score` formatted as `%.2f` (or `n/a` / `—` when the value is `null`).
- The per-pillar breakdown in parens: `(creator NN, owner NN, track NN)` with each `NN` formatted as `%.2f`.
- When `creator_is_owner=True`, an additional label `"creator == owner"`.

The chip MUST NOT replace the Activity score card, MUST NOT touch the OFAC banner area, and MUST NOT introduce new CSS classes (existing `badge category` / `dim-bars` / `score-dimensions` are reused). The `#hire-cta` button and its `disabled` state are untouched.

#### Scenario: chip renders with full breakdown

- GIVEN `wallet_activity_score=72.50`, `wallet_activity_breakdown={"creator": 80, "owner": 65, "track_record": 70}`, `creator_is_owner=False`
- WHEN `GET /agents/56/1` is rendered
- THEN the response body MUST contain the substring `Wallet activity: 72.50/100`
- AND the body MUST contain the three breakdown labels `creator`, `owner`, `track_record` with their three numeric values
- AND the body MUST NOT contain the substring `creator == owner`

#### Scenario: chip renders the `creator == owner` label when applicable

- GIVEN `wallet_activity_score=80.00`, `wallet_activity_breakdown={"creator": 80, "owner": 80, "track_record": 80}`, `creator_is_owner=True`
- WHEN `GET /agents/56/1` is rendered
- THEN the response body MUST contain the substring `creator == owner`
- AND the response body MUST still carry the Wallet activity header and the breakdown

#### Scenario: chip falls back gracefully on `null` score

- GIVEN `wallet_activity_score=None` (aggregation failed)
- WHEN `GET /agents/56/1` is rendered
- THEN the response body MUST render the chip with `Wallet activity: n/a/100` (or `—/100`) — never a stack trace, never a broken template
- AND the rest of the page MUST still render correctly

#### Scenario: OFAC banner area is untouched

- GIVEN an agent with `creator_flagged=true AND owner_flagged=true` (compliance-flags precedent)
- WHEN `GET /agents/56/1` is rendered
- THEN the response body MUST still contain the OFAC-blocking banner string `Hiring is disabled while OFAC compliance is unresolved for this agent`
- AND the body MUST still have `disabled` on `#hire-cta`
- AND the wallet-activity chip MUST render alongside, NOT replacing, the OFAC banner

---

### Requirement: Listing pages MUST stay wallet-chip-free

The home page (`/`), `/agents`, and `/agents/{chain}` index pages MUST NOT render the wallet-activity chip in this change. The chip is detail-page-only. This invariant is encoded as a negative assertion in `tests/test_pages.py` so a future change that wants to roll the chip out to listings cannot silently reintroduce the 48-indexed-seek query-count problem without an explicit, reviewed change.

#### Scenario: home page does not render the chip

- GIVEN a working DB with seeded agents
- WHEN `GET /` is rendered
- THEN the response body MUST NOT contain the substring `Wallet activity:`

#### Scenario: `/agents` index does not render the chip

- GIVEN a working DB with seeded agents
- WHEN `GET /agents` is rendered
- THEN the response body MUST NOT contain the substring `Wallet activity:`

#### Scenario: per-page query count on listings is not regressed

- GIVEN a working DB with seeded agents on the index page
- WHEN `GET /agents` is rendered with a SQLAlchemy event listener that counts `OnchainTransfer` queries
- THEN the query count MUST NOT include wallet-keyed queries against `OnchainTransfer` (the chip is not on this surface)

---

### Requirement: Strict TDD discipline at apply time

The apply phase MUST follow RED → GREEN → TRIANGULATE → REFACTOR using `uv run pytest`. Each pure helper MUST have failing tests written first (helper truth table, shrinkage branches, neutral branch, rounding). Each DB-backed aggregation behaviour MUST use the existing `tests/_compliance_fixtures.py::seed_*` style (portable UPSERT — survives sqlite + postgres) or `_seed_transfer` from `tests/test_indexer_linking.py` for the `OnchainTransfer` rows.

Targeted pytest invocation MUST be used during development; the full suite is run only at the end of the apply phase. New tests grow the count without flaking; no previously-passing test regresses.

#### Scenario: RED → GREEN for `compute_wallet_activity_score`

- GIVEN the helper does not yet exist in `app/services/wallet_activity.py`
- WHEN `tests/test_wallet_activity.py` is written first and `uv run pytest tests/test_wallet_activity.py` is invoked
- THEN the test MUST fail with `ImportError` or `NameError` on the missing symbol (RED)
- AND only after the helper is implemented MUST the test pass (GREEN)

#### Scenario: full baseline preserved at the end of apply

- GIVEN the change has been fully applied and all targeted suites are green
- WHEN `uv run pytest` is run from the repo root
- THEN the final summary MUST report `348 passed`, `8 skipped`, `0 failed`
- AND the 8 skipped MUST be the same pre-existing Postgres-only tests, unchanged in count and names
- AND no previously-passing test in the pre-change baseline may regress

---

## Out of Scope

- Scheduler / cron work — `DESIGN.md` D8 nightly cron is deferred.
- Listing-page rollout — `/`, `/agents`, `/agents/{chain}` MUST NOT render the chip.
- `activity_score` / `wallet_score` mutation — the wallet signal stays parallel and additive.
- Phase 3 composite integration — folding `wallet_activity_score` into the activity composite is a future change.
- `agent_wallet` coverage — payment wallet is excluded from this slice.
- OFAC compliance integration — `agent_compliance_flags` / `compliance_penalty` are NOT consulted by this change.
- New `agent_cache` columns, new tables, new indexes, new migrations.
- Heuristic address matching.
- `## RENAMED Requirements` — gentle-pi does not support executable rename semantics yet; future refactors must model renames as explicit ADDED / MODIFIED / REMOVED with Reason / Migration notes.

---

## Risks

- **R-1 (low):** The frozen reference fixture (`tests/_fixtures/score_response_pre_wallet_activity.json` or equivalent) must be captured BEFORE the change is applied. If the fixture is captured after, AC-7 cannot fail loudly on accidental regressions. The fixture is the load-bearing artifact for the byte-identical assertion.
- **R-2 (low):** `wallet_activity_score = None` (aggregation failure) renders as `n/a` or `—` on the detail page. The exact fallback copy is a template-author choice; both `n/a` and `—` are acceptable per the requirement. Operators are expected to read the function logs (which already swallow transfer-fetch errors) when a chip is missing.
- **R-3 (low):** A future change that adds the chip to listing pages would push the per-page query count to 48 indexed seeks against `OnchainTransfer` (24 agents × 2 wallets). Tractable today but worth pre-planning. The negative assertion in the "Listing pages MUST stay wallet-chip-free" requirement is the load-bearing guard.
- **R-4 (low):** The `is_neutral` flag is a deliberate second-axis input to the helper. Future contributors who "simplify" the signature by removing it (e.g. by collapsing neutral into `events=0`) will regress the brand-new-wallet case — `recency_days=None` and zero events look identical at the SQL layer but the helper must treat a missing address differently from a real-but-inactive wallet. The requirement pins the signature explicitly to make this regression obvious in code review.
- **R-5 (info):** Output path is flat (`openspec/changes/wallet-activity/spec.md`); archive copies verbatim to `openspec/specs/wallet-activity/spec.md`. No nested `openspec/changes/wallet-activity/specs/wallet-activity/spec.md` layout was used per the parent task's explicit instruction.

---

## References

- Proposal / explore: `openspec/changes/wallet-activity/{proposal.md, explore.md}`
- Format precedent: `openspec/specs/agent-compliance/spec.md` (chained-SDD pattern; additive columns on `ScoreOut`; Decimal boundary; per-page additive chips; 348-pass baseline at chain closure)
- Existing surfaces: `app/services/agent_score.py` (`fetch_track_record`, `_track_parts`, `TRACK_WINDOW_DAYS=90`), `app/routers/onchain_stats.py::get_wallet_onchain_stats` (live wallet-keyed query precedent against `OnchainTransfer`), `app/routers/agents.py::get_agent_score`, `app/routers/pages.py::agent_detail`, `app/templates/pages/agent_detail.html`, `app/schemas/score.py::ScoreOut`, `app/db/models/agent.py::AgentCache.creator_address / .owner_address`
- Address-normalization precedent: `app/services/compliance_refresh.py:117-118` (`.strip().lower()` at the SQL boundary)
- Migration context: `migrations/versions/0005_onchain_index.py` (the source of `ix_onchain_transfers_from` / `ix_onchain_transfers_to`); no new migration is created by this change.