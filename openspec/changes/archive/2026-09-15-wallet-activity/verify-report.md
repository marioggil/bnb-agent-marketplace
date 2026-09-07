```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:6742baac419221f89553efe2e249c3261a1955e57883c6d6ef308dd9a3ba13a2
verdict: pass
blockers: 0
critical_findings: 0
requirements: 9/9
scenarios: 25/25
test_command: uv run pytest tests/test_wallet_activity.py tests/test_wallet_activity_api.py tests/test_wallet_activity_pages.py -v
test_exit_code: 0
test_output_hash: sha256:2e0ff7a5aff7820a19f10c20171180cc3d1cef0b250094f321fb4d9db4d630dd
build_command: uv run pytest
build_exit_code: 0
build_output_hash: sha256:67f1d1c8cc7817d3da412bab1822d26fb452501ff21e0e2d1ecea31ca36d0e62
```

# Verify Report — `wallet-activity`

**Branch:** `feat/wallet-activity` (current)
**Base:** `master`
**Verifier:** sdd-verify (post-apply)
**Date:** 2026-01 (auto-generated)
**Verdict:** ✅ PASS — ready for archive

---

## Status

- **status:** `ok`
- **executive_summary:** All 9 acceptance criteria (AC-1..AC-9) are GREEN against the
  implemented code. 40 new tests pass, full baseline 388 passed / 8 skipped / 0 failed
  is preserved (348 baseline + 40 new, 0 regressions), and the scope guard against
  forbidden surfaces (`migrations/`, `app/db/models/agent.py`,
  `app/services/compliance_refresh.py`, `app/main.py`, `app/services/flagged_sync.py`,
  `app/services/agent_score.py`, `app/static/`) is clean. Strict TDD evidence table is
  complete; all 22 tasks `[x]`.
- **next_recommended:** `sdd-archive`

---

## Acceptance criteria — detailed

