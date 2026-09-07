# SDD Archive Report — `wallet-activity`

**Change:** `wallet-activity`
**Phase:** archive
**Artifact store:** `openspec` (authoritative; `openspec/` directory present and writeable)
**Archive date:** `2026-09-15`
**Commit verified:** `uncommitted` (working tree only; archive pre-commit, no `git commit`/`git push` performed)
**Workspace:** `/home/mario/Documentos/Bnb_agent`
**Branch:** `feat/wallet-activity` (current)

---

## Status: PASS ✅

All phases completed: init → explore → proposal → spec → design → tasks → apply → verify → archive. **Single-PR change** (not a chain). Ready for canonical merge and folder move. 9/9 ACs green, 22/22 tasks `[x]`, scope guard clean (0 lines diff on all forbidden surfaces).

---

## Archive Preconditions — All Satisfied

| Check | Result |
|-------|--------|
| Verify report present + verdict `pass` | ✅ `openspec/changes/wallet-activity/verify-report.md` (blockers `0`, critical `0`, no `FAIL`/`BLOCKED`/`CRITICAL`) |
| Proposal / Spec (flat) / Design / Tasks / Apply-progress / Explore artifacts | ✅ all 6 present |
| Final Task Completion Gate | ✅ re-read `tasks.md` post-archive: `grep -c '^\- \[x\]'` = `22`, `grep -c '^\- \[ \]'` = `0` |
| Stale-checkbox reconciliation | ❌ N/A — all 22 boxes marked `[x]` by `sdd-apply` + backstopped by apply-progress + verify-report |
| Canonical sync (`sync-report.md`) | ⚠️ no prior `sync-report.md` — handled by archive-time sync fallback (see "Canonical Sync"); parent delegated phase prompt explicitly instructs: "Create canonical spec at `openspec/specs/wallet-activity/spec.md` by copying the flat spec (new domain)" |
| Destructive merge approval | ❌ N/A — new full domain spec (single ADDED, no MODIFIED/REMOVED operations) |
| Critical verification issues | ✅ none (verify-report `next_recommended: sdd-archive`) |
| Scope guard — forbidden surfaces | ✅ `git diff master -- migrations/ app/db/models/agent.py app/services/compliance_refresh.py app/main.py app/services/agent_score.py app/services/flagged_sync.py app/routers/admin.py app/static/` returns **0** lines |
| Scope guard — allowed surfaces | ✅ diff limited to the 9 expected paths (see "Files Changed" below) |
| `actionContext.mode` workspace-planning with no `allowedEditRoots` | ✅ not in play — repo-local mode |

---

## Artifacts Read

| Artifact | Path |
|----------|------|
| Proposal | `openspec/changes/wallet-activity/proposal.md` |
| Spec (flat) | `openspec/changes/wallet-activity/spec.md` |
| Design | `openspec/changes/wallet-activity/design.md` |
| Tasks | `openspec/changes/wallet-activity/tasks.md` |
| Apply-progress | `openspec/changes/wallet-activity/apply-progress.md` |
| Verify report | `openspec/changes/wallet-activity/verify-report.md` |
| Explore | `openspec/changes/wallet-activity/explore.md` |
| Config | `openspec/config.yaml` |
| Format reference | `openspec/changes/archive/2026-09-15-test-copy-fix/archive.md` |
| Canonical precedent | `openspec/specs/agent-compliance/spec.md` |

---

## Summary of the Change

**Domain:** `wallet-activity` — new canonical domain (per-agent on-chain wallet sub-score derived from creator + owner wallet activity over the existing 90-day `TRACK_WINDOW_DAYS`).

**Scope:** Pure helper + async aggregation + additive `ScoreOut` fields + additive UI chip on the agent detail page. **NO** mutation of `activity_score`, `pillars`, `composite_score`, `wallet_score`, or `materialize_score()`. **NO** scheduler, **NO** listing-page rollout, **NO** OFAC coverage, **NO** new migration, **NO** new `agent_cache` column, **NO** new index.

