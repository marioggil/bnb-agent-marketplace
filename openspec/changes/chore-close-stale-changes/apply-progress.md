# Apply Progress: chore-close-stale-changes

## Status

`partial` — T1–T4 applied (filesystem operations executed). T5–T6 deferred until commit + verification step runs in your session (requires permission to run `uv run pytest` and `git status` as a verification step before commit).

## Completed tasks

- [x] **T1** — `openspec/changes/doc-refresh/` deleted (recursive).
- [x] **T2** — `openspec/changes/test-copy-fix/` deleted (recursive).
- [x] **T3** — `ls openspec/changes/` confirmed: `archive/` and `chore-close-stale-changes/` only.
- [x] **T4** — `diff -rq` confirmed archive copies remained byte-identical before deletion (only `apply-progress.md`, `archive.md`, `verify-report.md` existed in archive but not in the deleted copies — that was the original archive operation's signature).

## Pending tasks

- [ ] **T5** — `uv run pytest` → expect 285 passed, 8 skipped, 0 failed. (Requires pytest run.)
- [ ] **T6** — `git status --short openspec/changes/` shows only the two deletions and the new chore directory. (Requires git verification.)

## Files changed

```text
openspec/changes/doc-refresh/                    (deleted, 4 files)
openspec/changes/test-copy-fix/                  (deleted, 5 files)
openspec/changes/chore-close-stale-changes/      (added: proposal.md, spec.md, design.md, tasks.md, apply-progress.md)
```

## Verification commands to run before commit

```bash
ls openspec/changes/
# Expected: archive/  chore-close-stale-changes/

uv run pytest
# Expected: 285 passed, 8 skipped, 0 failed

cd /home/mario/Documentos/Bnb_agent && git status --short openspec/changes/
# Expected: D  openspec/changes/doc-refresh/...
#           D  openspec/changes/test-copy-fix/...
#           ?? openspec/changes/chore-close-stale-changes/
```
