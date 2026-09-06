# SDD Archive Report — `compliance-flags` (Phase 1a-i)

**Change:** `compliance-flags`
**Phase:** archive (Phase 1a-i of a 3-PR chain: 1a-i → 1a-ii → 1b)
**Artifact store:** `openspec` (authoritative; `openspec/` directory present and writeable)
**Archive date:** 2026-09-15
**Implementation commit:** `uncommitted at archive time` (orchestrator will commit after archive; branch head `cd86e1f chore(sdd): close stale changes + scaffold compliance-flags chain` is the scaffolding commit — all 1a-i work lives in the working tree as untracked files plus one modified file)
**Workspace:** `/home/mario/Documentos/Bnb_agent`
**Branch:** `feat/compliance-flags-1a-i` (currently checked out)

---

## Status: PASS ✅

Phase 1a-i (DB schema + pure helper) is complete and verified. The remaining chain — 1a-ii (service + admin router) and 1b (UI gate + additive API fields) — stays active under `openspec/changes/compliance-flags/` and will be archived separately when each sub-PR is verified.

**Note:** Unlike the precedent archives (`test-copy-fix`, `doc-refresh`), this archive does **not** move the `compliance-flags/` folder out of `openspec/changes/`. The parent task explicitly instructs the folder to remain in place because 1a-ii and 1b are still active. The dated archive directory `openspec/changes/archive/2026-09-15-compliance-flags-1a-i/` is a **copy** of the 1a-i-relevant artifacts (proposal, spec, design, all three task files, apply-progress, verify-report, explore) so the 1a-i sub-PR history is preserved as audit trail. The canonical spec at `openspec/specs/agent-compliance/spec.md` is a **copy** (not a move) of the change spec — same flat-spec → new-canonical-domain pattern as the `test-copy-fix` archive.

---

## Archive Preconditions — All Satisfied

| Check | Result |
|-------|--------|
| Verification report present | ✅ `openspec/changes/compliance-flags/verify-report.md` exists (verdict `pass`, blockers `0`, critical findings `0`) |
| Verification verdict clearly passing | ✅ `pass` — no `FAIL`, `BLOCKED`, `CRITICAL`, or verification blockers |
| Proposal artifact present | ✅ `openspec/changes/compliance-flags/proposal.md` |
| Spec artifact present | ✅ `openspec/changes/compliance-flags/spec.md` (flat layout; new canonical domain — see "Canonical Sync") |
| Design artifact present | ✅ `openspec/changes/compliance-flags/design.md` |
| Tasks artifact present | ✅ `openspec/changes/compliance-flags/tasks.md` (umbrella) + `tasks-1a-i.md` (8/8 complete) + `tasks-1a-ii.md` (15 pending, owned by 1a-ii) + `tasks-1b.md` (10 pending, owned by 1b) |
| Apply-progress artifact present | ✅ `openspec/changes/compliance-flags/apply-progress.md` |
| Unchecked implementation tasks for 1a-i (`- [ ]` in `tasks-1a-i.md`) | ✅ none — Final Task Completion Gate re-read confirms 0 unchecked lines in `tasks-1a-i.md`; all 8 are `- [x]` |
| Stale-checkbox reconciliation required | ❌ N/A — `sdd-apply` marked all 8 task boxes in `tasks-1a-i.md` and they are backstopped by apply-progress + verify-report (18/18 targeted passes, 315/8/0 full suite) |
| Canonical sync required | ✅ YES — this change creates a NEW canonical domain (`agent-compliance/`); archive-time sync fallback copies the flat change spec to `openspec/specs/agent-compliance/spec.md` |
| Sync report required | ⚠️ no pre-existing `sync-report.md` — handled by archive-time sync fallback |
| Archive-time sync fallback approval | ✅ Explicit — parent task instructs: "Write the spec.md to the canonical catalog at `openspec/specs/agent-compliance/spec.md` — this is a NEW catalog domain created by this change. The spec is the contract going forward for 1a-ii and 1b." The spec itself (§"Output path note") pre-authorizes this path: "Archive will treat this as a full new domain spec and copy it verbatim to `openspec/specs/agent-compliance/spec.md`." |
| Critical verification issues | ✅ none — explicit CRITICAL/BLOCKED/FAIL overrides are not in play (verify-report notes one WARNING on a tautological assertion in `test_compute_penalty_deterministic`; harmless and recommended for cleanup pre-1a-ii) |
| Destructive merge approval | ❌ N/A — no MODIFIED/REMOVED requirement sections; this is a new full domain spec |
| Missing-artifact partial-archive approval | ❌ N/A — all required 1a-i artifacts (proposal/spec/design/tasks/apply-progress/verify-report) are present |
| Production diff scope (1a-i) | ✅ matches design §1: `app/db/models/agent.py` modified (+col, +check); `app/db/models/agent_compliance.py` new; `app/services/compliance_refresh.py` new (1a-i scope: only `compute_penalty` + `ComplianceRefreshReport` dataclass); `migrations/versions/0012_compliance_penalty.py` new; `tests/test_compliance_penalty.py` new; `tests/test_compliance_models.py` new. NO router, schema, template, static, main.py, or `flagged_sync.py`/`agent_score.py` diff. |
| `actionContext.mode` workspace-planning with no `allowedEditRoots` | ✅ not in play — mode is repo-local |

