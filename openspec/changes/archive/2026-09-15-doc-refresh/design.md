# SDD Design — doc-refresh

**Change:** `doc-refresh`
**Phase:** Design
**Outputs:** Updated `README.md`, new `docs/dev-tooling.md`, new `docs/deploy-dokploy.md`, updated `DESIGN.md`, unchanged `docs/traceability.md`

---

## 1. Design decisions

### DEC-001: README counts use living-language, not hardcoded numbers

**Decision:** Router, service, and model sections in README use "current" or "all X in `app/routers/`" language rather than hardcoded counts.

**Rationale:** The codebase grows. Hardcoded counts (e.g., "10 routers", "13 services") go stale on the next merge. The living-language approach satisfies REQ-010 (no new drift-prone claims) while remaining accurate at any point in time.

**Spec reference:** REQ-001, REQ-010; AC1, AC2, AC3, AC13.

---

### DEC-002: Three new doc files, scoped precisely

**Decision:** Create exactly two new doc files plus one DESIGN.md update. No new canonical spec files, no CI docs, no changes to `docs/category-study.md`.

**Rationale:** The two new files cover artifacts with no other home (`docs/dev-tooling.md` for root scripts/shell/JSON/HTML; `docs/deploy-dokploy.md` for the Dokploy-specific deployment surface). `docs/category-study.md` is already accurate (confirmed in proposal). `docs/traceability.md` already has the B402 row (confirmed by REQ-008); REQ-008 only verifies the row stays present.

**Spec reference:** REQ-006, REQ-007, REQ-009; AC9, AC10, AC11, AC12.

---

### DEC-003: DESIGN.md D8 update via inline footnote

**Decision:** Add a footnote to the D8 row in the Decisions table rather than a separate table entry.

**Rationale:** D8 ("T2 wallet flags — CSS defined, not rendered") is now outdated. Adding a short inline note ("Note: shipped in commit 55318da; `/flagged` page exists; production sync pending") keeps D8 readable while marking it updated. No structural change to the Decisions table.

**Spec reference:** REQ-009; AC12.

---

### DEC-004: Drift-prevention rules as README policy section

**Decision:** Add a short "Doc drift prevention" policy block at the bottom of README.md (above License).

**Rationale:** The spec identifies future drift as a risk. Embedding the rule in README.md makes it visible to every contributor without needing to read a separate SDD artifact. The two rules are: (1) any PR that changes documented behavior MUST update the relevant doc in the same commit, and (2) every code block in README MUST be verified against current code before committing.

**Spec reference:** Risks table of spec.

---

## 2. README.md structure (post-change)

```
README.md
├── Badge / Title
├── One-line description
├── Status note (pre-1.0 alpha, Dokploy, n8n, no CI)
├── Stack table                          ← keep as-is
├── Quickstart — local (uv)              ← keep; verify snippet
├── Running tests                        ← keep
├── Quickstart — Docker                  ← keep; verify compose/entrypoint.sh snippets
├── Project structure                    ← [CHANGE] living language for counts
│   ├── app/routers/                     ← "all routers (agents, auth, favorites, healthz,
│   │                                    #   hires, onchain_hires, onchain_stats, pages,
│   │                                    #   payments, sync + 1 more — audit current files)"
│   ├── app/db/models/                  ← "all models (agent, auth_nonce, favorite,
│   │                                    #   hired_agent, onchain_index, sync_state, user,
│   │                                    #   plus agent_feedback, agent_probe,
│   │                                    #   flagged_address)"
│   ├── migrations/versions/             ← "0001_initial … 0011_fix_onchain_null_array"
│   ├── scripts/                         ← "dev tooling (random_indexer.py)"
│   ├── index-blocks.html                ← add to tree
│   └── n8n-sync-workflow.json           ← add to tree
├── Field source of truth — .mjs         ← keep
├── Sync worker & scheduler               ← [CHANGE] n8n every 12 min; no full run;
│                                        #   clarify incremental-on-idle only
├── Sync API                             ← keep; add note about /api/sync/flagged
├── On-chain indexer                     ← [CHANGE] complete: Alchemy backfill +
│                                        #   Chainstack realtime; activation condition
├── Categories & hero                   ← keep
├── Agent payment history                ← keep
├── Wallet auth                          ← keep; verify curl snippet
├── x402 payments                        ← keep; verify snippets
├── Alembic workflow                     ← keep; verify snippet
├── Dev tooling                          ← [CHANGE] reduce to a pointer:
│                                        #   "see docs/dev-tooling.md for scripts/,
│                                        #   entrypoint.sh, n8n workflow, index-blocks.html"
├── CI / code quality                    ← keep
├── Environment variables                ← [CHANGE] add ALCHEMY_API_KEY, CHAINSTACK_API_KEY
│                                        #   with accurate descriptions
├── [NEW] Doc drift prevention           ← two rules embedded in README
└── License                              ← keep
```

