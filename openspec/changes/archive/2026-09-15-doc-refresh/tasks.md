## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~280 |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | ask-on-risk |
| Chain strategy | single-pr |

---

```text
Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: single-pr
400-line budget risk: Low
```

---

## Pre-flight: audit current codebase

Before writing any changes, run these commands and save the output — it will be the source of truth for all living-language descriptions in README:

```bash
# Routers
ls app/routers/*.py | grep -v __init__ | xargs -I{} basename {} .py
# Expected: agents, auth, favorites, healthz, hires, onchain_hires, onchain_stats, pages, payments, sync  (10 files, no __init__)

# Services
ls app/services/*.py | grep -v __init__ | xargs -I{} basename {} .py
# Expected: agent_payments, agent_score, auth, categories, client_8004scan, client_bscscan,
#           client_evoevo, client_mcp, client_termix, feedback_sync, flagged_sync,
#           onchain_indexer, payment, probe_worker, reclassify, rpc_client, sync_worker
#           (17 files, no __init__)

# Models
ls app/db/models/*.py | grep -v __init__ | xargs -I{} basename {} .py
# Expected: agent, agent_feedback, agent_probe, auth_nonce, favorite, flagged_address,
#           hired_agent, onchain_index, sync_state, user
#           (10 files, no __init__)

# Migrations
ls migrations/versions/ | sort | tail -1
# Expected: 0011_fix_onchain_null_array.py
```

Save the output. Use the exact names in all README edits below.

---

## README.md — Project structure section

### Task 1 — Update `routers/` entry

File: `README.md`

Find the `app/routers/` comment block in the Project structure tree. Replace it with living language using the audited names (10 routers):

```
Replace:
│   ├── routers/                 # 10 routers: agents, auth, favorites, healthz,
│   │                            #   hires, onchain_hires, onchain_stats, pages,
│   │                            #   payments, sync

With:
│   ├── routers/                 # all routers in app/routers/: agents, auth,
│   │                            #   favorites, healthz, hires, onchain_hires,
│   │                            #   onchain_stats, pages, payments, sync
```

- [x] Implement and verify the behavior. <!-- sdd-owner: implementation -->

### Task 2 — Update `services/` entry

File: `README.md`

Find the `app/services/` comment block. Replace hardcoded "12 modules" with living language using audited names (17 services):

```
Replace:
│   ├── services/                # 12 modules: 8004scan client, sync worker,
│   │                            #   categories, auth, payment, agent_payments,
│   │                            #   onchain_indexer, rpc_client, client_bscscan,
│   │                            #   client_evoevo, client_mcp, client_termix

With:
│   ├── services/                # all services in app/services/ (current audit):
│   │                            #   agent_payments, agent_score, auth, categories,
│   │                            #   client_8004scan, client_bscscan, client_evoevo,
│   │                            #   client_mcp, client_termix, feedback_sync,
│   │                            #   flagged_sync, onchain_indexer, payment,
│   │                            #   probe_worker, reclassify, rpc_client, sync_worker
```

- [x] Implement and verify the behavior. <!-- sdd-owner: implementation -->

### Task 3 — Update `models/` entry

File: `README.md`

Find the `app/db/models/` comment block. Replace hardcoded "7 models" with living language using audited names (10 models):

```
Replace:
│   │   └── models/              # 7 models: agent, auth_nonce, favorite,
│   │                            #   hired_agent, onchain_index, sync_state, user

With:
│   │   └── models/              # all models in app/db/models/ (current audit):
│   │                            #   agent, agent_feedback, agent_probe, auth_nonce,
│   │                            #   favorite, flagged_address, hired_agent,
│   │                            #   onchain_index, sync_state, user
```

- [x] Implement and verify the behavior. <!-- sdd-owner: implementation -->

### Task 4 — Update migrations range

File: `README.md`

Find the `migrations/versions/` comment in the Project structure. Replace the old range with the current range:

