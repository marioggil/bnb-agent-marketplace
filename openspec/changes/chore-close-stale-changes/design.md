# Design: chore-close-stale-changes

## Approach

Pure filesystem operation: `rm -r` on the two duplicate directories. No migration, no application logic, no test changes. The archive copies are authoritative.

## Diff shape

```text
openspec/changes/doc-refresh/   (deleted, 4 files: proposal, spec, design, tasks)
openspec/changes/test-copy-fix/ (deleted, 5 files: proposal, spec, design, tasks, apply-progress)
openspec/changes/chore-close-stale-changes/  (added: proposal, spec, design, tasks, archive)
```

The two archive directories remain byte-identical; no diff entry appears for them.

## Verification

```text
ls openspec/changes/
# expected: archive/  chore-close-stale-changes/

diff -rq openspec/changes/archive/2026-09-15-doc-refresh/ <(git show HEAD:openspec/changes/archive/2026-09-15-doc-refresh/)
# expected: empty diff

uv run pytest
# expected: 285 passed, 8 skipped (Postgres-only), 0 failed — baseline preserved
```

No code change, so the test baseline is preserved by construction.

## Risk

- None. Archive copies verified identical to the deleted content before deletion.
