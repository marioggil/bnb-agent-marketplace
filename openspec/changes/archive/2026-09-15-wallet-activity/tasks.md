# Tasks: wallet-activity

**Change:** `wallet-activity`
**Sub-PR:** single PR (independent slice)
**Branch:** `feat/wallet-activity`
**Base:** `master`
**Strategy:** stacked-to-main · strict TDD · `uv run pytest`
**Test discipline:** RED → GREEN → TRIANGULATE → REFACTOR with targeted invocation; full suite only at end.

---

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~370 (1 helper + 1 fetcher + schema + route + page + tests) |
| 400-line budget risk | Medium (1 PR just under budget; tests are bulk) |
| Chained PRs recommended | No (single-slice additive change) |
| Suggested split | n/a — single PR |
| Delivery strategy | ask-on-risk |
| Chain strategy | stacked-to-main |

```text
Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: stacked-to-main
400-line budget risk: Medium
```

> **Operator decision NOT required before apply** — this is a single additive slice with low cross-cutting surface. The 400-line budget is the only risk; mitigation is to split `tests/test_wallet_activity.py` into a separate commit if a helper implementation grows past ~80 lines.

---

## Dependency statement

This sub-PR is **independent of any in-flight change**:

- **No dependency on `compliance-flags`** (compliance-flags chain already merged). `agent_cache.compliance_penalty` already exists and is populated. `agent_compliance_flags` is already populated by the orchestrator. This change touches a *parallel* additive surface (`wallet_activity_score`/`creator_is_owner`) — none of the OFAC signals are read or written.
- **No new migration, no new `agent_cache` column, no new table, no new scheduler.** The score is fully derived from `OnchainTransfer` over `ix_onchain_transfers_from` / `ix_onchain_transfers_to` (migration `0005_onchain_index`).
- **No scheduler / cron work** — read-side at request time only.
- **Fixture** `tests/fixtures/score_response_reference.json` was captured during the design phase (per design §8). Apply phase MUST freeze `datetime.now` at `2026-01-01T00:00:00+00:00` during the byte-identical assertion (T15).

## Task ordering rationale

Pure helper first (RED → GREEN → TRIANGULATE for the math, AC-1/2/3/4), then aggregation (RED → GREEN for the SQL contract + query-count invariants, AC-5/6), then additive API fields (AC-7), then UI chip (AC-8), then negative assertions on listing pages (AC-8 §Non-Goals), then full-suite verification (AC-9). Pure helper before DB-backed tests so that all math failure modes are pinned before any aggregation behaviour is asserted.

---

## Tasks

- [x] **T1 — RED: `test_compute_wallet_activity_score_basic_truth_table`.** Create `tests/test_wallet_activity.py` with a parametrized case `test_compute_wallet_activity_score_basic_truth_table` covering 4–7 rows from design §3.4 (zero/active/very-active wallets, neutral flag load-bearing). Expected: `ImportError` or `NameError` on missing `compute_wallet_activity_score`. Run `uv run pytest tests/test_wallet_activity.py -k basic_truth_table -x`. RED captured. <!-- sdd-owner: implementation -->

- [x] **T2 — GREEN: implement `creator_wallet_pillar_score`, `owner_wallet_pillar_score`, `is_neutral_pillar` in `app/services/wallet_activity.py`.** Implement the three helpers per design §3.5 (pure, side-effect-free, no logging). Per-pillar math: `0.5*min(1, events/30) + 0.3*min(1, counterparties/10) + 0.2*min(1, 30/recency_days)`. `is_neutral_pillar` returns `Decimal("50.00")` when flag is set. Both pillar helpers wrap `_wallet_pillar_score` and quantize via `ROUND_HALF_EVEN`. Files touched: `app/services/wallet_activity.py` (new, ~50 lines). Re-run `uv run pytest tests/test_wallet_activity.py -k basic_truth_table`. Expected: passes. <!-- sdd-owner: implementation -->