---

## Artifacts Read

| Artifact | Path | Notes |
|----------|------|-------|
| Proposal | `openspec/changes/compliance-flags/proposal.md` | Umbrella proposal for the 3-PR chain |
| Spec (flat) | `openspec/changes/compliance-flags/spec.md` | Output-path note explicitly directs archive to copy to `openspec/specs/agent-compliance/spec.md` |
| Design | `openspec/changes/compliance-flags/design.md` | §1 confirms the 3-way split; §5 confirms 1a-i scope is migration + model + pure helper only |
| Tasks umbrella | `openspec/changes/compliance-flags/tasks.md` | Stack order: 1a-i → 1a-ii → 1b |
| Tasks 1a-i | `openspec/changes/compliance-flags/tasks-1a-i.md` | 8/8 checked |
| Tasks 1a-ii | `openspec/changes/compliance-flags/tasks-1a-ii.md` | 15 unchecked (owned by 1a-ii) |
| Tasks 1b | `openspec/changes/compliance-flags/tasks-1b.md` | 10 unchecked (owned by 1b) |
| Apply-progress | `openspec/changes/compliance-flags/apply-progress.md` | T1–T8 narrative with RED → GREEN → TRIANGULATE evidence |
| Verify report | `openspec/changes/compliance-flags/verify-report.md` | `verdict: pass`; 18 targeted passes; 315 passed / 8 skipped / 0 failed full suite |
| Explore | `openspec/changes/compliance-flags/explore.md` | Full surface audit |
| Config | `openspec/config.yaml` | `artifact_store: openspec`; `review_budget: 400` |
| Project status / nextRecommended | derived from verify-report structured status (`artifactStore: openspec`, `applyState: all_done`, `dependencies.archive: ready`, `nextRecommended: sdd-archive`) |

---

## Summary of the Change (Phase 1a-i only)

**Domain:** `agent-compliance` (NEW canonical spec). **Sub-PR scope:** DB schema (column + table + constraint + migration) + pure helper (`compute_penalty` + `ComplianceRefreshReport` dataclass) + model (`AgentComplianceFlag`) + column-existence/import tests. **No orchestrator, no admin router, no template, no `ScoreOut` fields, no UI** — all of that lives in 1a-ii and 1b.
**Production diff size:** ~573 lines added (design estimated ~186; size:exception — growth is in docstrings + tests, not production logic).

| File | Action | What |
|------|--------|------|
| `app/db/models/agent.py` | Modified | +`compliance_penalty: Mapped[Decimal]` (Numeric(5,2), NOT NULL, server_default=0); +`CheckConstraint("compliance_penalty >= 0", name="compliance_penalty_nonneg")`. `activity_score` / `wallet_score` byte-identical. |
| `app/db/models/agent_compliance.py` | New (~71 lines) | `AgentComplianceFlag` declarative — mirrors migration 1:1. Not imported in `app/db/models/__init__.py` (lazy discovery per design §2.2). |
| `app/services/compliance_refresh.py` | New (~75 lines) — **1a-i scope only**: constants, `ComplianceRefreshReport` dataclass, pure `compute_penalty(...) -> Decimal`. No service/orchestrator functions. |
| `migrations/versions/0012_compliance_penalty.py` | New (~136 lines) | `down_revision = "0011_fix_onchain_null_array"`; adds `compliance_penalty` + CHECK; creates `agent_compliance_flags` (8 cols, 2 indexes). Symmetric downgrade. |
| `tests/test_compliance_penalty.py` | New (~101 lines) | 9 tests for `compute_penalty` (truth table, cap, determinism, Decimal-not-float, dataclass defaults). |
| `tests/test_compliance_models.py` | New (~172 lines) | 9 tests for `AgentComplianceFlag` import + columns + `compliance_penalty` column + CHECK + regression guards on `activity_score` / `wallet_score` / `quality_score` / `popularity_score`. |

