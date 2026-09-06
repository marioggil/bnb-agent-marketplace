# Spec: chore-close-stale-changes

**Change:** `chore-close-stale-changes`
**Domain:** SDD hygiene
**Scope:** delete two orphan change directories; archive copies remain authoritative.

---

## Purpose

After `doc-refresh` and `test-copy-fix` were archived to `openspec/changes/archive/2026-09-15-{name}/`, identical duplicates remained in `openspec/changes/{name}/`. Those duplicates are byte-identical to the archive copies (verified with `diff -rq`) and serve no purpose. Removing them makes the active-changes directory clean before starting the new SDD chain (`compliance-flags`, `wallet-activity-pillar`, `agent-score-integration`).

---

## Non-Goals

- No archive-side changes. `openspec/changes/archive/2026-09-15-{name}/` stays untouched.
- No code, no tests, no production artifacts touched.
- No metadata added to the archive copies (no `archive-note.md` etc.).

---

## Acceptance Criteria

1. `openspec/changes/` contains only `archive/` and any active changes added after this chore.
2. The archive directories `archive/2026-09-15-doc-refresh/` and `archive/2026-09-15-test-copy-fix/` remain byte-identical to their pre-chore state.

---

## Requirements

### Requirement: Orphan change directories removed

The two directories `openspec/changes/doc-refresh/` and `openspec/changes/test-copy-fix/` MUST be deleted. After deletion, neither directory exists on disk. The canonical content lives in `openspec/changes/archive/2026-09-15-doc-refresh/` and `openspec/changes/archive/2026-09-15-test-copy-fix/` respectively, which remain untouched.

#### Scenario: post-cleanup inspection

- GIVEN the chore is applied
- WHEN `ls openspec/changes/` runs
- THEN the output contains `archive/` and any post-chore active changes
- AND `openspec/changes/doc-refresh/` does not exist
- AND `openspec/changes/test-copy-fix/` does not exist
- AND `openspec/changes/archive/2026-09-15-doc-refresh/` still exists with all 7 files (`proposal.md`, `spec.md`, `design.md`, `tasks.md`, `apply-progress.md`, `archive.md`, `verify-report.md`)
- AND `openspec/changes/archive/2026-09-15-test-copy-fix/` still exists with all 7 files

#### Scenario: git diff is bounded

- GIVEN the chore is applied
- WHEN `git status --short openspec/changes/` runs
- THEN only deletions appear (no modifications, no additions inside `archive/`).