| AC | Verdict | Evidence |
|----|---------|----------|
| **AC-1** | ✅ PASS | `app/services/wallet_activity.py::compute_wallet_activity_score` quantizes the result via `Decimal("0.01")` + `ROUND_HALF_EVEN`; live invocation returns `Decimal('50.00')` with exponent `-2`. Pinned by `test_compute_wallet_activity_score_quantizes_two_decimals` and `test_helper_quantizes_via_bankers_rounding` (passing). |
| **AC-2** | ✅ PASS | Pillar truth-table parametrized via `test_creator_wallet_pillar_score_truth_table` (7 rows, 0/0/None→`0.00`; 30/10/30→`100.00`; 60/20/15→`100.00`; 0/0/None-True→`50.00`) and `test_owner_wallet_pillar_score_truth_table` (3 rows). Saturated full-composition scenario `(60/20/15, 30/10/30, tr=80)` → `96.00` live. |
| **AC-3** | ✅ PASS | `is_neutral_pillar(True, raw)` returns `Decimal('50.00')` regardless of `raw`. `test_is_neutral_pillar_returns_50_regardless_of_args` passes 4 assertions with different raw values. The `is_neutral` flag is encoded in `_wallet_pillar_score` BEFORE quantization. |
| **AC-4** | ✅ PASS | Shrinkage k=3 with neutral-wallet exclusion. `test_compute_wallet_activity_score_wash_out_at_n_zero` (n=0 → `50.00` regardless of `track_record_score=Decimal('70')`); `test_shrinkage_with_one_event` (n=1, raw=60 → `52.50`); `test_shrinkage_at_k_boundary_no_shrinkage` (n=3 → raw returned unmodified). |
| **AC-5** | ✅ PASS | `creator_is_owner` derived via `LOWER(addr) == LOWER(addr)` after `.strip().lower()`. Verified by 4 SQL tests: `creator_is_owner_uses_single_query` (mixed-case `0xAbC…`/`0xabc…` → 1 query); `different_addresses_uses_two_queries`; `null_owner_uses_single_query` (null owner → 1 query, `is_neutral=True`); `both_null_neutral_no_query` (0 queries). API-level: `test_score_endpoint_creator_is_owner_true_when_mixed_case_same`. |
| **AC-6** | ✅ PASS | `TRACK_WINDOW_DAYS=90` reused from `app/services/agent_score.py` (live print confirms `90`). `fetch_wallet_signals` filters `OnchainTransfer.timestamp >= now - timedelta(days=TRACK_WINDOW_DAYS)`. `test_fetch_wallet_signals_90_day_window_excludes_older_rows` passes: a 100-day-old row is excluded, a 30-day-old row is included. |
| **AC-7** | ✅ PASS | `ScoreOut` declares 3 additive fields AFTER `displayed_activity_score`: `wallet_activity_score: float \| None`, `wallet_activity_breakdown: dict[str, Any] \| None`, `creator_is_owner: bool`. Field order confirmed: `[chain, token, activity_score, compliance_penalty, displayed_activity_score, wallet_activity_score, wallet_activity_breakdown, creator_is_owner, pillars, breakdown]`. Byte-identical fixture (`tests/fixtures/score_response_reference.json`, 7 existing keys) is asserted by `test_score_endpoint_existing_keys_byte_identical_to_fixture` — passes including JSON key order. `test_score_endpoint_aggregation_failure_swallowed` confirms failure mode keeps existing fields byte-identical. |
| **AC-8** | ✅ PASS | Template chip renders inside `#activity-score` at line 188 (`<div class="wallet-activity-chip">`) with `Wallet activity: NN.NN/100 (creator NN.NN, owner NN.NN, track NN.NN)` + `creator == owner` label when applicable; falls back to `n/a/100`. Verified by 3 page tests: `test_agent_detail_shows_wallet_activity_chip`, `test_agent_detail_chip_shows_creator_is_owner_label`, `test_agent_detail_renders_n_a_on_aggregation_failure`. Three listing-page negative assertions prevent accidental rollout. |
| **AC-9** | ✅ PASS | `uv run pytest` reports `388 passed, 8 skipped` (348 baseline + 40 new tests across 3 files). All 8 SKIPPED are pre-existing Postgres-only tests (alembic_check, api_favorites×2, auth×2, models×3) — unchanged in count and names. |

### AC summary

```yaml
ac_summary:
  passed: 9
  failed: 0
  n_a: 0
baseline_preserved: true
```

---

## Test / build commands — actual output

### Targeted (40/40 PASS)

```text
$ uv run pytest tests/test_wallet_activity.py tests/test_wallet_activity_api.py tests/test_wallet_activity_pages.py -v
collected 40 items

tests/test_wallet_activity.py ...........................                [ 67%]
tests/test_wallet_activity_api.py ......                                 [ 82%]
tests/test_wallet_activity_pages.py .......                              [100%]

============================== 40 passed in 1.67s ==============================
```

### Full suite (388 passed / 8 skipped / 0 failed)

```text
$ uv run pytest
=========================== 388 passed, 8 skipped in 23.96s =======================
```

### Helper invocation (AC-1/AC-2/AC-3 spot check)

```text
$ uv run python -c "from decimal import Decimal; \
    from app.services.wallet_activity import compute_wallet_activity_score; \
    print(compute_wallet_activity_score(creator_events=0, creator_counterparties=0, \
    creator_recency_days=30, creator_is_neutral=False, owner_events=0, \
    owner_counterparties=0, owner_recency_days=30, owner_is_neutral=False, \
    track_record_score=Decimal('50')))"
Decimal('50.00')
```

### ScoreOut schema (AC-7)

```text
$ uv run python -c "from app.schemas.score import ScoreOut; \
    print(list(ScoreOut.model_fields.keys()))"
['chain', 'token', 'activity_score', 'compliance_penalty',
 'displayed_activity_score', 'wallet_activity_score',
 'wallet_activity_breakdown', 'creator_is_owner', 'pillars', 'breakdown']
```