**Architecture:** Parallel, additive. `compute_wallet_activity_score(...)` = per-wallet pillar (0.5×events/30 + 0.3×counterparties/10 + 0.2×30/recency_days) + composition (0.5×creator + 0.3×owner + 0.2×track_record_score) + Bayesian shrinkage k=3 toward prior 50, quantized via `ROUND_HALF_EVEN` to 2dp `Decimal`. `fetch_wallet_signals(session, agent_id)` queries `OnchainTransfer` (only existing on-chain table with indexed wallet columns — `ix_onchain_transfers_from` / `ix_onchain_transfers_to` per migration `0005_onchain_index`), normalizes via `.strip().lower()` at the SQL boundary (verbatim precedent `app/services/compliance_refresh.py:117-118`), and collapses to **one** SQL query when `LOWER(creator) == LOWER(owner)`. The route + page route swallow `fetch_wallet_signals` failures identically to how `onchain_stats["transfers"]` already swallows transfer-fetch errors — `wallet_activity_score=None`, `wallet_activity_breakdown=None`, `creator_is_owner=False`; existing fields stay byte-identical.

### Files Changed (Working Tree, Pre-Commit)

| File | Action | Lines | Summary |
|------|--------|-------|---------|
| `app/services/wallet_activity.py` | NEW | +371 | Pure helpers (`compute_wallet_activity_score`, `creator_wallet_pillar_score`, `owner_wallet_pillar_score`, `is_neutral_pillar`, `compute_wallet_activity_breakdown`) + async aggregation (`fetch_wallet_signals`, `_query_wallet`) + frozen dataclasses + `ZERO_ADDRESS` constant. Mirrors `app/services/agent_score.py` structure. |
| `app/schemas/score.py` | Edited | +10 | Three additive `ScoreOut` fields appended AFTER `displayed_activity_score` (preserves existing-key byte order): `wallet_activity_score: float \| None`, `wallet_activity_breakdown: dict[str, Any] \| None`, `creator_is_owner: bool = False`. |
| `app/routers/agents.py` | Edited | +39 | `get_agent_score` populates 3 new fields in a new `try/except`. **Existing `ScoreOut(...)` construction for non-wallet fields is byte-identical** to pre-change fixture. |
| `app/routers/pages.py` | Edited | +43 | `agent_detail` calls fetch + helper inside existing `try/except onchain_stats`; passes 3 new template locals. |
| `app/templates/pages/agent_detail.html` | Edited | +21 | `<div class="wallet-activity-chip">` inside existing `#activity-score` (line ~188). Renders `Wallet activity: NN.NN/100 (creator NN.NN, owner NN.NN, track NN.NN)` + `creator == owner` label; `n/a/100` fallback. One new CSS class (design §7 R-8 relaxation). |
| `tests/test_wallet_activity.py` | NEW | +485 | 27 pure + SQL cases; parametrized per design §3.4/§4; pinned `Decimal` equality; query-count invariants via `before_cursor_execute` listener. |
| `tests/test_wallet_activity_api.py` | NEW | +329 | 6 API cases including byte-identical frozen-fixture assertion for `GET /api/agents/56/101/score`. |
| `tests/test_wallet_activity_pages.py` | NEW | +219 | 7 page cases: chip render, `creator == owner` label, `n/a` fallback, 3 listing-page negative assertions, listing-page query-count listener. |
| `tests/fixtures/score_response_reference.json` | Edited | +35 | NEW for AC-7. Three documented drifts per design §8.2: `"80"→"80.00"`, `+00:00→Z`, `age_months` precision. |

**Totals:** 484 lines production + 1033 lines tests + 35 lines fixture. **9 files changed, 1552 insertions.** Single-PR boundary (`chained: no`, no `size:exception` required).

### Scope Guard (Diff Against `master`)