**Forbidden surfaces (must remain untouched — and do):** `app/routers/`, `app/templates/`, `app/static/`, `app/schemas/`, `app/main.py`, `app/services/flagged_sync.py`, `app/services/agent_score.py`. `git diff` on those paths returns empty.

---

## Test Results (from verify-report.md)

| Run | Command | Result |
|-----|---------|--------|
| Targeted | `uv run pytest tests/test_compliance_penalty.py tests/test_compliance_models.py -v` | **18 passed**, 0 failed in 0.15s |
| Full | `uv run pytest` | **315 passed, 8 skipped, 0 failed** in 14.29s (baseline 297 + 18 new = 315; 8 Postgres-only skips unchanged from pre-1a-i baseline) |
| Migration DDL (offline) | `uv run alembic upgrade head --sql` | exit 0; DDL matches spec AC-2 row-for-row including JSONB `server_default='[]'::jsonb` |
| Migration downgrade (offline) | `uv run alembic downgrade 0012_compliance_penalty:0011_fix_onchain_null_array --sql` | exit 0; symmetric reverse (drop indexes, drop table, drop constraint, drop column) |
| Live ORM introspection | `AgentCache.compliance_penalty.type` → `NUMERIC(5, 2)`, `nullable=False`, `default=0`; `compute_penalty` truth table → `(F,F)=0`, `(T,F)=30`, `(F,T)=30`, `(T,T)=50`; `AgentComplianceFlag` columns → 8 names as expected | matches spec |

**Exit codes:** test `0`, alembic upgrade `0`, alembic downgrade `0`.
**Output digests:** `test_output_hash sha256:0ae06c8544ddd451523a73ce427587133dad096a0accac10c543db2a9f6a42e3`; `build_output_hash sha256:e12dfcdd74d8912deb5ddde592ebba25e30e892c67fc8bdb1b19c95626556533`.

---

## Requirement Coverage Summary (1a-i applicable slice)

`openspec/changes/compliance-flags/spec.md` declares 10 requirements and 27 scenarios. **Phase 1a-i covers Req 1 (column), Req 3 (penalty helper), and Req 11 (TDD discipline).** The remaining requirements (Req 2 — orchestrator, Req 4 — case-insensitive match in service code, Req 5 — Hire CTA gate, Req 6 — displayed score formula, Req 7 — stale-data flag, Req 8 — admin endpoints, Req 9 — `ScoreOut` additive fields, Req 10 — auth contract) are owned by 1a-ii (Req 2, 4, 7, 8, 10) and 1b (Req 5, 6, 9).

| # | Requirement | Scenarios owned by 1a-i | Status |
|---|-------------|--------------------------|--------|
| R1 | Storage — `compliance_penalty` column with non-negative floor | 3 / 3 (column exists + type + floor, backfill, negative rejected at DB layer via constraint introspection) | ✅ pass |
| R3 | Penalty math — pure helper `compute_penalty` | 3 / 3 (truth table, cap holds at 50, deterministic + side-effect-free) | ✅ pass |
| R11 | Strict TDD discipline at apply time | 2 / 2 (RED → GREEN for `compute_penalty` captured at T1/T2; full baseline 315 passed / 8 unchanged skipped / 0 failed) | ✅ pass |

**Applicable coverage: 8 / 8 scenarios pass. 0 CRITICAL, 0 BLOCKER, 1 WARNING** (tautological loop in `test_compute_penalty_deterministic:56–58` — harmless; redundant with the `first == second` two lines above; recommended cleanup before 1a-ii merges).

The 19 out-of-scope scenarios are explicitly deferred to 1a-ii and 1b per design §3.5 (e.g. "row exists after refresh" requires the orchestrator from 1a-ii; "dual-flag agent renders disabled CTA" requires the template + handler from 1b).

---

## Deviations from Design / Verify