```
Replace:
├── migrations/versions/         # 0001_initial … 0005_onchain_index

With:
├── migrations/versions/         # 0001_initial … 0011_fix_onchain_null_array
```

- [x] Implement and verify the behavior. <!-- sdd-owner: implementation -->

### Task 5 — Verify `scripts/` and `index-blocks.html` in tree

File: `README.md`

The design specifies that `scripts/` and `index-blocks.html` should appear in the project tree. Check the current README:

- [x] Verify `scripts/` is listed in the tree under Project structure. If missing, add it with comment: `# dev tooling (see "Dev tooling" section below)`
- [x] Verify `index-blocks.html` is listed in the tree. If missing, add it with comment: `# dev UI for block-index webhook`

- [x] Implement and verify the behavior. <!-- sdd-owner: implementation -->

---

## README.md — Sync worker & scheduler section

### Task 6 — Update sync scheduler description

File: `README.md`

Find the "The schedule is owned by **n8n**" paragraph in the "Sync worker & scheduler" section. The current text reads:

```
The schedule is owned by **n8n** (`n8n-sync-workflow.json`), not by a cron in
the container: the workflow runs **every 12 minutes**, first `GET
/api/sync/status` (with `X-API-Key`), and only if the sync is not already
running it `POST`s an **incremental** run. There is no full run in the
schedule; a full re-walk is available on demand via the CLI or the Sync API.
```

The paragraph already mentions n8n, 12 minutes, and no full run in schedule. Verify it is present and correct, then add a sentence clarifying the on-demand full run:

```
Add after the last sentence:
Full re-walk is on-demand only — use `uv run python -m app.worker.sync --full`
or `POST /api/sync` with `{"mode":"full"}` (requires `X-API-Key`).
```

- [x] Implement and verify the behavior. <!-- sdd-owner: implementation -->

### Task 7 — Add Dev tooling section reduction

File: `README.md`

Find the "## Dev tooling" section. Replace its entire content with a pointer to the new doc:

```
Replace the entire ## Dev tooling section body with:
See [docs/dev-tooling.md](docs/dev-tooling.md) for:
- `scripts/random_indexer.py` — random-block $U transfer indexer with SQLite dedupe
- `entrypoint.sh` — container preflight, migrations, and uvicorn bootstrap
- `n8n-sync-workflow.json` — production sync scheduler (import into n8n)
- `index-blocks.html` — dev UI for the block-index webhook
```

- [x] Implement and verify the behavior. <!-- sdd-owner: implementation -->

---

## README.md — Environment variables section

### Task 8 — Verify ALCHEMY_API_KEY and CHAINSTACK_API_KEY descriptions

File: `README.md`

In the Environment variables table, locate the rows for `ALCHEMY_API_KEY` and `CHAINSTACK_API_KEY`. Verify the descriptions match these exact values (or improve them to match):

| Var | Current description | Required |
|-----|---------------------|----------|
| `ALCHEMY_API_KEY` | "Alchemy RPC key — on-chain backfill worker; also activates the indexer loop at startup" | Must mention backfill worker AND indexer activation |
| `CHAINSTACK_API_KEY` | "Chainstack RPC key — on-chain realtime worker" | Must mention realtime worker |

- [x] Implement and verify the behavior. <!-- sdd-owner: implementation -->

---

## README.md — Production URL

### Task 9 — Verify URL has current/deployment qualifier

File: `README.md`

Grep for `agentmarket.marioggil.xyz`. Verify the URL appears with a qualifier such as "current deployment", "at time of writing", or "subject to change". If it appears as a bare guarantee, add a parenthetical qualifier.

- [x] Implement and verify the behavior. <!-- sdd-owner: implementation -->

---

## README.md — New sections

### Task 10 — Add Doc drift prevention block

File: `README.md`

Add the following block immediately above the `## License` section:

```markdown
## Doc drift prevention

This README describes deployed behaviour. Two rules keep it from going stale:

1. **Same-commit doc rule** — Any PR that changes code behaviour that is
   documented in this README MUST update the relevant README section in the
   same commit. Docs and code must not drift across commits or branches.

2. **Snippet audit rule** — Every shell/code block in this README MUST be
   verified against the current codebase before the PR that touches it lands.
   Copy-paste errors in env var names, endpoint paths, or CLI flags are the
   most common source of misleading documentation.

When in doubt, prefer "current" or "at time of writing" over hardcoded
counts or specific wallet addresses.
```

- [x] Implement and verify the behavior. <!-- sdd-owner: implementation -->

---

## New doc: docs/dev-tooling.md

### Task 11 — Create `docs/dev-tooling.md`

File: `docs/dev-tooling.md` (create new)

Create the file with the following content. Adapt the placeholders (e.g., VPS IP) to match the current deployment facts documented elsewhere in this project:

```markdown
# Dev tooling

Operational scripts and artifacts with no other home in the docs.

---

## `scripts/random_indexer.py`

Purpose: random-block $U transfer indexer that hits the production API rather
than the local indexer. Useful for spreading index coverage across
non-contiguous blocks during development.

Dedupe: blocks already indexed are recorded in `data/indexer_used_blocks.db`
(SQLite). A block is never indexed twice.

Usage:
:   python3 scripts/random_indexer.py --from 72122100 --to 72500000 --count 100

Flags:
- `--from`, `--to` — block range
- `--count` — number of random blocks to pick

---

## `entrypoint.sh`

Purpose: container preflight + migration + uvicorn bootstrap. Runs inside the
Docker container on every start.

Preflight checks:
- Rejects if `DATABASE_URL` is absent.
- Rejects if `SECRET_KEY` is absent or starts with `"change-me"`.
- Logs which secrets are missing on failure.

Startup sequence:
1. `alembic upgrade head`
2. `uvicorn app.main:app --host 0.0.0.0 --port 8000`

Dokploy compose parser note:
The Dokploy compose parser does **not** support `${}` variable interpolation in
the `environment:` block. All env vars (including `DATABASE_URL` and `SECRET_KEY`)
must be set directly in the Dokploy panel, not referenced as `${VAR}` in the
compose file. `entrypoint.sh` enforces this at container start.

---

## `n8n-sync-workflow.json`

Purpose: the production sync scheduler. Exported from n8n and checked into
the repo so the schedule is reproducible without exporting from a live n8n
instance.

Schedule: every 12 minutes (Timer trigger).

Logic:
1. `GET /api/sync/status` (header: `X-API-Key = $SYNC_API_KEY`)
2. If `running == false` → `POST /api/sync` with body `{"mode":"incremental"}`
3. No full run in the schedule.

Import into n8n:
1. Open n8n → Workflows → Import from JSON.
2. Paste `n8n-sync-workflow.json`.
3. Set the `SYNC_API_KEY` variable in n8n to match the app's `SYNC_API_KEY` env var.
4. Activate the workflow.

Manual trigger:
- `POST /api/sync` with `X-API-Key` header → on-demand incremental.
- `POST /api/sync` with `{"mode":"full"}` → full re-walk.

---

## `index-blocks.html`

Purpose: standalone browser-based dev UI for triggering block-index runs
against the n8n `index-blocks` webhook.

Usage:
1. Open `index-blocks.html` in a browser.
2. Enter a start block number.
3. Click "Index blocks" — posts to the n8n webhook.
4. The webhook indexes 2000 blocks per run (~33 min).
5. The page tells you where to continue for the next run.

Prerequisite: the n8n webhook node (index-blocks trigger) must be active in n8n.

Dev-only: not deployed to production. Keep at the repo root.
```

- [x] Implement and verify the behavior. <!-- sdd-owner: implementation -->

---

## New doc: docs/deploy-dokploy.md

### Task 12 — Create `docs/deploy-dokploy.md`