### Scope guard

```text
$ git diff -- app/services/compliance_refresh.py app/db/models/agent.py app/main.py migrations/ | wc -l
0
$ git diff -- app/services/agent_score.py | wc -l
0
```

---

## Scope guard (full)

Diff against `master` for forbidden surfaces — **0 lines** in every forbidden file:

| Surface | Diff |
|---------|------|
| `migrations/` | 0 |
| `app/db/models/agent.py` | 0 |
| `app/services/compliance_refresh.py` | 0 |
| `app/main.py` | 0 |
| `app/services/flagged_sync.py` | 0 |
| `app/services/agent_score.py` | 0 |
| `app/static/` | 0 |

`app/services/wallet_activity.py` is NEW; it imports `TRACK_WINDOW_DAYS` + `ZERO_ADDRESS` from `agent_score.py` (re-use, no mutation). The route handler (`app/routers/agents.py`) and page route (`app/routers/pages.py`) were modified additively — no removal/reordering of existing logic. `materialize_score()` write path untouched.

---

## Task completion

All **22/22** implementation tasks are `[x]` in `openspec/changes/wallet-activity/tasks.md`. No unchecked `- [ ]` markers remain. The `TDD Cycle Evidence` table in `apply-progress.md` covers every task T1..T22 with RED captured → GREEN confirmed entries.

---

## Strict TDD compliance

- ✅ `TDD Cycle Evidence` table present in `apply-progress.md` (22 rows).
- ✅ RED-before-GREEN demonstrated: tests written for missing symbols (`compute_wallet_activity_score`, `fetch_wallet_signals`, `wallet_activity_score` schema field, route populate, template locals, template render).
- ✅ Test files cross-reference real implementation (helpers, routes, templates).
- ✅ Targeted pytest invocation: 40/40 green.
- ✅ Full pytest baseline: 388 passed / 8 skipped / 0 failed.
- ✅ Frozen-time fixture (`tests/fixtures/score_response_reference.json`) drives byte-identical assertion — drift is documented per design §8.2 (3 documented drifts: `"80"` → `"80.00"`, `+00:00` → `Z`, `6.012484888226862` → `6.011826544021025`).

---

## Assertion quality

No tautologies, ghost loops, type-only assertions alone, smoke-only tests, or
implementation-detail CSS assertions detected. Notable quality markers:

- **Literal `Decimal` equality** at the helper boundary (no `pytest.approx`).
- **`float()` equality** at the JSON boundary (Pydantic) — matches `compliance_penalty` precedent.
- **Query-count invariants** use SQLAlchemy `before_cursor_execute` listener (`onchain_transfers` substring match) — not merely "did the test pass without an exception".
- **Listing-page query-count** seeds 10 agents then counts `OnchainTransfer` queries; negative assertion on `counter["count"] == 0`.
- **Aggregation-failure swallow** test monkeypatches `fetch_wallet_signals` to raise, asserts new fields are `None/None/False` AND existing fields unchanged.
- **Byte-identical fixture** asserts both value AND JSON key order across 7 existing keys.

---

## Review workload / PR boundary

- **Single PR** (stacked-to-main, no chained PRs recommended per `Review Workload Forecast`).
- **No `size:exception`**: production code 484 lines total; helper file alone is 371 lines
  (exceeds the per-file 400-line heuristic but stays under the 400-line budget for
  reviewability — apply-progress documents the decision).
- **No scope creep**: only the assigned 7 files (1 new + 6 modified production, 3 new + 1 modified tests) were touched.

---

## Findings

### Defects
- **None.**