1. **Line budget exceeded (size:exception).** Design estimated ~186 lines; shipped ~573 added. Growth is in docstrings + tests, not production logic (apply-progress R-1, verify-report concur). Accepted by parent prompt.
2. **Spec AC-9 baseline citation drift (info, not a blocker).** Spec AC-9 cites `285 passed, 8 skipped`; actual pre-1a-i baseline is `297 passed, 8 skipped`. Implementation satisfies the spirit of AC-9 (zero regressions, +18 new passes); verify-report recommends reconciling `285 → 297` on a follow-up. **Not** amended in this archive.
3. **Constraint naming.** SQLAlchemy auto-prefixes the ORM `CheckConstraint` to `ck_agent_cache_compliance_penalty_nonneg`; the migration uses the unprefixed name. Both resolve to the same Postgres constraint; the introspection test matches the suffix only.
4. **WARNING — tautological assertion.** `test_compute_penalty_deterministic:56–58` has `for c in (False, True): for o in (False, True): assert compute_penalty(c, o) == compute_penalty(c, o)`. Harmless (the `first == second` above is load-bearing); recommended cleanup before 1a-ii.

---

## Canonical Sync

**Status:** ✅ Performed as archive-time sync fallback.

The change spec was written flat at `openspec/changes/compliance-flags/spec.md` (not under `specs/agent-compliance/spec.md`). The spec itself (§"Output path note") explicitly directs archive to treat this flat layout as a full new domain spec and copy it verbatim to `openspec/specs/agent-compliance/spec.md`. The parent task reiterates this directive ("Write the spec.md to the canonical catalog at `openspec/specs/agent-compliance/spec.md` — this is a NEW catalog domain created by this change. The spec is the contract going forward for 1a-ii and 1b."). The verify-report structured status reports `dependencies.sync: ready` and `dependencies.archive: ready` with `nextRecommended: sdd-archive`. Together these constitute explicit approval of archive-time sync fallback.

### Operation applied

**New canonical spec created.** `openspec/specs/agent-compliance/` did not exist before this archive (verified — `openspec/specs/` only contained `agent-detail-ui/`, `branding/`, `onchain-indexer/`). The change spec was treated as a full domain spec and copied verbatim to:

```text
openspec/specs/agent-compliance/spec.md
```

The copy is byte-identical to the flat change spec (`sha256` matches after copy). The downstream sub-PRs (1a-ii, 1b) will write their delta specs to `openspec/changes/compliance-flags/specs/agent-compliance/spec.md` (nested layout) and merge into this canonical via `## ADDED Requirements` / `## MODIFIED Requirements` sections.

### ADDED Requirements (effective, since this is a new canonical domain)

The change spec is **new canonical domain content** (no prior `openspec/specs/agent-compliance/spec.md` to merge into), so all 10 requirements are effectively `## ADDED Requirements` for the new domain:

| # | Requirement name | Source heading |
|---|------------------|----------------|
| 1 | Storage — `compliance_penalty` column with non-negative floor | `### Requirement: Storage — compliance_penalty column with non-negative floor` |
| 2 | Storage — per-agent flag table `agent_compliance_flags` | `### Requirement: Storage — per-agent flag table agent_compliance_flags` |
| 3 | Penalty math — pure helper `compute_penalty` | `### Requirement: Penalty math — pure helper compute_penalty` |
| 4 | Refresh orchestrator — `run_compliance_refresh()` chains both phases | `### Requirement: Refresh orchestrator — run_compliance_refresh() chains both phases` |
| 5 | Case-insensitive exact match against `flagged_addresses.address` | `### Requirement: Case-insensitive exact match against flagged_addresses.address` |
| 6 | Hire-CTA gate — server-rendered `disabled` when both flags are true | `### Requirement: Hire-CTA gate — server-rendered disabled when both flags are true` |
| 7 | Displayed score formula on detail page and `/score` endpoint | `### Requirement: Displayed score formula on detail page and /score endpoint` |
| 8 | Stale-data flag derived from `flagged_addresses.updated_at` | `### Requirement: Stale-data flag derived from flagged_addresses.updated_at` |
| 9 | Admin endpoints guarded by `X-API-Key` | `### Requirement: Admin endpoints guarded by X-API-Key` |
| 10 | Score endpoint contract — additive fields on `ScoreOut` | `### Requirement: Score endpoint contract — additive fields on ScoreOut` |
| 11 | Strict TDD discipline at apply time | `### Requirement: Strict TDD discipline at apply time` |

### MODIFIED Requirements

None — no existing canonical requirement blocks were targeted for replacement.

### REMOVED Requirements

None — no destructive removal.

### Active Same-Domain Change Warnings

