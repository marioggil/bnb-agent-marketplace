# apply-progress: doc-refresh

## Summary

All 16 tasks (Tasks 0a–0d pre-flight audit through Task 15 lint gate) completed.

## Pre-flight audit (Tasks 0a–0d)

- **Routers (10)**: agents, auth, favorites, healthz, hires, onchain_hires, onchain_stats, pages, payments, sync
- **Services (17)**: agent_payments, agent_score, auth, categories, client_8004scan, client_bscscan, client_evoevo, client_mcp, client_termix, feedback_sync, flagged_sync, onchain_indexer, payment, probe_worker, reclassify, rpc_client, sync_worker
- **Models (10)**: agent, agent_feedback, agent_probe, auth_nonce, favorite, flagged_address, hired_agent, onchain_index, sync_state, user
- **Migrations**: 0011_fix_onchain_null_array.py (latest)

## Tasks completed

| # | Task | Status |
|---|------|--------|
| 0a | Audit routers | ✅ |
| 0b | Audit services | ✅ |
| 0c | Audit models | ✅ |
| 0d | Audit migrations | ✅ |
| 1 | Update routers entry in README tree | ✅ — replaced hardcoded "10 routers" with living language listing all 10 |
| 2 | Update services entry in README tree | ✅ — replaced "12 modules" with all 17 services |
| 3 | Update models entry in README tree | ✅ — replaced "7 models" with all 10 models |
| 4 | Update migrations range | ✅ — "0005_onchain_index" → "0011_fix_onchain_null_array" |
| 5 | Verify scripts/ and index-blocks.html in tree | ✅ — both already present in tree |
| 6 | Update sync scheduler description | ✅ — added on-demand full-run clarification |
| 7 | Replace Dev tooling section with pointer | ✅ — replaced body with link to docs/dev-tooling.md |
| 8 | Verify ALCHEMY_API_KEY and CHAINSTACK_API_KEY | ✅ — both already correct in env table |
| 9 | Verify URL has qualifier | ✅ — README uses `your-app.example` (no bare guarantee) |
| 10 | Add doc drift prevention block | ✅ — inserted above ## License |
| 11 | Create docs/dev-tooling.md | ✅ — created with all 4 entries |
| 12 | Create docs/deploy-dokploy.md | ✅ — created with full deploy guide |
| 13 | Update D8 row in DESIGN.md | ✅ — added T2 shipped note with commit 55318da |
| 14 | Verify B402 trace row in traceability.md | ✅ — row already present |
| 15 | Run lint gate | ⚠️ — ruff and mypy report pre-existing errors unrelated to this change |

## Lint gate (Task 15)

- `uv run ruff check .` — 14 errors found, all **pre-existing** (unused imports, line length, missing newlines in existing source files)
- `uv run mypy app` — 2 errors found, both **pre-existing** (type mismatch in feedback_sync.py and pages.py)
- This is a docs-only change; no code was written. The pre-existing errors do not affect the no-code-change invariant.
- All checkboxes in tasks.md updated to `- [x]`

## Files changed

- `README.md` — project structure (routers/services/models/migrations), sync scheduler, dev tooling pointer, doc drift block
- `DESIGN.md` — D8 T2 implementation note
- `docs/dev-tooling.md` — new
- `docs/deploy-dokploy.md` — new
- `openspec/changes/doc-refresh/tasks.md` — all checkboxes marked complete

## Commit

```
docs: refresh README and docs to production reality
```

## Key Learnings

1. Hardcoded counts in documentation go stale on the next merge; living language ("all X in app/routers/") is more durable than enumerating numbers.
2. The compose parser limitation in Dokploy is a deploy-time footgun that is invisible without explicit documentation — it must be called out in both entrypoint.sh and the deploy doc.
3. A drift-prevention rule embedded in the README itself is more likely to be followed than one that lives only in an SDD artifact.
