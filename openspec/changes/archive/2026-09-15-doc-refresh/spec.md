# SDD Spec — doc-refresh

**Change:** `doc-refresh`  
**Scope:** Documentation-only refresh of README.md, DESIGN.md, docs/traceability.md; addition of docs/dev-tooling.md and docs/deploy-dokploy.md  
**Non-goals:** No code changes; no CI setup; DESIGN.md stays accurate (T2 note only)

---

## Background

README.md was written at an earlier stage and has drifted from the current production deployment. DESIGN.md is byte-for-byte accurate and requires only a T2-implemented note. The proposal (openspec/changes/doc-refresh/proposal.md) is the source of truth for what needs to change.

This spec constrains and formalises the proposal's acceptance criteria into testable requirements.

---

## Requirements

### REQ-001: README.md — accurate counts

README.md MUST reflect the current codebase counts. The following sections MUST be updated:

| Section | Required update |
|---|---|
| **Stack / Project tree — routers** | List all 11 routers: `agents`, `auth`, `favorites`, `healthz`, `hires`, `onchain_hires`, `onchain_stats`, `pages`, `payments`, `sync`, plus the 11th router (verify current codebase). |
| **Stack / Project tree — services** | List all services: `client_8004scan`, `sync_worker`, `categories`, `auth`, `payment`, `agent_payments`, `onchain_indexer`, `rpc_client`, `client_bscscan`, `client_evoevo`, `client_mcp`, `client_termix`, `flagged_sync`, plus any additional services present in `app/services/`. |
| **Stack / Project tree — models** | List all 11 models: `agent`, `auth_nonce`, `favorite`, `hired_agent`, `onchain_index`, `sync_state`, `user`, `agent_feedback`, `agent_probe`, `flagged_address`, plus any additional models. |
| **Stack / Project tree — migrations** | List migration range as `0001_initial … 0011_onchain_array` (or current latest). |
| **Stack / Project tree — scripts** | Add `scripts/` (random_indexer.py) and `index-blocks.html` to the tree. |

> The counts listed above are the production snapshot as of this change. The README MUST NOT hard-code agent counts or x402 percentages that vary per run; use "production catalog" or "current" language.

**Scenario: README counts audit**

- GIVEN a reviewer reads README.md after this change lands
- WHEN they compare router, service, and model counts to `app/routers/`, `app/services/`, and `app/db/models/`
- THEN every listed name exists in the corresponding directory

---

### REQ-002: README.md — env vars table completeness

README.md env vars table MUST include all variables. The following MUST be added if absent:

| Var | Required |
|---|---|
| `ALCHEMY_API_KEY` | YES — activates on-chain backfill and indexer loop |
| `CHAINSTACK_API_KEY` | YES — on-chain realtime worker |

The existing `docs/` section or README section for env vars MUST NOT reference `${}` interpolation in docker-compose, as the Dokploy compose parser does not support it (documented in entrypoint.sh).

**Scenario: env vars audit**

- GIVEN a developer reads README.md env vars table
- WHEN they copy `.env.example` and fill in values
- THEN both `ALCHEMY_API_KEY` and `CHAINSTACK_API_KEY` are present with a description matching their purpose

---

### REQ-003: README.md — sync scheduler accuracy

README.md sync section MUST accurately describe the current scheduler:

- The schedule is owned by **n8n** (`n8n-sync-workflow.json`).
- Interval: **every 12 minutes** (not cron, not "every 30 min").
- Logic: `GET /api/sync/status` first; `POST /api/sync` incremental only when idle; **no full run in the schedule**.
- Full re-walk is on-demand only (CLI or Sync API).

**Scenario: sync section accuracy**

- GIVEN a developer reads the README sync section
- WHEN they follow the description to understand when agents are synced
- THEN the description matches the n8n workflow (every 12 min, incremental-on-idle, no scheduled full run)

---

### REQ-004: README.md — on-chain indexer completeness

README.md on-chain indexer section MUST include both workers:

