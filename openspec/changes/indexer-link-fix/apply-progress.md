# Apply Progress: indexer-link-fix

## Status

`completed` — both chained PRs landed; the change is fully applied against master.

**Chain strategy:** stacked-to-main (PR-A → master, PR-B → master).

**Branch lineage:**

| Branch | Merge into master | Working commit |
|---|---|---|
| `pr-a-link-fix` (off `e20a34f`) | merge `9c65408` | `2d91945 fix(onchain): link $U transfers to agents via shared canonicalizer` |
| `pr-b-link-fix` (off `9c65408`) | merge `ce0e143` | `9ffc4a0 feat(onchain): backfill link script + indexer tests` |

`master` is now 3 commits ahead of `e20a34f` (pre-change HEAD): the merge
of PR-A, the feature commit of PR-A, and the merge of PR-B and the
feature commit of PR-B (counting from the merge base `e20a34f`: 4
commits — `2d91945`, `9c65408`, `9ffc4a0`, `ce0e143`).

## Completed tasks

### PR-A — WU1 + WU2 + WU4

- [x] **WU1 — Diagnostic.** `scripts/diag_linking.py` probes overlap against the
  four candidate wallet columns (`agent_wallet`, `creator_address`,
  `owner_address`, `contract_address`) on `agent_cache` and prints the
  four REQ-001 candidate counters (case / whitespace / `0x`-prefix drift
  / length != 42). Decision line: `case | whitespace | wrong-column |
  structural`. Script exits 0 unconditionally. Smoke run captured in
  `notes/diag-2026-09-05.md`. Decided root cause: **case** (3/5 sample
  rows had uppercase hex in their `to_address` body, and the
  `agent_wallet` column was the only one producing matches).
- [x] **WU2 — Fix linking logic.** Added `_canon_addr()` next to
  `_extract_addr` at `app/services/onchain_indexer.py:93`. Added the public
  `resolve_wallet_to_agent(session)` coroutine. Refactored the private
  `_resolve_agent_wallets` to delegate to the public helper. Both
  `_scan_and_store()` (line 320) and `_scan_and_store_direct()` (line 432)
  now use `wallet_to_agent.get(_canon_addr(to_addr))` instead of
  `to_addr.lower()`. Grep confirms no `to_addr.lower()` references remain
  in the scan paths.
- [x] **WU4 — Realtime linking verification.** Documented R-5/R-6 in the
  `resolve_wallet_to_agent` docstring (empty wallet map on first cycle is
  a no-op; a wallet added between cycles is picked up on the next
  refresh). Added the "Refresh per cycle so newly-synced agents are
  picked up on the next pass" comment at both cycle entry points
  (`_backfill_cycle`, `_realtime_cycle`). Grep confirms no TODO / FIXME /
  cron / periodic references to a "link later" job — REQ-005's
  no-deferral constraint is upheld.

### PR-B — WU3 + WU5

- [x] **WU3 — Backfill script.** `scripts/backfill_link_agents.py`. New CLI
  with `--chunk` (default 10K, max 100K), `--dry-run`, `--limit`.
  Re-uses `resolve_wallet_to_agent` from PR-A so realtime and backfill
  cannot drift. Keyset pagination on `id` (the PK, monotonic) with
  `WHERE linked_agent_id IS NULL` for idempotency. Dialect-aware UPDATE:
  Postgres `pg_insert(...).on_conflict_do_update()` over `VALUES`; sqlite
  `executemany` of parameter binds. Final summary line shape:
  `unlinked_before=<n> linked_now=<n> still_unlinked=<n> wallets_known=<n>`
  (matches obs-242). Per-chunk commit + 50ms sleep.
  Exit codes: 0 success / no-op, 2 stall, 1 DB error.
- [x] **WU5 — Tests.** `tests/test_indexer_linking.py`. 12 test functions,
  ~47 assertions. Collected by `uv run pytest` with no new fixtures
  beyond `tests/conftest.py`'s existing `db` / `client` / `respx_mock`.
  Covers REQ-001 (3 tests), REQ-002 (3 tests), REQ-003 (3 tests),
  REQ-004 (1 respx integration test), REQ-005 (2 tests).

## Files changed

```text
app/services/onchain_indexer.py                          |  63 ++-
scripts/__init__.py                                      |   0
scripts/diag_linking.py                                  | 211 +++++
scripts/backfill_link_agents.py                          | 325 ++++++++
tests/test_indexer_linking.py                            | 527 +++++++++++
openspec/changes/indexer-link-fix/{spec,design}.md      | 616 +++++++ (initial add)
openspec/changes/indexer-link-fix/proposal.md            |  81 ++++ (initial add)
openspec/changes/indexer-link-fix/tasks.md               |  32 +-  (checkboxes)
openspec/changes/indexer-link-fix/notes/diag-2026-09-05.md | 101 ++++++
```

Production code changes are concentrated in two files:
`app/services/onchain_indexer.py` (+58 / -5 net, PR-A) and the two new
scripts under `scripts/` (PR-A: 211 lines diag, PR-B: 325 lines
backfill). Total new test code: 527 lines (PR-B). The `spec` /
`design` / `proposal` / `notes` files are tracked alongside the code so
the change artifacts travel with the implementation.