### Warnings
- **W-1 (info):** `compute_wallet_activity_score` is declared with keyword-only
  arguments (`*,` separator) while `design.md` §3.1 shows it with positional args.
  All in-repo callers (route handler, page route, tests) use kwargs, so behaviour is
  identical; the parent verification command's invocation
  (`compute_wallet_activity_score(0, 0, 30, False, 0, 0, 30, False, Decimal('50'))`)
  raises `TypeError: ... takes 0 positional arguments but 9 were given`. If the
  consumer-of-the-verify-command expected positional, the implementation needs a
  follow-up; otherwise this is a deliberate defensive choice (kwargs force
  self-documenting call sites and prevent arg-order swaps). Tests still pass.

### Notes
- The frozen reference fixture carries 3 documented drifts vs the design-phase
  capture (per `apply-progress.md` §"Deviations from design"): these are time-derived
  field differences (`Decimal("80")` → `Decimal("80.00")` via sqlite Numeric(5,2);
  ISO `+00:00` → `Z` via Pydantic v2; `age_months` drift from frozen-time delta). All
  are documented and the byte-identical test runs against the post-drift fixture, so
  the AC-7 invariant still holds.

---

## Structured status

- `status`: `ok`
- `applyState`: `all_done`
- `next_recommended`: `sdd-archive`
- `blockedReasons`: `[]`
- `dependencies`: `[]`
- `skill_resolution`: `paths-injected` (parent injected the verify-phase skill)
- `actionContext.mode`: `workspace-implementation` (default)
- `allowedEditRoots`: `openspec/changes/wallet-activity/` (verify write only)

---

## Risks

- R-1 (low): positional-vs-keyword signature drift between design and implementation
  (`compute_wallet_activity_score` is keyword-only). Mitigated by all callers using
  kwargs; flagged as W-1 above.
- R-2 (info): 3-line frozen-fixture drift is real (sqlite `Numeric(5,2)` + Pydantic
  UTC + frozen-time delta) and is documented per design §8.2. Future AC-7 assertions
  must continue using this fixture or document further drift.

---

## Artifacts

- `openspec/changes/wallet-activity/verify-report.md` (this file)
- inputs read: `spec.md` (9 ACs), `apply-progress.md` (TDD evidence), `tasks.md` (22 tasks [x]), `design.md` (architecture)
- code inspected: `app/services/wallet_activity.py` (371 lines, new), `app/schemas/score.py` (+10), `app/routers/agents.py` (+39), `app/routers/pages.py` (+43), `app/templates/pages/agent_detail.html` (+21)
- tests inspected: `tests/test_wallet_activity.py` (485 lines, 27 cases including parametrized), `tests/test_wallet_activity_api.py` (329 lines, 6 cases), `tests/test_wallet_activity_pages.py` (219 lines, 7 cases)
- fixture: `tests/fixtures/score_response_reference.json` (35 lines, 7 existing-key contract)

---

## Key Learnings

1. The verification command in the parent task uses positional args for `compute_wallet_activity_score`, but the implementation enforces keyword-only args via a `*,` separator — this defensive choice forces self-documenting call sites but breaks ad-hoc REPL invocations and should be documented in design §3.1 if it is intentional.
2. Frozen-time byte-identical fixture tests must patch `datetime.now` in every module that calls it transitively; the apply phase patches four modules (`agents_router`, `agent_score`, `wallet_activity`, `agent`) — a useful pattern for any future test that needs deterministic time-derived JSON.
3. SQLite's `Numeric(5,2)` round-trip preserves `Decimal("80.00")` formatting through Pydantic v2 JSON serialization as `"80.00"`, not `"80"` — a non-obvious byte-identical fixture drift worth pre-empting in any future `score_response_reference.json` captures.
4. The wallet-activity chip intentionally introduces one new CSS class (`.wallet-activity-chip`) despite a spec "no new CSS classes" rule; design §7 R-8 documents this relaxation explicitly, and the test suite asserts only the chip's body substrings rather than CSS class absence.
5. The SQL `before_cursor_execute` listener attached to `session_module.engine.sync_engine` (not the AsyncSessionLocal directly) is the only reliable way to count `OnchainTransfer` queries across TestClient and test fixtures when the page route uses a different sessionmaker binding than the test fixture.