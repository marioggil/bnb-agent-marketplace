# bnb-agent-marketplace

A server-rendered BSC agent marketplace built with **FastAPI + PostgreSQL + HTMX**.
It mirrors the public [8004scan](https://8004scan.io) index, exposes a minimal
wallet-nonce auth (EIP-191 `personal_sign`), and ships with a Docker / Docker
Compose stack ready to drop into [Dokploy](https://dokploy.com).

The Node.js scripts in the repo root (`stats.mjs`, `agents-bsc.mjs`,
`agent-detail.mjs`, `env.mjs`) stay as a **field-source-of-truth** reference for
the upstream 8004scan API. They are not part of the running app.

> **Status**: pre-1.0 alpha. Built for the [Build the Era](https://buildtheera.io)
> hackathon (BSC track). The marketplace ships in three reviewable PRs:
>
> - **PR-A — bootstrap (this PR)**: project skeleton, Docker, env, tooling.
> - **PR-B — data layer + sync worker**: models, Alembic, 8004scan client, sync.
> - **PR-C — auth + API + pages + tests**: wallet auth, JSON API, HTMX pages,
>   smoke tests.

---

## Stack

| Layer | Choice |
| --- | --- |
| Web framework | FastAPI (Python 3.12 / 3.13) |
| Database | PostgreSQL 16 |
| ORM / migrations | SQLAlchemy 2 (async) + Alembic |
| Frontend | HTMX 2 + Jinja2 templates (no SPA) |
| Auth | EIP-191 wallet-nonce stub (single-use, 10 min TTL, CSRF) |
| Packaging | pyproject.toml + multi-stage Dockerfile + Docker Compose |
| Quality | ruff (lint + format), mypy (strict on `app/`), pytest (smoke) |

---

## Quickstart — local (uv)

The primary local install uses [`uv`](https://astral.sh/uv), a single-binary
Python package manager. Install it once with:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then from the repo root:

```bash
cp .env.example .env
# edit .env — at minimum set SECRET_KEY to a real 32+ byte value
uv sync --extra dev
uv run alembic upgrade head         # no-op until PR-B lands the initial migration
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

App is served on <http://localhost:8000>. `GET /healthz` returns 200 with
`{"status": "ok", "db": "ok"}` once both the app and Postgres are up.

> The `app/main.py` module is created in **PR-C**. Until that lands,
> `uv run uvicorn` will fail with `ModuleNotFoundError: app.main`. The Docker
> build also references it; this is expected at the PR-A stage.

### Fallback — `python -m venv` + pip

If you do not have `uv` and prefer a stock Python workflow:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

---

## Running tests

The smoke suite (10 spec files + conftest) lands in `marketplace-scaffold-tests`
(FU-1). Default `uv run pytest` uses an in-memory aiosqlite engine and runs
in <5s. The `postgres`-marked scenarios skip unless `RUN_POSTGRES_TESTS=1`
is set with a live DSN (CI will wire a testcontainer in FU-7).

---

## Quickstart — Docker

The fastest way to stand the whole stack up:

```bash
cp .env.example .env
# edit .env — set SECRET_KEY to something real
docker compose up --build
```

`compose.yml` brings up:

- `db`  — Postgres 16 Alpine, volume `pgdata`, port 5432 on the host.
- `app` — the FastAPI image, running `alembic upgrade head` then
  `uvicorn app.main:app --host 0.0.0.0 --port 8000`. Exposed on
  <http://localhost:8000>.

Both services have healthchecks; `app` only starts once `db` reports healthy.

---

## Project structure

```
.
├── app/                       # FastAPI app (created in PR-B/C)
│   ├── main.py                # factory + lifespan
│   ├── config.py              # pydantic-settings
│   ├── db/                    # async engine, declarative base, models
│   ├── routers/               # pages, agents, auth, favorites, hires, healthz
│   ├── services/              # 8004scan client, sync worker, auth, categories
│   ├── schemas/               # pydantic request/response models
│   ├── templates/             # base.html + pages/* + partials/*
│   ├── static/                # css/, js/htmx-2.x.min.js, img/
│   └── worker/sync.py         # CLI: `python -m app.worker.sync`
├── migrations/                # Alembic env + versions/
├── tests/                     # smoke tests (respx + aiosqlite)
├── stats.mjs                  # 8004scan /stats (field reference, not in image)
├── agents-bsc.mjs             # 8004scan /agents?chain_id=56 (field reference)
├── agent-detail.mjs           # 8004scan /agents/{chain}/{token} (field reference)
├── env.mjs                    # .env loader used by the .mjs scripts
├── pyproject.toml             # deps + [tool.ruff] + [tool.mypy] + pytest
├── Dockerfile                 # multi-stage (builder -> python:3.13-slim runtime)
├── docker-compose.yml         # db + app
├── .dockerignore              # excludes tests, caches, secrets, etc.
├── .env.example               # all vars, every secret is `change-me`
├── alembic.ini                # migrations config
└── README.md
```

---

## Field source of truth — the `.mjs` prototype

Before this app existed, a small Node.js ESM script set (zero deps, `fetch`
native) was used to map the 8004scan public API surface. The scripts still
live at the repo root and are useful for:

- Sanity-checking the upstream API when the app misbehaves.
- Exploring new fields before wiring them into `AgentCache` (PR-B).
- Onboarding by showing the raw response shape in a one-liner.

Run them with:

```bash
node stats.mjs
node agents-bsc.mjs 30
node agent-detail.mjs 252698
```

The Python `app/services/client_8004scan.py` (PR-B) wraps the same three
endpoints (`/stats`, `/agents?chain_id=56`, `/agents/{chain}/{token}`) with
tenacity retries, a per-host `asyncio.Semaphore(4)` to stay under the Pro
tier, and pydantic models that accept unknown fields into a `raw: dict`
catch-all so schema drift in the upstream never crashes the worker.

---

## Sync worker usage

The sync worker (PR-B) populates the local `agent_cache` table from 8004scan.
It is a stateless CLI; Dokploy cron owns the schedule (every 30 min
incremental, weekly full on Sunday 03:00 UTC — spec sync-worker R4 / decision
Q4).

```bash
# incremental from the last checkpoint (default, batch 100)
uv run python -m app.worker.sync --incremental

# full re-walk (idempotent via ON CONFLICT)
uv run python -m app.worker.sync --full --batch 200
```

A 404 or chain mismatch is **skip-not-stop**; failed token IDs land in
`sync_state.failed_token_ids` (FIFO cap 1000, spec R4 / design D7). A 429
honors the upstream `Retry-After` header. With `8004SCAN_API_KEY` empty the
worker logs a `[WARN]` on startup and falls back to free-tier limits.

---

## Sync API

The `/api/sync` endpoints let you trigger sync runs over HTTP (curl, cron, a
future admin UI) instead of shelling into the container. Both endpoints
require the `X-API-Key` header to match the `SYNC_API_KEY` env var; with
`SYNC_API_KEY` unset they answer `503` (the API is disabled).

```bash
# start an incremental run (default; body optional)
curl -X POST https://your-app.example/api/sync \
     -H "X-API-Key: $SYNC_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"mode":"incremental"}'
# -> 202 {"status":"started","mode":"incremental"} | 409 if already running

# start a full run
curl -X POST https://your-app.example/api/sync \
     -H "X-API-Key: $SYNC_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"mode":"full"}'

# checkpoint + running state
curl https://your-app.example/api/sync/status -H "X-API-Key: $SYNC_API_KEY"
# -> {"running": false, "last_token_id": 42, "last_sync_at": "...", "failed_count": 0}
```

The run happens in the background; `POST` returns as soon as it is
dispatched. A second `POST` while a run is in flight gets `409`.

---

## Wallet auth (curl preview)

The full auth flow ships in **PR-C** (EIP-191 `personal_sign`, single-use
10-minute nonce, session cookie, CSRF token). The shape of the curl
exchange is:

```bash
# 1) ask the server for a nonce to sign
NONCE=$(curl -s "http://localhost:8000/auth/nonce?address=0xYourBSCWallet" | jq -r .nonce)
MESSAGE="Sign in to bnb_agent: ${NONCE}"

# 2) sign with any EIP-191 tool (e.g. MetaMask, ethers.js, web3.py)
#    `signature` ends up here, then:
curl -i -X POST http://localhost:8000/auth/verify \
     -H 'Content-Type: application/json' \
     -d "{\"address\":\"0xYourBSCWallet\",\"signature\":\"0x...\",\"nonce\":\"${NONCE}\"}"
# -> 200, Set-Cookie: session=...; HttpOnly; SameSite=Lax
```

The complete working example, including CSRF on `POST /api/favorites` and
`POST /api/hires`, lands in PR-C.

---

## Alembic workflow

The schema is managed by Alembic with a sync `psycopg2` driver against the
same `DATABASE_URL` (design D9). Local day-to-day loop:

```bash
# 1. edit a model in app/db/models/*.py
# 2. autogenerate the migration
uv run alembic revision --autogenerate -m "add_x"
# 3. READ the diff in migrations/versions/ and edit if needed
#    (autogenerate misses enum value changes and server defaults on
#    existing columns — design D6 / id 17 risk #6)
# 4. apply
uv run alembic upgrade head
# 5. CI gate: alembic check (raises if model <-> migration drift)
uv run alembic check
```

For a clean re-run in dev: `uv run alembic downgrade base && uv run alembic upgrade head`.

---

## CI / code quality

The quality bar is enforced by three commands, all wired in `pyproject.toml`:

```bash
# lint
uv run ruff check .

# format check (no writes)
uv run ruff format --check .

# static type check (strict on app/)
uv run mypy app

# smoke tests (added in PR-C)
uv run pytest
```

Ruff rules enabled: `E` (pycodestyle), `F` (pyflakes), `W` (pycodestyle
warnings), `I` (isort), `TID` (tidy imports — `TID252` enforces design
D5 module-boundary: `routers/` never imports `db/models/` directly).
Line length is 100, target Python is 3.12. Mypy runs in `--strict` mode
against `app/`; tests are allowed to relax `disallow_untyped_defs` so the
smoke suite can stay concise.

> `uv sync --extra dev` requires [uv](https://astral.sh/uv) on `PATH`. If you
> do not have it, install with `curl -LsSf https://astral.sh/uv/install.sh | sh`
> or fall back to the `python -m venv` + `pip install -e ".[dev]"` recipe
> shown above.

---

## Environment variables

Every var lives in `.env.example` with a `change-me` placeholder. The full
list (grouped by concern) is:

| Var | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+asyncpg://bnb:change-me@db:5432/bnb_agent` | asyncpg DSN used by the app |
| `8004SCAN_BASE` | `https://8004scan.io/api/v1/public` | upstream API base |
| `8004SCAN_API_KEY` | empty | optional Pro-tier key; worker logs `[WARN]` if empty |
| `SECRET_KEY` | `change-me-32-bytes-min` | session cookie signing + CSRF derivation |
| `SESSION_TTL_MIN` | `60` | session lifetime in minutes |
| `LOG_LEVEL` | `INFO` | uvicorn + app loggers |
| `WORKER_RATE_PER_SEC` | `4` | soft cap on sync worker upstream rate |
| `SYNC_API_KEY` | empty | optional shared secret for the remote sync API (`POST/GET /api/sync`); empty disables those endpoints (503) |
| `POSTGRES_USER` | `bnb` | docker-compose `db` user |
| `POSTGRES_PASSWORD` | `change-me` | docker-compose `db` password |
| `POSTGRES_DB` | `bnb_agent` | docker-compose `db` database name |

---

## License

MIT (see `pyproject.toml`).
