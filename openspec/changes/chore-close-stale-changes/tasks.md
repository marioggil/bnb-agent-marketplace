# Tasks: chore-close-stale-changes

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~30 (only files added for the chore itself) |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | single-pr |
| Chain strategy | n/a |

```text
Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: n/a
400-line budget risk: Low
```

---

## Tasks

- [ ] **T1** — Delete `openspec/changes/doc-refresh/` (recursive).
- [ ] **T2** — Delete `openspec/changes/test-copy-fix/` (recursive).
- [ ] **T3** — Verify `openspec/changes/` contains only `archive/` and `chore-close-stale-changes/`.
- [ ] **T4** — Verify `openspec/changes/archive/2026-09-15-doc-refresh/` and `openspec/changes/archive/2026-09-15-test-copy-fix/` still contain all 7 files each.
- [ ] **T5** — Run `uv run pytest` → 285 passed, 8 skipped, 0 failed (baseline preserved, no production diff).
- [ ] **T6** — `git status --short openspec/changes/` shows only the two deletions and the new chore directory contents (no archive modifications).
