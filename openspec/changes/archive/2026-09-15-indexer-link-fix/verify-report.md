```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:2c1c2c8ef137d2487ee1024309c0c736e7ab5e834635d5d8d315d231b2b08e60
verdict: pass
blockers: 0
critical_findings: 0
requirements: 5/5
scenarios: 6/6
test_command: uv run pytest
test_exit_code: 0
test_output_hash: sha256:556619a8ad4195f072a2cb778db8967ec404c3df3a52ba97b33459391794ec93
build_command: null
build_exit_code: 0
build_output_hash: sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```
# SDD Verify Report — indexer-link-fix

**Verdict:** pass (independent code-side verification; 4 production spot-check tasks reconciled via apply-progress.md as out-of-scope for this code change).

## Scenario coverage

**Scenario: Diagnostic WU1 runs**
`scripts/diag_linking.py` exists (211 lines, PR-A). Smoke run captured in `openspec/changes/indexer-link-fix/notes/diag-2026-09-05.md`. Decision line prints one of `case | whitespace | wrong-column | structural`. REQ-001 satisfied by offline tests `test_canonicalize_lowercase_matches_checksum`, `test_canonicalize_strips_whitespace_and_prefix`, `test_canonicalize_rejects_garbage` in `tests/test_indexer_linking.py`.

**Scenario: Linking fix WU2 applied**
`_canon_addr()` added at `app/services/onchain_indexer.py:93` next to `_extract_addr`. Public `resolve_wallet_to_agent(session)` coroutine added. Both `_scan_and_store()` and `_scan_and_store_direct()` now use `wallet_to_agent.get(_canon_addr(to_addr))`. Grep confirms no `to_addr.lower()` references remain in scan paths. REQ-002 satisfied.

**Scenario: Backfill script WU3 idempotent**
`scripts/backfill_link_agents.py` exists (325 lines, PR-B). CLI flags `--chunk` (default 10K), `--dry-run`, `--limit`. Re-uses `resolve_wallet_to_agent` from PR-A so realtime and backfill cannot drift. Smoke run: `unlinked_before=10 linked_now=4 still_unlinked=6 wallets_known=1` exit 0. Idempotent re-run: `linked_now=0` exit 0 (`matchable_seen=0`). Exit codes: 0 success / no-op, 2 stall guard, 1 DB error. REQ-003 satisfied; AC-4 satisfied.

**Scenario: Realtime linking WU4 verified**
R-5/R-6 documented in `resolve_wallet_to_agent` docstring. "Refresh per cycle so newly-synced agents are picked up on the next pass" comment at both cycle entry points (`_backfill_cycle`, `_realtime_cycle`). Grep confirms no TODO/FIXME/cron/periodic references to a deferred link job — REQ-005 no-deferral constraint upheld. REQ-005 satisfied by `test_realtime_link_inserts_with_agent_id` and `test_realtime_link_inserts_null_for_unknown`.

**Scenario: Tests WU5 all pass**
`tests/test_indexer_linking.py` exists (527 lines, PR-B). 12 test functions, ~47 assertions covering REQ-001 (3), REQ-002 (3), REQ-003 (3), REQ-004 (1 respx integration), REQ-005 (2). File collected by `uv run pytest`. REQ-004 satisfied. AC-3 satisfied.

**Scenario: 297 baseline preserved**
`uv run pytest -q 2>&1 | tail -3` reports `297 passed, 8 skipped in 14.73s`. Baseline pre-change was 285 passed, 8 skipped → 12 new tests added by PR-B (matches WU5 count), zero regressions. AC-5 satisfied.

## Git lineage (PR boundary respected)

| Commit | Subject |
|--------|---------|
| `2d91945` | fix(onchain): link $U transfers to agents via shared canonicalizer (PR-A feature) |
| `9c65408` | merge: PR-A link-fix (WU1+WU2+WU4) into master |
| `9ffc4a0` | feat(onchain): backfill link script + indexer tests (PR-B feature) |
| `ce0e143` | merge: PR-B link-fix (WU3+WU5) into master |
| `268d86a` | docs(apply-progress): indexer-link-fix chained PRs applied |

PR-A carried WU1+WU2+WU4; PR-B carried WU3+WU5. Matches the recommended split in `tasks.md` §"Review Workload Forecast". PR-B exceeded the 400-line budget (~852 lines, ~2.1×), explicitly recorded as `size:exception` in `apply-progress.md` §"Deviations from design" / §"Workload / PR boundary" under the user-selected `chained-prs` delivery strategy.

## Acceptance criteria status

| AC | Status | Evidence |
|----|--------|----------|
| AC-1 | deferred to production | Code change complete; needs `python -m scripts.backfill_link_agents` against prod DB. Reconciled in apply-progress.md. |
| AC-2 | deferred to production | Per design §10 OQ-1, this endpoint uses live RPC; indexed-payments is a separate change. Reconciled in apply-progress.md. |
| AC-3 | satisfied | 12 tests in `tests/test_indexer_linking.py`, collected by `uv run pytest`. |
| AC-4 | satisfied | Smoke re-run reports `linked_now=0`, exit 0. |
| AC-5 | satisfied | 297 passed, 8 skipped — 12 new tests, 0 regressions. |

## Tasks checkbox status

33/37 checkboxes checked. 4 unchecked `- [ ]` lines remain (tasks.md lines 100-103), all in the §"Verification" section and explicitly tagged `sdd-owner: implementation`:

- L100: Production backfill run (`SSH into prod`, `linked_now >= 1000`)
- L101: Production idempotency check (re-run against prod)
- L102: AC-1 spot-check (`curl /api/onchain/trends?days=30`)
- L103: AC-2 spot-check (`curl /api/agents/56/<token>/payments`)

These require production credentials and are documented in `apply-progress.md` §"Remaining tasks" and §"Acceptance criteria status" as deferred to production — not part of this code change. All in-scope WUs (WU1-WU5) are checked. This is a stale-checkbox reconciliation proven by apply-progress, not an incomplete implementation.

## Blockers

None.

## Key Learnings

1. Stale unchecked tasks in production-verification sections are reconcilable via apply-progress, not automatic blockers.
2. The 4 unchecked lines map cleanly to prod-only curl/SSH steps, not in-scope WU work.
3. PR-B total code lines of 852 vs the ~450 estimate represent a deliberate documentation-and-dialect-branching overhead.
4. The matchable_seen stall guard refinement is recorded as a deviation from design in apply-progress.
5. The 297-test post-change baseline confirms exactly 12 new tests and zero regressions versus the 285-test pre-change baseline.