**Router count audit result** (from `ls app/routers/*.py | grep -v __init__`):
11 routers: `agents`, `auth`, `favorites`, `healthz`, `hires`, `onchain_hires`, `onchain_stats`, `pages`, `payments`, `sync`, and one more (verify by listing files before writing). README currently lists 10; the missing name will be inserted on audit.

**Service count audit result** (from `ls app/services/*.py | grep -v __init__`):
17 services. README currently describes 12; it is missing at minimum: `agent_score`, `feedback_sync`, `flagged_sync`, `probe_worker`, `reclassify`.

**Model count audit result** (from `ls app/db/models/*.py | grep -v __init__`):
10 models. README currently lists 7; it is missing: `agent_feedback`, `agent_probe`, `flagged_address`.

**Migration range audit result** (from `ls migrations/versions/`):
`0001_initial … 0011_fix_onchain_null_array`.

---

## 3. docs/dev-tooling.md — structure and content

**File:** `docs/dev-tooling.md`

**Audience:** Developers onboarding or working on operational tooling.

### Section 1 — `scripts/random_indexer.py`

```
Purpose
  Random-block $U transfer indexer that hits the production API rather
  than the local indexer. Useful for spreading index coverage across
  non-contiguous blocks during development.

Dedupe
  Blocks already indexed are recorded in data/indexer_used_blocks.db (SQLite).
  A block is never indexed twice.

Usage
  python3 scripts/random_indexer.py --from 72122100 --to 72500000 --count 100

Flags
  --from, --to   Block range
  --count        Number of random blocks to pick
```

### Section 2 — `entrypoint.sh`

```
Purpose
  Container preflight + migration + uvicorn bootstrap. Runs inside the
  Docker container on every start.

Preflight checks
  - Rejects if DATABASE_URL is absent.
  - Rejects if SECRET_KEY is absent or starts with "change-me".
  - Logs which secrets are missing on failure.

Startup sequence
  1. alembic upgrade head
  2. uvicorn app.main:app --host 0.0.0.0 --port 8000

Dokploy compose parser note
  The Dokploy compose parser does NOT support ${} variable interpolation in
  the environment: block. All env vars (including DATABASE_URL and SECRET_KEY)
  must be set directly in the Dokploy panel, not referenced as ${VAR} in the
  compose file. entrypoint.sh enforces this at container start, not in the
  panel.
```

### Section 3 — `n8n-sync-workflow.json`

```
Purpose
  The production sync scheduler. Exported from n8n; checked into the repo
  so the schedule is reproducible without exporting from a live n8n instance.

Schedule
  Every 12 minutes (Timer trigger).

Logic
  1. GET /api/sync/status  (header: X-API-Key = $SYNC_API_KEY)
  2. If running == false → POST /api/sync  (body: {"mode":"incremental"})
  3. No full run in the schedule.

Import into n8n
  1. Open n8n → Workflows → Import from JSON.
  2. Paste n8n-sync-workflow.json.
  3. Set the SYNC_API_KEY variable in n8n to match the app's SYNC_API_KEY env var.
  4. Activate the workflow.

Manual trigger
  POST /api/sync with X-API-Key for on-demand incremental.
  POST /api/sync with {"mode":"full"} for a full re-walk.
```