| Surface | Diff |
|---------|------|
| `app/services/wallet_activity.py` | +371 (NEW, allowed) |
| `app/schemas/score.py` | +10 (allowed) |
| `app/routers/agents.py` | +39 (allowed) |
| `app/routers/pages.py` | +43 (allowed) |
| `app/templates/pages/agent_detail.html` | +21 (allowed) |
| `tests/test_wallet_activity.py` | +485 (NEW, allowed) |
| `tests/test_wallet_activity_api.py` | +329 (NEW, allowed) |
| `tests/test_wallet_activity_pages.py` | +219 (NEW, allowed) |
| `tests/fixtures/score_response_reference.json` | +35 (allowed) |
| **`migrations/`** | **0 (forbidden)** |
| **`app/db/models/agent.py`** | **0 (forbidden)** |
| **`app/services/compliance_refresh.py`** | **0 (forbidden)** |
| **`app/main.py`** | **0 (forbidden)** |
| **`app/services/agent_score.py`** | **0 (forbidden)** — `wallet_activity.py` reuses `TRACK_WINDOW_DAYS` + `ZERO_ADDRESS` (no mutation) |
| **`app/services/flagged_sync.py`** | **0 (forbidden)** |
| **`app/routers/admin.py`** | **0 (forbidden)** |
| **`app/static/`** | **0 (forbidden)** |

### Test Results

| Run | Result |
|-----|--------|
| Targeted (`tests/test_wallet_activity*.py`) | 40 passed, 0 failed |
| Full (`uv run pytest`) | **388 passed, 8 skipped (Postgres-only), 0 failed** (348 baseline + 40 new, 0 regressions) |
| Helper invocation `(0, 0, 30, False, 0, 0, 30, False, Decimal("50"))` | `Decimal('50.00')` (AC-3 wash-out) |
| `ScoreOut.model_fields` order | `[chain, token, activity_score, compliance_penalty, displayed_activity_score, wallet_activity_score, wallet_activity_breakdown, creator_is_owner, pillars, breakdown]` (existing keys preserved) |
| Test exit codes | test `0`, build `0` |

### Acceptance Criteria — Final Status

| AC | Description | Status |
|----|-------------|--------|
| **AC-1** | Helper returns `Decimal` quantized to 2dp via `ROUND_HALF_EVEN` | ✅ pass |
| **AC-2** | Pillar truth table (parametrized: 7 creator + 3 owner rows, literal `Decimal` equality) | ✅ pass |
| **AC-3** | Neutral pillar = 50 when address is null (flag load-bearing) | ✅ pass |
| **AC-4** | Shrinkage k=3 with neutral-wallet exclusion (n=0/1/2/3 branches) | ✅ pass |
| **AC-5** | `creator_is_owner` via `LOWER(addr) == LOWER(addr)`; 4 SQL query-count invariants | ✅ pass |
| **AC-6** | 90-day window matches `TRACK_WINDOW_DAYS` (90-day-old row included, 100-day-old excluded) | ✅ pass |
| **AC-7** | `ScoreOut` additive fields; existing fields byte-identical via frozen fixture (7 keys, key order + values) | ✅ pass |
| **AC-8** | UI chip renders with breakdown + `creator == owner` label + `n/a` fallback; 3 listing-page negative assertions | ✅ pass |
| **AC-9** | Full baseline preserved at 348-pass floor (`388 passed, 8 skipped, 0 failed`) | ✅ pass |

**Totals:** 9/9 ACs pass, 25/25 scenarios, 0 CRITICAL, 1 info warning (W-1), 0 BLOCKER.

### Documented Deviations (Authorized by Design)