## Test commands run

| Command | When | Result |
|---|---|---|
| `uv run pytest` (baseline, pre-change) | before any edits | 285 passed, 8 skipped |
| `.venv/bin/pytest tests/test_onchain_indexer.py tests/test_api_agent_payments.py -v` | after PR-A | 12 passed |
| `uv run pytest` (full suite, post PR-A) | after PR-A | 285 passed, 8 skipped (baseline preserved) |
| `python -m scripts.diag_linking --sample 100` (smoke against test sqlite) | after WU1 | exits 0, decision: case |
| `python -m scripts.backfill_link_agents --chunk 5` (smoke, 10 rows) | after WU3 | `unlinked_before=10 linked_now=4 still_unlinked=6 wallets_known=1` exit 0 |
| `python -m scripts.backfill_link_agents --chunk 5` (idempotent re-run) | after WU3 | `unlinked_before=6 linked_now=0 still_unlinked=6 wallets_known=1` exit 0 (matchable_seen=0) |
| `python -m scripts.backfill_link_agents --dry-run --chunk 5` | after WU3 | exit 0, summary line with `(dry-run)` suffix |
| `python -m scripts.backfill_link_agents --chunk 5 --limit 5` | after WU3 | exit 0, partial-process behavior confirmed |
| `.venv/bin/pytest tests/test_indexer_linking.py -v` | after WU5 | 12 passed in 0.97s |
| `uv run pytest` (full suite, post PR-B) | after PR-B | **297 passed, 8 skipped** in 15.96s |

## Acceptance criteria status

| AC | Spec clause | Status |
|---|---|---|
| AC-1 | `top_agents` has ≥ 5 distinct agents with ≥ 5 transfers each | **deferred to production** — code-only change; needs `python -m scripts.backfill_link_agents` against prod. Unchecked in `tasks.md`. |
| AC-2 | Agent payments endpoint returns data | **deferred to production** — per design §10 OQ-1, this endpoint uses live RPC; the indexed-payments endpoint is a separate change. Unchecked. |
| AC-3 | `tests/test_indexer_linking.py` exists and is collected | **satisfied** — 12 tests, 47 assertions, collected by `uv run pytest`. |
| AC-4 | Idempotent backfill | **satisfied** — second run on the smoke fixture reports `linked_now=0` and exits 0 (verified above). |
| AC-5 | Baseline preserved (no new failures) | **satisfied** — 297 - 285 = 12 new tests, 0 regressions. |

## Deviations from design

1. **Stall guard uses `matchable_seen`, not `unlinked_before`.**
   The spec's REQ-003 exit-code rule (`linked_now == 0 AND wallets_known > 0
   AND unlinked_before > 0 → exit 2`) is in tension with the idempotent
   re-run scenario (`linked_now == 0 AND unlinked_before > 0 → exit 0`), since
   the unknown-wallet rows are still NULL after a fully-successful first run.
   The design introduces the refinement implicitly via "matchable" rows;
   the implementation tracks `matchable_seen` (rows whose `to_address`
   canonicalizes to a known wallet) per run. The exit-2 guard now triggers
   on `linked_now == 0 AND wallets_known > 0 AND matchable_seen > 0`, which
   correctly distinguishes a real stall from a clean idempotent no-op.

2. **Test file is 527 lines, not the design's ~600 estimate.** Within budget;
   covered all 12 tests with full docstrings. The design's test plan called
   for ≥ 10 functions, ≥ 25 assertions; the implementation contributes 12
   functions and ~47 assertions.

3. **PR-B total code lines: 852 (estimate was ~450).** A ~90% overrun on
   the per-PR budget is reported as a `size:exception` under the
   user-approved delivery strategy. The excess is in the test file's
   documentation (per-test docstring + per-section header) and in the
   backfill's dialect-aware branching (Postgres `pg_insert` path + sqlite
   `executemany` path). No assertion was trimmed to fit; the budget
   constrains slicing, not code content.

## Remaining tasks

None of the in-scope WUs are open. Three verification tasks remain
unchecked in `tasks.md` (production backfill run, production idempotency
check, AC-1 / AC-2 spot-checks) — they require prod credentials and are
not part of this code change.

## Workload / PR boundary

| Field | Value |
|---|---|
| PR-A production code lines | ~270 (`onchain_indexer.py` 63 net + `diag_linking.py` 211) |
| PR-B production code lines | 852 (`backfill_link_agents.py` 325 + `test_indexer_linking.py` 527) |
| 400-line review budget | exceeded on PR-B (~2.1×). Reported as `size:exception`. |
| Chained PRs recommended | Yes (per the user-selected chain strategy) |
| Chain strategy | stacked-to-main (PR-A → master, PR-B → master) |
| Delivery strategy | chained-prs (user-selected) |
| Pre-PR-A baseline | 285 passed, 8 skipped |
| Post-PR-A baseline | 285 passed, 8 skipped (no change) |
| Post-PR-B baseline | 297 passed, 8 skipped (12 new tests, 0 regressions) |