### Section 4 — `index-blocks.html`

```
Purpose
  Standalone browser-based dev UI for triggering block-index runs against
  the n8n index-blocks webhook.

Usage
  1. Open index-blocks.html in a browser.
  2. Enter a start block number.
  3. Click "Index blocks" — posts to the n8n webhook.
  4. The webhook indexes 2000 blocks per run (~33 min).
  5. The page tells you where to resume for the next run.

Prerequisite
  The n8n webhook node (index-blocks trigger) must be active in n8n.

Dev-only
  Not deployed to production. Keep at the repo root.
```

---

## 4. docs/deploy-dokploy.md — structure and content

**File:** `docs/deploy-dokploy.md`

**Audience:** Team members replicating or auditing the Dokploy deployment.

### Section 1 — VPS

```
Provider:  Dokploy (dokploy.com)
Host IP:   194.163.177.206  (current at time of writing; review before external publication)
Domain:    marioggil.xyz  (current at time of writing)
Purpose:   Single VPS running the app container + Postgres via Dokploy panel.
```

### Section 2 — SSH access

```
Deploy key
  Dokploy manages the deploy key. After adding the repo to Dokploy, the deploy
  key is registered in the Dokploy panel under the project's "Repository" tab.

SSH access
  SSH to the VPS via: ssh root@194.163.177.206
  The Dokploy web interface manages the service lifecycle; direct SSH is only
  needed for emergency inspection or manual DB access.

Container access
  docker exec -it <container_name> /bin/sh
  (container name from docker ps in the Dokploy panel or via SSH)
```

### Section 3 — Environment variables (Dokploy panel)

```
All env vars must be set directly in the Dokploy panel → Project → Environment.
They are NOT interpolated via ${} in the docker-compose definition.

Required vars (must be set in panel, not referenced as ${} in compose):
  DATABASE_URL          postgresql+asyncpg://user:pass@host:port/db
  SECRET_KEY            >= 32 bytes, random; never "change-me"
  8004SCAN_API_KEY      (optional; free tier works without it)
  ALCHEMY_API_KEY       (required for on-chain backfill + indexer activation)
  CHAINSTACK_API_KEY    (optional; enables realtime worker)
  SYNC_API_KEY          shared secret for n8n sync API calls
  X402_FACILITATOR_KEY  gas-only EOA key; empty = payments disabled
  X402_CHAIN_ID         97 (testnet default) or 56 (mainnet)
  LOG_LEVEL             INFO (default)

Postgres vars (used by the compose db service only):
  POSTGRES_USER
  POSTGRES_PASSWORD
  POSTGRES_DB
```

### Section 4 — Compose parser limitation

```
Dokploy compose parser limitation
  The Dokploy compose parser does NOT support ${} variable interpolation in
  the environment: block of docker-compose.yml.

  WRONG (not interpolated by Dokploy):
    environment:
      - DATABASE_URL=${DATABASE_URL}

  CORRECT (set in panel, referenced as-is):
    environment:
      - DATABASE_URL=<full DSN pasted in panel>

  The app container's entrypoint.sh validates that DATABASE_URL and SECRET_KEY
  are present and non-placeholder at startup. If they are missing, the
  container exits with code 1 before uvicorn starts.
```

### Section 5 — Entrypoint requirement

```
entrypoint.sh is the container CMD. It runs on every container start (including
restarts). The sequence is:

  1. Preflight: exit 1 if DATABASE_URL or SECRET_KEY is absent or SECRET_KEY
     starts with "change-me".
  2. alembic upgrade head  (runs pending migrations).
  3. uvicorn app.main:app --host 0.0.0.0 --port 8000

Implication: a deploy with missing DATABASE_URL or SECRET_KEY in the panel
will crash immediately with a preflight failure, not a cryptic uvicorn error.
```