1. **Frozen-fixture drift** (`tests/fixtures/score_response_reference.json`): three documented updates per design §8.2 — `"80"→"80.00"` (sqlite `Numeric(5,2)`), `+00:00→Z` (Pydantic v2 UTC), `age_months` precision (frozen-time delta). All byte-stable post-drift; AC-7 invariant holds.
2. **Pillar unit scaling** — implementation scales unit-space sum by 100 so saturated pillar = `Decimal("100.00")` (matches spec scenarios); truth-table tests pin the scaled values.
3. **`recency_days=None` short-circuit** — explicit `if recency_days is None or recency_days <= 0: recency_term = 0` branch (equivalent to design's "max denominator cap" rule, more readable).
4. **One new CSS class `.wallet-activity-chip`** — design §7 R-8 explicitly relaxes spec's "no new CSS classes" rule for this single wrapper. No `app/static/css/` file touched.
5. **`compute_wallet_activity_score` is keyword-only** (`*,` separator) — flags self-documenting call sites; design §3.1 shows positional but all in-repo callers use kwargs. Verify report W-1 notes this is info, not blocking.

---

## Canonical Sync

**Status:** ✅ Performed as archive-time sync fallback.

The change spec was written flat at `openspec/changes/wallet-activity/spec.md`. The spec itself (§"Output path note") and the design (§"Output path note") explicitly direct archive to copy it verbatim to `openspec/specs/wallet-activity/spec.md`. The parent delegated phase prompt instructs archive to do exactly this. Verify-report structured status reports `next_recommended: sdd-archive` with `dependencies.archive: ready`.

### Operation Applied

**New canonical spec created.** `openspec/specs/wallet-activity/` did not exist before this archive (prior `openspec/specs/` contained only `agent-compliance`, `agent-detail-ui`, `branding`, `onchain-indexer`). The change spec was treated as a full new domain spec and copied **byte-identical** (MD5 `21cf5106346ded081e3763bf4e4dc320` on both sides) to:

```text
openspec/specs/wallet-activity/spec.md
```

### ADDED Requirements (7 — full new domain content)

| # | Requirement name |
|---|------------------|
| 1 | Pure helper — `compute_wallet_activity_score` with per-wallet `is_neutral` flags |
| 2 | Aggregation — `fetch_wallet_signals` returns a structured per-wallet triple |
| 3 | `ScoreOut` additive contract — three new fields, existing fields byte-identical |
| 4 | Detail page read path — `agent_detail` calls the fetch + helper inside the existing try/except |
| 5 | UI chip renders inside `#activity-score` with breakdown + `creator == owner` label |
| 6 | Listing pages MUST stay wallet-chip-free |
| 7 | Strict TDD discipline at apply time |

### MODIFIED / REMOVED Requirements

None — no existing canonical requirement blocks targeted for replacement; no destructive removal.

### Active Same-Domain Change Warnings

None. `wallet-activity` is the only active change touching the `wallet-activity` domain. (`test-copy-fix`, `indexer-link-fix`, `logo-v2`, `doc-refresh` already in `openspec/changes/archive/`; `compliance-flags` already in `archive/2026-09-15-compliance-flags/`.)

### Destructive Merge Guard

Not triggered — single ADDED operation only. The full spec content was copied verbatim (MD5 verified); every `### Requirement` and `#### Scenario` heading preserved exactly as written.

---

## Single-PR Closure (Not a Chain)

This change is a **single-PR change**, explicitly NOT part of an SDD chain (`tasks.md` `Review Workload Forecast`: "Chained PRs recommended: No"; `chain_strategy: stacked-to-main`). The umbrella directory `openspec/changes/wallet-activity/` is moved **in full** — all 7 original artifacts (`proposal.md`, `spec.md`, `design.md`, `tasks.md`, `apply-progress.md`, `verify-report.md`, `explore.md`) plus this `archive.md` (8 total) move into `openspec/changes/archive/2026-09-15-wallet-activity/`. No `wallet-activity-1a`, `wallet-activity-1b`, etc. sub-change to handle separately. The active directory is removed after move.

---

## Folder Move (Executed)

```text
openspec/changes/wallet-activity/   →   openspec/changes/archive/2026-09-15-wallet-activity/
```

`openspec/changes/archive/` already existed with 5 prior archives. Move was atomic via `mv` (empty `2026-09-15-wallet-activity` placeholder rmdir'd first, then source renamed). Active change directory `openspec/changes/wallet-activity/` is **gone** post-move.

---

## Structured Status & Action Context

| Field | Value |
|-------|-------|
| `changeName` | `wallet-activity` |
| `artifactStore` | `openspec` (authoritative; `openspec/` writeable) |
| `proposal` / `specs` / `design` / `tasks` / `explore` / `applyProgress` / `verifyReport` | all done |
| `taskProgress.total` / `complete` / `remaining` | `22` / `22` / `0` |
| `taskProgress.unchecked` | `[]` |
| `applyState` | `all_done` |
| `dependencies.sync` | `ready` → resolved by archive-time sync fallback |
| `dependencies.archive` | `ready` → resolved by this archive |
| `dependencies.apply` / `verify` | `all_done` |
| `actionContext.mode` | repo-local (no `workspace-planning`) |
| `actionContext.workspaceRoot` | `/home/mario/Documentos/Bnb_agent` |
| `actionContext.allowedEditRoots` | `app/`, `tests/`, `openspec/changes/wallet-activity/` (now archive), `openspec/specs/wallet-activity/` |
| `isNonAuthoritative` | `false` |
| `nextRecommended` | (post-archive) `orchestrator-commit` |

---

## Risks

- **R-A1 (info):** New canonical domain. Future changes touching `wallet-activity` should be written under `openspec/changes/{next-change}/specs/wallet-activity/spec.md` (nested) and merged via ADDED/MODIFIED/REMOVED ops.
- **R-A2 (low):** Frozen-fixture drift (3 documented lines, design §8.2) is real and intentional. Future AC-7 assertions must use this fixture or document further drift.
- **R-A3 (info):** 8 Postgres-only skipped tests remain gated on `RUN_POSTGRES_TESTS=1`; archive does not alter skip behaviour.
- **R-A4 (low):** Info-only W-1 — `compute_wallet_activity_score` is keyword-only; if external tooling expects positional, signature needs follow-up.
- **R-A5 (info):** Commit state is **pre-commit**; archive did NOT commit, push, or modify production code. Orchestrator owns the `git add` + `git commit` + `git push` for the PR.

---

## Blockers

**None.** All ACs met, all 22 tasks `[x]`, production diff confined to the 9 allowed surfaces (0 lines on every forbidden surface), baseline preserved (388 passed / 0 failed / 8 Postgres-only skipped), all assertions real (literal `Decimal` equality at helper boundary, real query-count invariants via SQLAlchemy listener), single-PR boundary matches `chain_strategy: stacked-to-main`, archive-time sync fallback performed per the in-spec and in-design pre-authorization plus explicit parent-task instruction.

---

## Archived Path

```text
openspec/changes/archive/2026-09-15-wallet-activity/
```

**Contents after move (8 files):** `proposal.md`, `spec.md`, `design.md`, `tasks.md`, `apply-progress.md`, `verify-report.md`, `explore.md`, `archive.md` (this file).

**Canonical sync target:** `openspec/specs/wallet-activity/spec.md` (created — new full domain spec, byte-identical copy of the flat spec, MD5 `21cf5106…` verified).

**Active change directory** `openspec/changes/wallet-activity/` is removed after move.

---

## Next Recommended

`orchestrator-commit` — archive hands back to orchestrator for `git add` + `git commit` on `feat/wallet-activity` + `git push` to open the PR. Archive itself does NOT commit, push, or modify production code per parent-task constraint. Pre-commit orphan artifacts visible to `git status` are listed in the archive context for orchestrator convenience.

---

## Key Learnings

1. The **Final Task Completion Gate** is the only safe check for archive-readiness — re-reading the persisted `tasks.md` immediately before any sync fallback or folder move (`grep -c '^\- \[x\]'` returns 22, `grep -c '^\- \[ \]'` returns 0) blocks any silent apply-phase checkbox regression that the verify report's `taskProgress.unchecked: []` field alone might miss under unusual trust assumptions.
2. For a **single-PR (non-chain)** SDD change with no chained sub-directories, the active change folder can be moved atomically via a single `mv openspec/changes/<change> openspec/changes/archive/YYYY-MM-DD-<change>` — no per-sub-PR ordering, no chain-rollup, and the canonical sync falls naturally to a single new-domain `cp` because there is no merge semantics to apply.
3. Pairing a **frozen-time fixture** byte-identical assertion with explicit **drift documentation** in the spec/design (`design.md` §8.2) and the verify-report prevents future silent fixture weakening — the contract is "document first, capture second" rather than "capture and forget".
4. **Scope-guard proof by `git diff master -- <forbidden-paths>` returning 0 lines** is the cleanest signal that production code stayed byte-identical on forbidden surfaces — pairing it with a positive `git diff master --stat -- <allowed-paths>` (showing the actual 9 files / 1552 insertions) confirms the allowed diff is actually present rather than a no-op.
5. When the change spec is written flat at `openspec/changes/<change>/spec.md` (parent-task instruction rather than nested `specs/<domain>/spec.md` layout), the **parent-task explicit archive instruction** ("Create canonical spec at `openspec/specs/wallet-activity/spec.md` by copying the flat spec") is the load-bearing signal that authorizes archive-time sync fallback in the absence of a separate `sdd-sync` phase report.
