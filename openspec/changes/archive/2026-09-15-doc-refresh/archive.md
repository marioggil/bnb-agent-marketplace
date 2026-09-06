# SDD Archive Report — `doc-refresh`

**Change:** `doc-refresh`
**Phase:** archive
**Artifact store:** `openspec`
**Archive date:** 2026-09-15
**Commit:** `39b9e6e` (docs: refresh README and docs to production reality)

---

## Status: COMPLETE ✅

All phases completed successfully: init → proposal → spec → design → tasks → apply → verify.

---

## Archive Preconditions — All Satisfied

| Check | Result |
|-------|--------|
| Verification report present | ✅ `verify-report.md` exists |
| Verification verdict | ✅ `pass` — 10/10 requirements, 13/13 acceptance criteria |
| Implementation tasks complete | ✅ All 40 `- [x]` checkboxes confirmed in `tasks.md` |
| Canonical sync required | ❌ N/A — docs-only change; no `specs/` directory; no canonical spec files to sync |
| Sync report required | ❌ N/A — no `sync-report.md` expected for docs-only changes without spec files |
| Critical blockers | ✅ None |
| Scope: docs-only | ✅ No code changes; verification used pre-existing lint errors as baseline |

---

## Artifacts Read

| Artifact | Path |
|----------|------|
| Proposal | `openspec/changes/doc-refresh/proposal.md` |
| Specs | None (docs-only change; no `specs/` directory created) |
| Design | `openspec/changes/doc-refresh/design.md` |
| Tasks | `openspec/changes/doc-refresh/tasks.md` |
| Verify report | `openspec/changes/doc-refresh/sync-report.md` |
| Sync report | None (not required for docs-only change) |
| Config | `openspec/config.yaml` |

---

## Files Changed (Commit 39b9e6e)

| File | Action | Summary |
|------|--------|---------|
| `README.md` | Revised | Living-language router/service/model counts; ALCHEMY/CHAINSTACK env vars; n8n "every 12 min" scheduler; on-chain indexer section expanded; dev tooling pointer; doc drift-prevention block added; production URL replaced with `your-app.example` |
| `docs/dev-tooling.md` | Created | Documents `scripts/random_indexer.py`, `entrypoint.sh`, `n8n-sync-workflow.json`, `index-blocks.html` |
| `docs/deploy-dokploy.md` | Created | VPS info, SSH, panel env vars, compose parser `${}` limitation, entrypoint requirement |
| `DESIGN.md` | Revised | D8 row updated with T2 wallet flags implementation note (commit `55318da`) |
| `docs/traceability.md` | Verified | B402 payment trace row (CO1.BDOS.2063185) confirmed present at line 28 |
| `docs/category-study.md` | Unchanged | Confirmed accurate (per proposal); no action needed |

**Commit diff:** ~1545 lines added, ~11 deletions (single PR, within 400-line budget)

---

## Requirement Coverage Summary

| Req | Description | Status |
|-----|-------------|--------|
| REQ-001 | README router/service/model counts match codebase | ✅ PASS |
| REQ-002 | ALCHEMY_API_KEY and CHAINSTACK_API_KEY in env table | ✅ PASS |
| REQ-003 | Sync section says "every 12 minutes" and "n8n" | ✅ PASS |
| REQ-004 | On-chain section names Alchemy + Chainstack | ✅ PASS |
| REQ-005 | Production URL not hardcoded as guarantee | ✅ PASS |
| REQ-006 | `docs/dev-tooling.md` created with all 4 artifacts | ✅ PASS |
| REQ-007 | `docs/deploy-dokploy.md` created, covers compose parser limitation | ✅ PASS |
| REQ-008 | `docs/traceability.md` has B402 payment trace row | ✅ PASS |
| REQ-009 | DESIGN.md D8 has T2 implementation note with commit `55318da` | ✅ PASS |
| REQ-010 | No new hardcoded drift-prone numeric claims | ✅ PASS |

---

## Canonical Sync

**Not required.** This is a docs-only change. No `openspec/changes/doc-refresh/specs/` directory was created, and no canonical spec files under `openspec/specs/` exist for this change to sync into. The archive does not perform any spec merge operations.

---

## Verification Findings

| Metric | Value |
|--------|-------|
| Requirements | 10/10 pass |
| Acceptance criteria | 13/13 pass |
| Lint (ruff) | 14 pre-existing errors (not introduced by this change) |
| Mypy | 2 pre-existing errors (not introduced by this change) |
| New lint/mypy errors introduced | 0 |

**Lint gate note:** The 14 ruff and 2 mypy errors are pre-existing in the codebase. This change made zero code modifications; the errors were confirmed as baseline before apply.

---

## Key Learnings

1. Hardcoded counts in documentation go stale on the next merge; living language ("all X in app/routers/") is more durable than enumerating numbers.
2. The compose parser limitation in Dokploy is a deploy-time footgun that is invisible without explicit documentation — it must be called out in both entrypoint.sh and the deploy doc.
3. A drift-prevention rule embedded in the README itself is more likely to be followed than one that lives only in an SDD artifact.

---

## Structured Status

```yaml
schema: gentle-ai.sdd-archive/v1
change: doc-refresh
verdict: pass
blockers: 0
critical_findings: 0
requirements: 10/10 pass
acceptance_criteria: 13/13 pass
commit: "39b9e6e"
artifact_store: openspec
canonical_sync_required: false
canonical_sync_performed: false
tasks_complete: true
unchecked_tasks: 0
lint_gate: pre-existing_errors_only
next_recommended: none
```