- [x] **T3 — RED: `test_neutral_pillar_returns_50`.** In `tests/test_wallet_activity.py` add `test_is_neutral_pillar_returns_50_regardless_of_args` — pass `(events=30, counterparties=10, recency_days=30, is_neutral=True)` and assert `is_neutral_pillar` returns `Decimal("50.00")` literal. Run `uv run pytest tests/test_wallet_activity.py -k neutral_pillar -x`. Expected: fails (helper exists but the flag is not load-bearing yet — T4). RED captured. <!-- sdd-owner: implementation -->

- [x] **T4 — GREEN: encode neutral branch in helpers.** In `app/services/wallet_activity.py` ensure `_wallet_pillar_score` consults `is_neutral_pillar(is_neutral, raw)` before quantizing. Re-run `uv run pytest tests/test_wallet_activity.py -k neutral_pillar`. Expected: passes. <!-- sdd-owner: implementation -->

- [x] **T5 — RED: `test_recency_days_none_returns_zero_recency_component`.** In `tests/test_wallet_activity.py` add `test_creator_wallet_pillar_score_zero_events_returns_zero_pillar` (events=0, counterparties=0, recency_days=None, is_neutral=False) → `Decimal("0.00")` literal. Pin divide-by-zero guard. Run `uv run pytest tests/test_wallet_activity.py -k recency_none -x`. Expected: fails (T7 covers `compute_wallet_activity_score` itself; recency None handling lives in the same module). RED captured. <!-- sdd-owner: implementation -->

- [x] **T6 — RED: `test_shrinkage_with_zero_events_returns_50_washout`.** In `tests/test_wallet_activity.py` add `test_compute_wallet_activity_score_wash_out_at_n_zero` — both addresses null-equivalent (events=0, is_neutral=True on both), `track_record_score=Decimal("70.00")`; assert final = `Decimal("50.00")` regardless of `track_record_score`. Run `uv run pytest tests/test_wallet_activity.py -k wash_out -x`. Expected: `NameError` on `compute_wallet_activity_score`. RED captured. <!-- sdd-owner: implementation -->

- [x] **T7 — GREEN: implement `compute_wallet_activity_score` with shrinkage k=3 + `ROUND_HALF_EVEN`.** In `app/services/wallet_activity.py` add the top-level pure helper per design §3.1. Composition: `raw = 0.50*creator + 0.30*owner + 0.20*track_record_score`. Shrinkage: `n = creator_events + owner_events` (neutral wallets contribute 0 to `n`); when `n < 3`: `(n/(n+3))*raw + (3/(n+3))*Decimal("50.00")`; else `raw`. Quantize `Decimal("0.01")` via `ROUND_HALF_EVEN`. Re-run `uv run pytest tests/test_wallet_activity.py`. Expected: all of T1/T3/T5/T6 green. <!-- sdd-owner: implementation -->