File: `docs/deploy-dokploy.md` (create new)

Create the file with the following content. Fill in the specific IP/domain with current values (current at time of writing; mark as subject to review before external publication):

```markdown
# Deploy — Dokploy

How the app is deployed to the Dokploy VPS.

> **Note:** Specific IP/domain values are current at time of writing. Review
> before publishing externally.

---

## VPS

| Field | Value |
|-------|-------|
| Provider | Dokploy (dokploy.com) |
| Host IP | `194.163.177.206` (current; verify before publishing) |
| Domain | `marioggil.xyz` (current; verify before publishing) |
| Purpose | Single VPS running the app container + Postgres via Dokploy panel |

---

## SSH access

**Deploy key:** Dokploy manages the deploy key. After adding the repo to
Dokploy, the deploy key is registered in the Dokploy panel under the project's
"Repository" tab.

**SSH access:** `ssh root@194.163.177.206`. The Dokploy web interface manages
the service lifecycle; direct SSH is only needed for emergency inspection or
manual DB access.

**Container access:**
:   `docker exec -it <container_name> /bin/sh`
    (container name from `docker ps` in the Dokploy panel or via SSH)

---

## Environment variables (Dokploy panel)

All env vars must be set directly in the Dokploy panel → Project → Environment.
They are **not** interpolated via `${}` in the docker-compose definition.

Required vars (must be set in panel, not referenced as `${}` in compose):

| Var | Purpose |
|-----|---------|
| `DATABASE_URL` | `postgresql+asyncpg://user:pass@host:port/db` — required; validated by entrypoint.sh |
| `SECRET_KEY` | >= 32 bytes, random; must not start with "change-me" |
| `8004SCAN_API_KEY` | Optional; free tier works without it |
| `ALCHEMY_API_KEY` | Required for on-chain backfill + indexer activation |
| `CHAINSTACK_API_KEY` | Optional; enables realtime worker |
| `SYNC_API_KEY` | Shared secret for n8n sync API calls |
| `X402_FACILITATOR_KEY` | Gas-only EOA key; empty = payments disabled |
| `X402_CHAIN_ID` | `97` (testnet default) or `56` (mainnet) |
| `LOG_LEVEL` | `INFO` (default) |

Postgres vars (used by the compose `db` service only):
`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`.

---

## Compose parser limitation

**The Dokploy compose parser does not support `${}` variable interpolation in the
`environment:` block of docker-compose.yml.**

**WRONG** (not interpolated by Dokploy):
:   ```yaml
    environment:
      - DATABASE_URL=${DATABASE_URL}
    ```

**CORRECT** (set in panel, referenced as-is):
:   ```yaml
    environment:
      - DATABASE_URL=<full DSN pasted in panel>
    ```

The app container's `entrypoint.sh` validates that `DATABASE_URL` and
`SECRET_KEY` are present and non-placeholder at startup. If they are missing,
the container exits with code 1 before uvicorn starts.

---

## Entrypoint requirement

`entrypoint.sh` is the container CMD. It runs on every container start
(including restarts):

1. Preflight: exit 1 if `DATABASE_URL` or `SECRET_KEY` is absent, or
   `SECRET_KEY` starts with `"change-me"`.
2. `alembic upgrade head` — runs pending migrations.
3. `uvicorn app.main:app --host 0.0.0.0 --port 8000`

Implication: a deploy with missing `DATABASE_URL` or `SECRET_KEY` in the panel
will crash immediately with a preflight failure, not a cryptic uvicorn error.

---

## Deployment workflow

