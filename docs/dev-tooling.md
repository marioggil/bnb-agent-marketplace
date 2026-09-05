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