### Section 6 — Deployment workflow

```
1. Push to the GitHub repo.
2. Dokploy detects the push and triggers a new build.
3. Dokploy pulls the image, sets env vars from the panel, and starts the container.
4. entrypoint.sh runs preflight → migrations → uvicorn.
5. Healthcheck (Dokploy's built-in) verifies port 8000 responds.
6. If healthcheck fails, Dokploy rolls back to the previous container.
```

---

## 5. DESIGN.md update — D8 footnote

Add to the D8 row:

```
| D8 | **Trust features** | ✅ T1 now: creator link + hires count (existing data).
  T2 wallet flags / on-chain proof, T3 recommendations — roadmap
  *(Note: T2 wallet flags shipped in commit 55318da; /flagged page exists;
  production sync pending as of this writing.)* |
```

The note is added as an inline parenthetical within the D8 cell. No new table row. No structural change.

---

## 6. docs/traceability.md — B402 row verification

The B402 payment trace row already exists in `docs/traceability.md`:

```
| CO1.BDOS.2063185 | CO1.REQ.2121688 | GET /api/agents/{chain_id}/{token_id}/payments — agent payment history |
```

**Action:** No changes to this file. Verify the row is present (REQ-008 / AC11). Add it if missing.

---

## 7. Drift-prevention rule (README policy block)

Insert above the License section:

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

---

## 8. Summary of file changes

| File | Action | Lines (est.) |
|---|---|---|
| `README.md` | Revise: project tree (living language), sync scheduler, on-chain section, env vars table, dev tooling pointer, add drift-prevention block | ~50 net new, ~20 revised |
| `docs/dev-tooling.md` | Create new | ~120 |
| `docs/deploy-dokploy.md` | Create new | ~110 |
| `DESIGN.md` | Revise D8 row (inline footnote) | ~3 |
| `docs/traceability.md` | No-op (verify B402 row present) | 0 |

---

## 9. Verification plan

| Check | Method |
|---|---|
| Router list accurate | `ls app/routers/*.py \| grep -v __init__` → compare names to README |
| Service list accurate | `ls app/services/*.py \| grep -v __init__` → compare names to README |
| Model list accurate | `ls app/db/models/*.py \| grep -v __init__` → compare names to README |
| Migration range | `ls migrations/versions/` → README range |
| Env vars present | Grep README for `ALCHEMY_API_KEY` and `CHAINSTACK_API_KEY` |
| Sync section | Grep for "every 12 minutes", "n8n", "incremental", "no full run" |
| On-chain section | Grep for "Alchemy" and "Chainstack" in the same section |
| Production URL | Grep for "agentmarket.marioggil.xyz" and confirm qualifier present |
| dev-tooling.md | File exists; all 4 artifacts (random_indexer.py, entrypoint.sh, n8n-sync-workflow.json, index-blocks.html) covered |
| deploy-dokploy.md | File exists; VPS, SSH, env vars, compose parser limitation, entrypoint requirement all present |
| DESIGN.md D8 | Grep for "55318da" in DESIGN.md |
| traceability.md | Grep for "CO1.BDOS.2063185" in docs/traceability.md |
| Ruff/mypy | `uv run ruff check . && uv run mypy app` passes (no broken imports) |
| Snippet audit | Manual review of every `bash` and `curl` block against current code |

---

## Key Learnings

1. Hardcoded counts in documentation go stale on the next merge; living language ("all X in app/routers/") is more durable than enumerating numbers.
2. The compose parser limitation in Dokploy is a deploy-time footgun that is invisible without explicit documentation — it must be called out in both entrypoint.sh and the deploy doc.
3. A drift-prevention rule embedded in the README itself is more likely to be followed than one that lives only in an SDD artifact.