1. Push to the GitHub repo.
2. Dokploy detects the push and triggers a new build.
3. Dokploy pulls the image, sets env vars from the panel, and starts the container.
4. `entrypoint.sh` runs preflight → migrations → uvicorn.
5. Healthcheck (Dokploy's built-in) verifies port 8000 responds.
6. If healthcheck fails, Dokploy rolls back to the previous container.
```

- [x] Implement and verify the behavior. <!-- sdd-owner: implementation -->

---

## DESIGN.md

### Task 13 — Add T2 implementation note to D8 row

File: `DESIGN.md`

Find the D8 row in the Decisions table:

```
| D8 | **Trust features** | ✅ T1 now: creator link + hires count (existing data).
  T2 wallet flags / on-chain proof, T3 recommendations — roadmap |
```

Add a parenthetical note confirming T2 has shipped. The note should appear inline within the D8 cell:

```
Replace the D8 row with:
| D8 | **Trust features** | ✅ T1 now: creator link + hires count (existing data).
  T2 wallet flags / on-chain proof, T3 recommendations — roadmap
  *(Note: T2 wallet flags shipped in commit 55318da; /flagged page exists;
  production sync pending as of this writing.)* |
```

- [x] Implement and verify the behavior. <!-- sdd-owner: implementation -->

---

## docs/traceability.md

### Task 14 — Verify B402 payment trace row

File: `docs/traceability.md`

Grep for `CO1.BDOS.2063185` in the file. Confirm the row is present:

```
| CO1.BDOS.2063185 | CO1.REQ.2121688 | GET /api/agents/{chain_id}/{token_id}/payments — agent payment history |
```

If the row is missing, add it to the Registered pairs table.

- [x] Implement and verify the behavior. <!-- sdd-owner: implementation -->

---

## Linting

### Task 15 — Run ruff and mypy

File: entire repo

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy app
```

Verify all three commands pass with no errors. (This is a docs-only change — no broken imports are expected, but the check confirms the no-code-change invariant.)

- [x] Implement and verify the behavior. <!-- sdd-owner: implementation -->

---

## Verification checklist

After all tasks are complete, run through these manually:

- [x] `ls app/routers/*.py | grep -v __init__` — names match README router listing
- [x] `ls app/services/*.py | grep -v __init__` — names match README service listing
- [x] `ls app/db/models/*.py | grep -v __init__` — names match README model listing
- [x] `grep ALCHEMY_API_KEY README.md` — description mentions backfill AND activation
- [x] `grep CHAINSTACK_API_KEY README.md` — description mentions realtime worker
- [x] `grep "every 12 minutes" README.md` — present in sync section
- [x] `grep "n8n" README.md` — present in sync section
- [x] `grep "incremental" README.md` — present in sync section
- [x] `grep "full run" README.md` — present in sync section (should say no scheduled full run)
- [x] `grep "Alchemy" README.md` — present in on-chain section
- [x] `grep "Chainstack" README.md` — present in on-chain section
- [x] `grep "agentmarket.marioggil.xyz" README.md` — present with qualifier
- [x] `test -f docs/dev-tooling.md` — file exists
- [x] `grep "random_indexer" docs/dev-tooling.md` — covered
- [x] `grep "entrypoint" docs/dev-tooling.md` — covered
- [x] `grep "n8n-sync-workflow" docs/dev-tooling.md` — covered
- [x] `grep "index-blocks" docs/dev-tooling.md` — covered
- [x] `test -f docs/deploy-dokploy.md` — file exists
- [x] `grep "compose" docs/deploy-dokploy.md` — compose parser limitation documented
- [x] `grep "55318da" DESIGN.md` — T2 note present
- [x] `grep "CO1.BDOS.2063185" docs/traceability.md` — B402 row present
- [x] `uv run ruff check .` — passes
- [x] `uv run mypy app` — passes

## Key Learnings

1. Hardcoded counts in documentation go stale on the next merge; living language ("all X in app/routers/") is more durable than enumerating numbers.
2. The compose parser limitation in Dokploy is a deploy-time footgun that is invisible without explicit documentation — it must be called out in both entrypoint.sh and the deploy doc.
3. A drift-prevention rule embedded in the README itself is more likely to be followed than one that lives only in an SDD artifact.
