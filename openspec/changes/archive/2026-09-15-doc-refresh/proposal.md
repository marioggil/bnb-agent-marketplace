# Proposal: doc-refresh — sync README/docs to production reality

## Change name
`doc-refresh`

## Domain
Documentation

## Status
Draft

---

## Problem statement

README.md, DESIGN.md, and docs/ are stale relative to what shipped. The drift is non-trivial: sections describe features that don't exist, omit features that do, and reference old numbers (agent counts, router counts, env vars). DESIGN.md is accurate; README.md and docs/ need work.

## Existing evidence

- **obs-222** (sdd/doc-refresh/explore, 2026-09-01): full audit — README stale at 17 specific claims, DESIGN accurate, docs/category-study.md accurate, docs/traceability.md accurate. Root artifacts (n8n-sync-workflow.json, scripts/, entrypoint.sh) have no home in docs.
- **obs-43** (deploy, 2026-08-09): dokploy setup documented in session summary, not in README.
- **obs-205** (sync, 2026-08-25): checkpoint token 303446, 100 agents, but README says "every 30 min" (reality: every 12 min via n8n).
- **obs-212** (categories, 2026-08-26): 93% in "other" confirmed, category-study is accurate.
- **obs-276** (hire gap, 2026-09-05): R3 decision documented in code comment, not in README.

## What needs to change

### README.md (primary)

| Section | Current state | Needed |
|---|---|---|
| Stack | Outdated router/service counts | 10 routers, 13 services, 7 models |
| Project tree | Missing routers, services, models | Complete tree |
| Env vars table | Missing ALCHEMY_API_KEY, CHAINSTACK_API_KEY | Full table with both RPC keys |
| Sync scheduler | "Dokploy cron every 30 min" | n8n workflow every 12 min, no full run |
| On-chain indexer | Sketches the feature | Complete section: Alchemy backfill + Chainstack realtime |
| Field source-of-truth | Missing n8n, entrypoint.sh, scripts/ | Add dev tooling section |
| Sync API | Accurate | Keep, add note about /api/sync/flagged |
| Payments | Accurate | Keep |
| CI section | "no CI" — accurate | Keep |
| Alembic | Accurate | Keep |

### DESIGN.md (no changes needed)

Verified byte-for-byte accurate vs implementation. Only add a note about T2 wallet flags being implemented since D8 was written before it shipped.

### docs/category-study.md

No changes. Accurate and current.

### docs/traceability.md

Add entry for B402 payment trace (CO1.BDOS.2063185 → CO1.REQ.2121688 → GET /api/agents/{chain_id}/{token_id}/payments). Currently has one row; add payment trace row.

### New: docs/dev-tooling.md

Consolidate untracked root artifacts that need a home:
- `scripts/` — random_indexer.py, test_bscscan.py
- `entrypoint.sh` — preflight + alembic + uvicorn
- `n8n-sync-workflow.json` — sync scheduler (import into n8n)
- `index-blocks.html` — block index dev UI

### New: docs/deploy-dokploy.md

From obs-43 session summary: Dokploy setup (VPS 194.163.177.206, domain, compose parser gotcha — no `${}` interpolation).

## What is NOT in scope

- Code changes (this is docs-only)
- Changes to DESIGN.md beyond a T2 note
- CI pipeline setup
- Indexer bug fixes (tracked separately)

## Acceptance criteria

1. README.md passes a claim audit: every section reflects current code/deploy state.
2. New docs/dev-tooling.md exists and documents scripts/, entrypoint.sh, n8n workflow, index-blocks.html.
3. New docs/deploy-dokploy.md captures Dokploy-specific setup (compose parser gotcha, env vars, SSH).
4. docs/traceability.md has the B402 payment trace row.
5. DESIGN.md has a T2 implementation note.
6. No new hardcoded claims (numbers, URLs) that could drift — use "current" language where exact values vary.
7. Ruff/mypy still pass (no code changes, but verify no broken imports in docs).

## Risks

- **Low**: docs-only change, no code impact.
- **Medium**: README is the first doc a new developer reads — wrong env vars or command examples could mislead. Audit each code snippet.
- **Low**: production URL (agentmarket.marioggil.xyz) is stable but should be noted as subject to change.

## Open questions

None — existing exploration (obs-222) resolved all product/technical questions.

## Related changes

- `doc-refresh` supersedes the partial `sdd/doc-refresh` explore phase (obs-222).
- Indexer bug fixes (separate change, not in scope here).
