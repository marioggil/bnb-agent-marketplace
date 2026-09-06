# Proposal: chore-close-stale-changes — clean orphan change copies

## Change name

`chore-close-stale-changes`

## Domain

SDD hygiene

## Status

Draft

---

## Problem

After `doc-refresh` and `test-copy-fix` were archived to `openspec/changes/archive/2026-09-15-{name}/`, identical copies remained in `openspec/changes/{name}/`. The duplicate directories contained the same `proposal.md`, `spec.md`, `design.md`, `tasks.md` as the archive (verified with `diff -rq`), missing only the post-apply files (`apply-progress.md`, `archive.md`, `verify-report.md`). They were orphans, not active changes.

The orphan copies confused the change-selection step before the chain of three new SDD changes (`compliance-flags`, `wallet-activity-pillar`, `agent-score-integration`), making it look like there were still active drafts.

## Scope

**In**: delete `openspec/changes/doc-refresh/` and `openspec/changes/test-copy-fix/` (the duplicates). The canonical content lives in `openspec/changes/archive/2026-09-15-{name}/`.

**Out**: any change to the archive copies themselves. No code, no tests, no docs outside of the two duplicate directories.

## Acceptance criteria

1. `ls openspec/changes/` shows only `archive/` (and any new active changes added after this chore).
2. `diff -rq openspec/changes/archive/2026-09-15-doc-refresh openspec/changes/archive/2026-09-15-test-copy-fix` shows no changes (the archive copies remain untouched and authoritative).
3. `git diff` for this change touches at most the two deleted directories.

## Risks

- **None**: the archive copies are byte-identical to the deleted content (verified). No information loss.

## Out of scope

- Any production code change.
- Any test change.
- Any archive-side cleanup.