| Worker | Provider | Purpose |
|---|---|---|
| Backfill | **Alchemy** (`ALCHEMY_API_KEY`) | Historical block walk from genesis |
| Realtime | **Chainstack** (`CHAINSTACK_API_KEY`) | New blocks as they land |

Activation condition: indexer loop starts only when `ALCHEMY_API_KEY` is set. Both keys absent → indexer disabled.

**Scenario: on-chain section accuracy**

- GIVEN a developer reads README.md on-chain indexer section
- WHEN they set only `CHAINSTACK_API_KEY` (no `ALCHEMY_API_KEY`)
- THEN they understand the indexer will not start and why

---

### REQ-005: README.md — production URL stability note

README.md MUST note the production URL as current but subject to change. The URL `agentmarket.marioggil.xyz` MUST NOT appear as a hard guarantee; it SHOULD appear with a qualifier such as "at time of writing" or "current deployment".

**Scenario: URL stability**

- GIVEN a developer reads README.md
- WHEN they look for the live URL to test against
- THEN the URL is present but clearly labelled as the current deployment (not a permanent reference)

---

### REQ-006: New — docs/dev-tooling.md

A new file `docs/dev-tooling.md` MUST be created. It MUST document each of the following root artifacts that have no other home:

| Artifact | Content required |
|---|---|
| **`scripts/random_indexer.py`** | Purpose: random-block $U transfer indexer against the production API. Dedupe via SQLite (`data/indexer_used_blocks.db`). Usage: `--from`, `--to`, `--count`. |
| **`entrypoint.sh`** | Purpose: container preflight (rejects missing `DATABASE_URL`, `SECRET_KEY`, and `change-me*` secrets), then `alembic upgrade head`, then `uvicorn`. Dokploy compose-parser caveat (no `${}` interpolation). |
| **`n8n-sync-workflow.json`** | Purpose: the sync scheduler. Runs every 12 min. GET `/api/sync/status` → if idle POST `/api/sync` incremental. Import into n8n, set `SYNC_API_KEY` variable. |
| **`index-blocks.html`** | Purpose: standalone dev UI. Posts a start block to the n8n `index-blocks` webhook (indexes 2000 blocks per run). Tells user where to resume. |

**Scenario: dev tooling documentation exists**

- GIVEN a new developer clones the repo
- WHEN they look in `docs/` for developer-facing operational docs
- THEN `docs/dev-tooling.md` exists and covers all four artifacts above

---

### REQ-007: New — docs/deploy-dokploy.md

A new file `docs/deploy-dokploy.md` MUST be created. It MUST capture:

1. **Dokploy VPS** — host IP, domain, purpose.
2. **SSH access** — how the deploy key is configured.
3. **Environment variables** — which vars are set in the Dokploy panel (not interpolated via `${}` in compose).
4. **Compose parser gotcha** — Dokploy's compose parser does not support `${}` variable interpolation in the `environment:` block; env vars must be set in the panel directly.
5. **Entrypoint requirement** — `DATABASE_URL` and `SECRET_KEY` must be set in the panel; `entrypoint.sh` validates these at container start.

> Specific IP/domain values SHOULD be noted as current at time of writing and reviewed before publishing externally.

**Scenario: Dokploy deploy doc exists**

- GIVEN a team member needs to replicate or audit the Dokploy deployment
- WHEN they read `docs/deploy-dokploy.md`
- THEN they can understand the VPS setup, panel env var requirements, and the compose parser limitation without needing to search session logs

---

### REQ-008: docs/traceability.md — B402 payment trace row

`docs/traceability.md` MUST add a row for the B402 payment trace:

| Design | Requirement | Repo artifact |
|---|---|---|
| CO1.BDOS.2063185 | CO1.REQ.2121688 | `GET /api/agents/{chain_id}/{token_id}/payments` — agent payment history (U token transfers to `agent_wallet`) |

The row already exists in the file; this requirement verifies it remains present after the change.