**None.** No other active change under `openspec/changes/*/specs/agent-compliance/spec.md` exists (the parent `compliance-flags/` directory remains active for 1a-ii and 1b, but those sub-PRs will write their deltas under `openspec/changes/compliance-flags/specs/agent-compliance/spec.md` and merge into the canonical at that time). No collision risk on the `agent-compliance` canonical spec.

### Destructive Merge Guard

Not triggered — only `ADDED` (new canonical domain content). No MODIFIED/REMOVED operations, no replaced blocks, no removed scenarios.

---

## Phase 1a-i Scope Closure

This archive covers **Phase 1a-i ONLY**: the DB schema additions and the pure `compute_penalty` helper. The remaining two sub-PRs stay active under the **same** `openspec/changes/compliance-flags/` umbrella directory:

| Sub-PR | Status | Owns |
|--------|--------|------|
| **1a-i** (this archive) | ✅ archived | DB schema (column + table + CHECK + migration), pure helper, model, column/import tests |
| **1a-ii** | ⏳ active | `refresh_agent_compliance_flags()`, `flagged_data_stale()`, `run_compliance_refresh()` orchestrator, `app/routers/admin.py` with `POST /api/admin/compliance/refresh` + `GET /api/admin/compliance/status`, `app/main.py` mount |
| **1b** | ⏳ active | `pages.py::agent_detail` read-path edit, `agent_detail.html` template edit (OFAC banner + `#hire-cta` disabled), `schemas/score.py` additive fields, `agents.py::get_agent_score` populate, `tests/test_pages.py` extension, `tests/test_compliance_api.py` |

**The `openspec/changes/compliance-flags/` directory MUST remain in place** for 1a-ii and 1b to continue. The dated archive directory is a **copy** of the 1a-i-relevant artifacts, not a move.

---

## Outstanding Tasks for Future Sub-PRs

| Sub-PR | Outstanding unchecked implementation tasks | Notes |
|--------|---------------------------------------------|-------|
| **1a-ii** | **15 / 15** in `openspec/changes/compliance-flags/tasks-1a-ii.md` | Service orchestrator (T1–T3: `refresh_agent_compliance_flags` + idempotence); `flagged_data_stale` (T4–T5); `run_compliance_refresh` chaining (T6–T7); admin router (T8–T13); `main.py` mount (T14); final verification (T15). Applies the §6.R-1 mitigation if apply-time TDD pushes >380 net lines. |
| **1b** | **10 / 10** in `openspec/changes/compliance-flags/tasks-1b.md` | `ScoreOut` additive fields (T1–T4); detail page compliance gate (T5–T7); displayed score formula on the page (T8); regression guards on existing CTA tests (T9); final verification (T10). |

Both sub-PRs will be archived independently under `openspec/changes/archive/2026-09-15-compliance-flags-1a-ii/` and `openspec/changes/archive/2026-09-15-compliance-flags-1b/` (or later dates) when verified.

---

## Structured Status & Action Context Findings

| Field | Value |
|-------|-------|
| `schemaName` | `spec-driven` |
| `changeName` | `compliance-flags` |
| `subPhase` | `1a-i` (of 1a-i + 1a-ii + 1b) |
| `artifactStore` | `openspec` (authoritative; directory present and writeable) |
| `proposal` / `specs` / `design` / `tasks` / `applyProgress` / `verifyReport` | all `done` |
| `taskProgress` (1a-i) | total=8, complete=8, remaining=0, unchecked=`[]` |
| `taskProgress` (deferred to 1a-ii + 1b) | 15 unchecked + 10 unchecked (out of scope for 1a-i) |
| `taskArtifactErrors` | `[]` |
| `applyState` | `all_done` (for 1a-i) |
| `dependencies.apply` / `dependencies.verify` | `all_done` (for 1a-i) |
| `dependencies.sync` | `ready` → **resolved by archive-time sync fallback** (canonical copy at `openspec/specs/agent-compliance/spec.md`) |
| `dependencies.archive` | `ready` → **resolved by this archive** (for 1a-i only) |
| `actionContext.mode` | `repo-local` (no edit-root gating required) |
| `actionContext.workspaceRoot` | `/home/mario/Documentos/Bnb_agent` |
| `actionContext.allowedEditRoots` | n/a (repo-local mode) |
| `isNonAuthoritative` | `false` |
| `nextRecommended` | `orchestrator-commit` (post-archive for 1a-i) → human review → merge `feat/compliance-flags-1a-i` to `main` → start 1a-ii apply on `feat/compliance-flags-db-service` |