- [x] **T8 — TRIANGULATE: `test_shrinkage_k3_with_one_event` + `test_shrinkage_k3_with_three_events_returns_full_weight`.** In `tests/test_wallet_activity.py` add the n=1 and n=3 boundary cases from design §4 truth table. Pin `n=1`: `(1/4)*60 + (3/4)*50 = Decimal("52.50")`. Pin `n=3`: `raw` returned unmodified (k=3 floor). Also add `test_compute_wallet_activity_score_quantizes_two_decimals` pinning `12.345 → Decimal("12.34")` (banker's rounding). Run `uv run pytest tests/test_wallet_activity.py`. Expected: all green. <!-- sdd-owner: implementation -->

- [x] **T9 — RED: `tests/test_wallet_activity.py::test_fetch_wallet_signals_creator_only` (SQL seam).** Add a test that seeds one `AgentCache` row with `creator_address="0xabc..."` and `owner_address=NULL`, plus one matching `OnchainTransfer` row in the 90-day window (use `tests/_compliance_fixtures._seed_agent_with_addresses` pattern + a `_seed_transfer` helper). Call `fetch_wallet_signals(session, agent_id)`; assert `creator.events==1, creator.is_neutral==False, owner.is_neutral==True, owner.events==0`. Run `uv run pytest tests/test_wallet_activity.py -k creator_only -x`. Expected: `ImportError` on `fetch_wallet_signals`. RED captured. <!-- sdd-owner: implementation -->

- [x] **T10 — GREEN: implement `fetch_wallet_signals(session, agent_id) -> WalletSignals` in `app/services/wallet_activity.py`.** Add the `WalletSignal`/`WalletSignals` dataclasses; implement the SQL contract from design §5.1–5.2. Read `AgentCache.creator_address` and `owner_address`; normalize via `.strip().lower()` at the SQL boundary (verbatim precedent `app/services/compliance_refresh.py:117-118`). Filter `OnchainTransfer` rows by `timestamp >= now - TRACK_WINDOW_DAYS` (= 90 days, reuse `TRACK_WINDOW_DAYS` from `app/services/agent_score.py`). Aggregates: `events = COUNT(DISTINCT tx_hash)`, `counterparties = COUNT(DISTINCT other_side) FILTER (other != ZERO_ADDRESS)`, `recency_days = (now - MAX(timestamp)).days if MAX(timestamp) else None`. Re-run `uv run pytest tests/test_wallet_activity.py -k creator_only`. Expected: passes. <!-- sdd-owner: implementation -->

- [x] **T11 — RED: `test_fetch_wallet_signals_creator_is_owner_uses_single_query`.** In `tests/test_wallet_activity.py` add the `before_cursor_execute` SQLAlchemy listener that counts queries against `OnchainTransfer`. Seed `creator_address="0xAbC..."` and `owner_address="0xabc..."` (mixed-case, same). Run `fetch_wallet_signals`; assert query count == 1. Run `uv run pytest tests/test_wallet_activity.py -k creator_is_owner_uses_single_query -x`. Expected: fails (T10 currently emits 2). RED captured. <!-- sdd-owner: implementation -->

- [x] **T12 — GREEN: implement `creator_is_owner` collapse in `fetch_wallet_signals`.** In `app/services/wallet_activity.py::fetch_wallet_signals`, detect `LOWER(creator_addr) == LOWER(owner_addr)` (both non-empty) and short-circuit to a single `_query_wallet` call shared by both `creator` and `owner` `WalletSignal` objects. When both addresses are null, return `(neutral, neutral, creator_is_owner=False)` with **0** queries. Re-run `uv run pytest tests/test_wallet_activity.py -k "creator_is_owner or null_owner or both_null"`. Expected: passes. <!-- sdd-owner: implementation -->

- [x] **T13 — RED: `tests/test_wallet_activity_api.py::test_score_endpoint_includes_wallet_activity_score`.** Create `tests/test_wallet_activity_api.py`. Use TestClient against a seeded agent. Call `GET /api/agents/56/1/score`; assert response JSON contains key `wallet_activity_score` (float in `[0.0, 100.0]`). Run `uv run pytest tests/test_wallet_activity_api.py -k includes_wallet_activity_score -x`. Expected: `KeyError` (field absent). RED captured. <!-- sdd-owner: implementation -->

- [x] **T14 — GREEN: add 3 additive fields to `ScoreOut` in `app/schemas/score.py`.** Append (after `displayed_activity_score`) `wallet_activity_score: float | None = None`, `wallet_activity_breakdown: dict[str, Any] | None = None`, `creator_is_owner: bool = False`. **Do NOT** rename, remove, or reorder any existing field. Re-run `uv run pytest tests/test_wallet_activity_api.py -k includes_wallet_activity_score`. Expected: still fails (route handler not populating yet — T16 covers populate). Partial GREEN at this step = the field is in the schema. Files touched: `app/schemas/score.py` (~3 lines). <!-- sdd-owner: implementation -->

- [x] **T15 — RED: `test_score_endpoint_existing_fields_byte_identical_to_fixture`.** In `tests/test_wallet_activity_api.py` add the AC-7 load-bearing assertion. Load `tests/fixtures/score_response_reference.json`. Seed a stable Alpha agent (per design §8: chain 56, token 101, frozen timestamp `2026-01-01T00:00:00+00:00`, `activity_score=Decimal("80")`). **Monkey-patch `datetime.now` to return the fixture reference time** (Pydantic + json.dumps round-trip is order-stable when seed data is deterministic). Call `GET /api/agents/56/101/score`; assert response JSON's existing keys (`chain`/`token`/`activity_score`/`compliance_penalty`/`displayed_activity_score`/`pillars`/`breakdown`) are byte-identical to fixture (key set + values + JSON key order). Run `uv run pytest tests/test_wallet_activity_api.py -k byte_identical -x`. Expected: fails (T16 not yet populating fields — but this test must fail because `probed_at`/`age_months`/`recency_days` are time-derived and the fixture is frozen). RED captured. <!-- sdd-owner: implementation -->

- [x] **T16 — GREEN: populate the 3 new fields in `app/routers/agents.py::get_agent_score`.** Inside a new `try / except` block, call `fetch_wallet_signals(db, row.agent_id)` and `compute_wallet_activity_score(...)`; populate `wallet_activity_score`, `wallet_activity_breakdown`, `creator_is_owner` on `ScoreOut`. **Do NOT modify the existing `ScoreOut(...)` construction for non-wallet fields** (`chain`, `token`, `activity_score`, `compliance_penalty`, `displayed_activity_score`, `pillars`, `breakdown`). On `fetch_wallet_signals` failure, set `wallet_activity_score=None`, `wallet_activity_breakdown=None`, `creator_is_owner=False`. Re-run `uv run pytest tests/test_wallet_activity_api.py`. Expected: T13/T15 green; full API suite green. Files touched: `app/routers/agents.py` (~12 lines). <!-- sdd-owner: implementation -->

- [x] **T17 — RED: `tests/test_wallet_activity_pages.py::test_agent_detail_shows_wallet_activity_chip`.** Create `tests/test_wallet_activity_pages.py`. Seed `AgentCache` with both `creator_address` and `owner_address` populated (chain 56, token 1). Call `GET /agents/56/1`; assert response body contains substring `Wallet activity:` AND the breakdown labels `creator`/`owner`/`track_record`. Run `uv run pytest tests/test_wallet_activity_pages.py -k shows_wallet_activity_chip -x`. Expected: substring missing (T18 not yet populating template locals). RED captured. <!-- sdd-owner: implementation -->

- [x] **T18 — GREEN: extend `app/routers/pages.py::agent_detail` to fetch `WalletActivity` and pass to template.** Inside the existing `try / except onchain_stats` block (lines ~867–895 in `app/routers/pages.py`), call `wallet_signals = await fetch_wallet_signals(ocs, row.agent_id)` and compute `wallet_activity_score` + `wallet_activity_breakdown`. Pass three new template locals: `wallet_activity_score`, `wallet_activity_breakdown`, `creator_is_owner`. On failure, default to `None`/`None`/`False`. Re-run `uv run pytest tests/test_wallet_activity_pages.py -k shows_wallet_activity_chip`. Expected: still fails (template lacks chip — T20). Files touched: `app/routers/pages.py` (~10 lines). <!-- sdd-owner: implementation -->

- [x] **T19 — RED: `test_agent_detail_chip_shows_creator_is_owner_label`.** In `tests/test_wallet_activity_pages.py` add: seed `creator_address="0xAbC..."` and `owner_address="0xabc..."` (same address, mixed-case). Call `GET /agents/56/1`; assert response body contains substring `creator == owner`. Run `uv run pytest tests/test_wallet_activity_pages.py -k creator_is_owner_label -x`. Expected: fails (T20 not yet rendering label). RED captured. <!-- sdd-owner: implementation -->

- [x] **T20 — GREEN: extend `app/templates/pages/agent_detail.html` with the chip.** Insert `<div class="wallet-activity-chip">` inside the existing `#activity-score` section (line ~177), AFTER the score-value `<p>` and BEFORE the breakdown `<dl>` (per design §7). One new CSS class `.wallet-activity-chip` for flex layout (R-8 caveat). Reuse `.badge.category`. Render `Wallet activity: {{ '%.2f'|format(wallet_activity_score) }}/100 (creator {{ '%.2f'|format(...) }}, owner {{ '%.2f'|format(...) }}, track {{ '%.2f'|format(...) }})` + `creator == owner` label when `creator_is_owner`. Fallback `n/a/100` when score is `None`. Re-run `uv run pytest tests/test_wallet_activity_pages.py -k "shows_wallet_activity_chip or creator_is_owner_label"`. Expected: both pass. Files touched: `app/templates/pages/agent_detail.html` (~14 lines). <!-- sdd-owner: implementation -->

- [x] **T21 — NEGATIVE ASSERTION: `test_listing_pages_dont_aggregate_wallet_signals`.** In `tests/test_wallet_activity_pages.py` add `test_home_page_does_not_render_wallet_activity_chip`, `test_agents_index_does_not_render_wallet_activity_chip`, `test_agents_chain_index_does_not_render_wallet_activity_chip`. Each calls the respective endpoint and asserts body does NOT contain substring `Wallet activity:`. Also add `test_listing_pages_issue_zero_wallet_queries` — SQLAlchemy `before_cursor_execute` listener asserting zero queries against `OnchainTransfer` on the listing endpoints. Run `uv run pytest tests/test_wallet_activity_pages.py -k listing`. Expected: all green (chip only on detail page). <!-- sdd-owner: implementation -->

- [x] **T22 — Final verification.** Run from repo root:
  1. `uv run pytest tests/test_wallet_activity.py tests/test_wallet_activity_api.py tests/test_wallet_activity_pages.py -v` → all green.
  2. `uv run pytest` (full suite) → baseline `348 passed, 8 skipped, 0 failed` preserved + new tests grow the count without flaking.
  3. `git diff -- migrations/ app/db/models/agent.py app/services/compliance_refresh.py app/main.py` → empty (scope guard: `compliance-flags` chain untouched, no new migration, no model mutation, no main.py wiring).
  4. `git diff --stat -- app/ tests/` → ≤ 400 net lines per file; total diff budget within forecast.
  5. **Manual smoke**: `GET /api/agents/56/101/score` against the seeded Alpha agent returns the 3 new keys (`wallet_activity_score`, `wallet_activity_breakdown`, `creator_is_owner`) AND existing-key JSON byte-identical to `tests/fixtures/score_response_reference.json`; `GET /agents/56/1` renders the chip with breakdown; `GET /` and `GET /agents` do NOT render the chip.
  6. **Scope guard**: `app/static/`, `app/services/flagged_sync.py`, `app/services/agent_score.py`, `migrations/versions/` all byte-identical to `master` — confirm via `git diff -- master -- app/static/ app/services/flagged_sync.py app/services/agent_score.py migrations/`. <!-- sdd-owner: implementation -->

---

## REFACTOR notes

- `app/services/wallet_activity.py` is the new module — keep it pure (`compute_wallet_activity_score`, `creator_wallet_pillar_score`, `owner_wallet_pillar_score`, `is_neutral_pillar`, `compute_wallet_activity_breakdown`) except for the one async `fetch_wallet_signals` SQL entry point. Mirrors `app/services/agent_score.py` structure.
- `WalletSignal` / `WalletSignals` are frozen dataclasses (per design §2.1); structured triple, not scalar args, because `creator_is_owner` collapse depends on aggregation-level knowledge the pure helper cannot see.
- `get_agent_score` route handler: existing `ScoreOut(...)` construction for non-wallet fields is BYTE-IDENTICAL to the pre-change fixture. The 3 new fields are populated separately (T14 + T16). This is the AC-7 invariant.
- `agent_detail.html`: ONE new class `.wallet-activity-chip` for flex layout. Existing `.badge.category` / `.dim-bars` / `.score-dimensions` reused. Do NOT touch the OFAC banner area.

## Evidence line (each task)

Each GREEN completion confirmed by the explicit `uv run pytest -k <marker>` invocation in the task body. RED first-failures capture the missing helper (T1/T6/T9), missing schema field (T13), missing route populate (T15), missing template locals (T17), and missing template render (T19).

## Verification command (one-shot, run at end)

```bash
uv run pytest tests/test_wallet_activity.py tests/test_wallet_activity_api.py tests/test_wallet_activity_pages.py -v && \
uv run pytest && \
git diff -- migrations/ app/db/models/agent.py app/services/compliance_refresh.py app/main.py | wc -l
```

Expected final output: `0` (zero changed lines in `migrations/`, `app/db/models/agent.py`, `app/services/compliance_refresh.py`, `app/main.py` — scope guard held).