**Scenario: B402 trace registered**

- GIVEN a reviewer audits `docs/traceability.md`
- WHEN they look for the payment trace CO1 pair
- THEN the row for CO1.BDOS.2063185 / CO1.REQ.2121688 is present

---

### REQ-009: DESIGN.md — T2 implementation note

DESIGN.md MUST include a brief note confirming that **T2 wallet flags** have been implemented since D8 was written. The note SHOULD appear in the Decisions table or as a footnote to D8.

The existing D8 row ("T2 wallet flags — CSS defined, not rendered until flagged-address data source lands") is now outdated; it MUST be updated to reflect that the feature shipped in commit `55318da` and the `/flagged` page exists (sync not yet run in production).

**Scenario: T2 note added**

- GIVEN a developer reads DESIGN.md D8
- WHEN they look for wallet risk flags status
- THEN they see a note that T2 wallet flags have been implemented (with a commit reference) rather than being pending

---

### REQ-010: No new hardcoded drift-prone claims

README.md MUST NOT introduce new hardcoded numeric claims (agent counts, router counts, sync checkpoint numbers, specific wallet addresses) that are likely to drift. Where exact values vary over time, the README MUST use "current", "at time of writing", or "verified against `app/routers/`" language instead of hardcoded counts.

**Scenario: no new hardcoded numbers**

- GIVEN a developer reads README.md 6 months after this change lands
- WHEN they see a numeric claim (e.g., "13 services")
- THEN that number is either derived from the codebase or qualified as approximate

---

## Non-goals (confirmed)

The following are explicitly out of scope and MUST NOT be touched:

| Item | Reason |
|---|---|
| Code changes | Docs-only change; code stays as-is |
| CI pipeline setup | No `.github/` directory; acknowledged "no CI" remains accurate |
| `docs/category-study.md` | Already accurate; no changes needed |
| New canonical spec files | This change writes delta specs only; canonical spec write is archive's job |

---

## Acceptance criteria (consolidated)

| # | Criterion | Testable outcome |
|---|---|---|
| AC1 | README.md router list matches `app/routers/` | `ls app/routers/*.py | wc -l` count and names match README |
| AC2 | README.md service list matches `app/services/` | `ls app/services/*.py | wc -l` count and names match README |
| AC3 | README.md model list matches `app/db/models/` | Count matches; all listed names exist |
| AC4 | `ALCHEMY_API_KEY` and `CHAINSTACK_API_KEY` in README env vars table | Both vars present with non-placeholder descriptions |
| AC5 | Sync section says "every 12 minutes" and "n8n" | README text contains both |
| AC6 | Sync section says no full run in the schedule | README text explicitly states incremental-on-idle, no scheduled full run |
| AC7 | On-chain section names both Alchemy and Chainstack | README names both providers with their respective roles |
| AC8 | Production URL noted as current/deployment | URL not presented as a permanent guarantee |
| AC9 | `docs/dev-tooling.md` exists | File present; covers all 4 artifacts |
| AC10 | `docs/deploy-dokploy.md` exists | File present; covers panel env vars and compose parser limitation |
| AC11 | `docs/traceability.md` has B402 payment trace row | CO1.BDOS.2063185 / CO1.REQ.2121688 row present |
| AC12 | DESIGN.md has T2 wallet flags note | D8 or footnote notes that T2 shipped in `55318da` |
| AC13 | No new hardcoded drift-prone numbers | README uses "current"/"at time of writing" for variable quantities |
| AC14 | `uv run ruff check .` and `uv run mypy app` pass | No broken imports or type errors introduced by docs changes |

---

## Risks

| Risk | Level | Mitigation |
|---|---|---|
| Stale README misleads a new developer | Medium | Audit each code snippet and env var example; verify against current code paths |
| Production URL hardcoded and then breaks | Low | Qualify URL as "current deployment"; add "subject to change" note |
| Docs drift again after this change | Low | Future changes that touch deployed code MUST update the relevant README section in the same commit |