---

## Risks

- **R-A1 (info):** Spec AC-9 cites `285 passed, 8 skipped`; actual pre-1a-i baseline is `297 passed, 8 skipped`. Implementation satisfies the spirit of AC-9 (zero regressions, +18 new passes). Orchestrator should reconcile `285 → 297` on a follow-up. **Not** amended in this archive.
- **R-A2 (low):** `compliance_refresh.py` ships with only the 1a-i surface (`compute_penalty` + `ComplianceRefreshReport`). 1a-ii extends it additively (design §1) — no cross-PR collision risk.
- **R-A3 (low):** WARNING in verify-report: tautological loop in `test_compute_penalty_deterministic:56–58`. Harmless; recommended cleanup before 1a-ii merges.
- **R-A4 (info):** Multi-PR archive deviation from precedent. Prior archives (`test-copy-fix`, `doc-refresh`) move the active change folder to `openspec/changes/archive/...`. This archive does **not** move `openspec/changes/compliance-flags/` because 1a-ii and 1b are still active there. The dated archive directory is a **copy** — documented here as audit trail.
- **R-A5 (info):** `openspec/specs/agent-compliance/` is a fresh canonical domain. 1a-ii and 1b will write deltas to `openspec/changes/compliance-flags/specs/agent-compliance/spec.md` (nested) and merge into the canonical via ADDED/MODIFIED.
- **R-A6 (low):** `AgentComplianceFlag` intentionally **not** imported in `app/db/models/__init__.py` (lazy discovery per design §2.2). The test file patches JSONB → JSON at module-import time for sqlite compatibility. 1a-ii's tests do **not** need to repeat this patch.
- **R-A7 (info):** Implementation commit is `uncommitted` at archive time. Orchestrator must commit and push `feat/compliance-flags-1a-i` post-archive.

---

## Blockers

**None.** All acceptance criteria for 1a-i are met, all 8 implementation tasks are complete with TDD evidence, the targeted suite is green (18/18), the full suite baseline is preserved (315 passed, 8 skipped, 0 failed; +18 over the 297-test pre-1a-i baseline), the production diff is in-scope (no forbidden-surface changes), the canonical spec is copied to the new `agent-compliance/` domain, and the umbrella `compliance-flags/` directory remains in place for 1a-ii and 1b.

---

## Archived Path

```text
openspec/changes/archive/2026-09-15-compliance-flags-1a-i/
```

Contents after copy: `proposal.md`, `spec.md`, `design.md`, `tasks.md`, `tasks-1a-i.md`, `tasks-1a-ii.md`, `tasks-1b.md`, `apply-progress.md`, `verify-report.md`, `explore.md`, `archive.md` (this file).

Canonical sync target: `openspec/specs/agent-compliance/spec.md` (created — new full domain spec, byte-identical copy of the flat change spec).

Active change directory (remains in place for 1a-ii + 1b): `openspec/changes/compliance-flags/` — unchanged.

---

## Key Learnings

1. For multi-PR chained changes, the dated archive directory is a **copy**, not a move: the active umbrella folder (`openspec/changes/{change}/`) must remain in place so sibling sub-PRs can continue, while the dated archive captures the history of the sub-PR being retired.
2. Writing the canonical spec at `openspec/specs/{domain}/spec.md` creates the **single source of truth** that downstream sub-PRs merge into via `## ADDED`/`## MODIFIED Requirements` deltas. The flat change spec stays at `openspec/changes/{change}/spec.md` as the original authored contract; the canonical is the merged, post-archive state.
3. When the change spec itself declares "Output path note: archive will copy this verbatim to `openspec/specs/{domain}/spec.md`", that single sentence is sufficient pre-authorization for archive-time sync fallback — no separate `sync-report.md` is required and the verify-report's `dependencies.sync: ready` is the gating signal.
4. For multi-PR chains, the Final Task Completion Gate must read the **sub-PR-specific** tasks file (here `tasks-1a-i.md`), not the umbrella `tasks.md`. Unchecked lines in `tasks-1a-ii.md` and `tasks-1b.md` are explicitly owned by future sub-PRs and must not block the current archive.
5. The chain metadata in the canonical spec (requirements owned by each sub-PR, mapped to design §3.5) lets the verify-report mark out-of-scope ACs as `n/a` rather than `fail`, which keeps the sub-PR's `verdict: pass` honest about which slice was actually exercised.
