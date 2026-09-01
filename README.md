# bnb-agent-marketplace

A server-rendered BSC agent marketplace built with **FastAPI + PostgreSQL + HTMX**.
It mirrors the public [8004scan](https://8004scan.io) index, exposes a minimal
wallet-nonce auth (EIP-191 `personal_sign`), ships an x402 payment rail
(hiring agents for $U), and runs an on-chain indexer for $U transfers.

The Node.js scripts in the repo root (`stats.mjs`, `agents-bsc.mjs`,
`agent-detail.mjs`, `env.mjs`) stay as a **field-source-of-truth** reference for
the upstream 8004scan API. They are not part of the running app.

> **Status**: pre-1.0 alpha. Single repo, single deployment: the app runs on a
> Dokploy-hosted instance; the sync schedule is owned by an n8n workflow (see
> [Sync worker & scheduler](#sync-worker--scheduler)). There is no CI pipeline
> — quality gates are local commands (see [CI / code quality](#ci--code-quality)).

---

## Stack

| Layer | Choice |
| --- | --- |
| Web framework | FastAPI (Python 3.12 / 3.13) |
| Database | PostgreSQL 16 |
| ORM / migrations | SQLAlchemy 2 (async) + Alembic |
| Frontend | HTMX 2 + Jinja2 templates (no SPA) |
| Auth | EIP-191 wallet-nonce (single-use, 10 min TTL, CSRF) |
| Payments | x402 (B402) over $U (`eip3009`), facilitator EOA settles on BSC |
| On-chain indexer | Alchemy (backfill) + Chainstack (realtime) RPC |
| Packaging | pyproject.toml + multi-stage Dockerfile + Docker Compose |
| Quality | ruff (lint + format), mypy (strict on `app/`), pytest |

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
uv run alembic upgrade head   # applies the 5 real migrations (0001_initial..0005_onchain_index)
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

App is served on <http://localhost:8000>. `GET /healthz` returns 200 with
`{"status": "ok", "db": "ok"}` once both the app and Postgres are up.

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

The smoke suite lives in `tests/`: 19 test files from the original suite plus
`test_categories.py` (20 total), with `conftest.py` and `fixtures/`. Default
`uv run pytest` uses an aiosqlite engine and runs in ~7s. The
`postgres`-marked scenarios (GENERATED columns, ON CONFLICT, trigram, FK
cascades) skip unless `RUN_POSTGRES_TESTS=1` is set with a live DSN.

---

## Quickstart — Docker

The fastest way to stand the whole stack up:

```bash
cp .env.example .env
# edit .env — set SECRET_KEY to something real
docker compose up --build
```

`compose.yml` brings up:

- `db` — Postgres 16 Alpine on the compose network (not published to the
  host), volume **`bnb-agent-pgdata-v5`** (v1–v5 rotation history in the
  compose file; bump the name whenever initdb must re-run).
- `app` — the FastAPI image. The container command runs `entrypoint.sh`:
  **preflight** (fails fast if `DATABASE_URL`/`SECRET_KEY` are missing or
  `SECRET_KEY` starts with `change-me`), then `alembic upgrade head`, then
  `uvicorn app.main:app --host 0.0.0.0 --port 8000`. Only port **8000** is
  published to the host.

Both services have healthchecks; `app` only starts once `db` reports healthy.

---

## Project structure

```
.
├── app/
│   ├── main.py                  # app factory + lifespan (starts the on-chain
│   │                            #   indexer only when ALCHEMY_API_KEY is set)
│   ├── config.py                # pydantic-settings (env vars, incl. RPC keys)
│   ├── errors.py                # error classes + handlers
│   ├── seed_agents.py           # one-shot seed: discover + enrich a batch
│   ├── _ops/
│   │   └── cleanup_orphans.py   # maintenance: delete orphaned rows
│   ├── db/
│   │   ├── session.py           # async engine + session factory
│   │   ├── base.py
│   │   └── models/              # 7 models: agent, auth_nonce, favorite,
│   │                            #   hired_agent, onchain_index, sync_state, user
│   ├── routers/                 # 10 routers: agents, auth, favorites, healthz,
│   │                            #   hires, onchain_hires, onchain_stats, pages,
│   │                            #   payments, sync
│   ├── schemas/                 # pydantic request/response models
│   ├── services/                # 12 modules: 8004scan client, sync worker,
│   │                            #   categories, auth, payment, agent_payments,
│   │                            #   onchain_indexer, rpc_client, client_bscscan,
│   │                            #   client_evoevo, client_mcp, client_termix
│   ├── templates/               # base.html + pages/* + partials/*
│   ├── static/                  # css/, js/ (htmx, ethers, payment.js), img/
│   └── worker/sync.py           # CLI: `python -m app.worker.sync`
├── migrations/versions/         # 0001_initial … 0005_onchain_index
├── tests/                       # 20 test files + conftest.py + fixtures/
├── scripts/                     # dev tooling (see "Dev tooling" below)
├── index-blocks.html            # dev UI for the block-index webhook
├── n8n-sync-workflow.json       # the real sync scheduler (every 12 min)
├── entrypoint.sh                # container preflight + migrations + uvicorn
├── stats.mjs                    # 8004scan /stats (field reference, not in image)
├── agents-bsc.mjs               # 8004scan /agents?chain_id=56 (field reference)
├── agent-detail.mjs             # 8004scan /agents/{chain}/{token} (field reference)
├── env.mjs                      # .env loader used by the .mjs scripts
├── pyproject.toml               # deps + [tool.ruff] + [tool.mypy] + pytest
├── Dockerfile                   # multi-stage (builder -> python:3.13-slim runtime)
├── docker-compose.yml           # db + app
├── .dockerignore                # excludes tests, caches, secrets, etc.
├── .env.example                 # all vars, every secret is `change-me`
├── alembic.ini                  # migrations config
└── README.md
```

---

## Field source of truth — the `.mjs` prototype

Before this app existed, a small Node.js ESM script set (zero deps, `fetch`
native) was used to map the 8004scan public API surface. The scripts still
live at the repo root and are useful for:

- Sanity-checking the upstream API when the app misbehaves.
- Exploring new fields before wiring them into `AgentCache`.
- Onboarding by showing the raw response shape in a one-liner.

Run them with:

```bash
node stats.mjs
node agents-bsc.mjs 30
node agent-detail.mjs 252698
```

The Python `app/services/client_8004scan.py` wraps the same three endpoints
(`/stats`, `/agents?chain_id=56`, `/agents/{chain}/{token}`) with tenacity
retries, a per-host `asyncio.Semaphore(4)` to stay under the Pro tier, and
pydantic models that accept unknown fields into a `raw: dict` catch-all so
schema drift in the upstream never crashes the worker.

---

## Sync worker & scheduler

The sync worker populates the local `agent_cache` table from 8004scan in two
phases:

1. **Discovery** — walk the paginated `/agents` listing (200 per page,
   client-side BSC filter).
2. **Enrichment** — per-token `get_agent` detail request + upsert (ON
   CONFLICT), then the category post-pass.

The schedule is owned by **n8n** (`n8n-sync-workflow.json`), not by a cron in
the container: the workflow runs **every 12 minutes**, first `GET
/api/sync/status` (with `X-API-Key`), and only if the sync is not already
running it `POST`s an **incremental** run. There is no full run in the
schedule; a full re-walk is available on demand via the CLI or the Sync API.

```bash
# incremental from the last checkpoint (default, batch 100)
uv run python -m app.worker.sync --incremental

# full re-walk (idempotent via ON CONFLICT)
uv run python -m app.worker.sync --full --batch 200
```

Worker constants (spec sync-worker R4): `DEFAULT_INCREMENTAL_BATCH=100`,
`DEFAULT_FULL_BATCH=200`, discovery `page_size=200` with per-token
enrichment, and `failed_token_ids` kept in a FIFO capped at 1000. A 404 or
chain mismatch is **skip-not-stop**; a 429 honors the upstream `Retry-After`
header. With `8004SCAN_API_KEY` empty the worker logs a `[WARN]` on startup
and falls back to free-tier limits.

### Category post-pass

`agent_cache.category` is a Postgres GENERATED column (x402/oasf →
`rebalancing`, else `other`). After each upsert, `_maybe_enrich_category`
runs the 10-category classifier (taxonomy accepted from
`docs/category-study.md`): termix source category → offchain tags → x402 →
skill/protocol hints → `other`. The UPDATE fires only when the result
differs from the GENERATED default, so the post-pass is a no-write for
sparse rows. The full 11-slug taxonomy is: rebalancing, grid_trading,
yield_optimisation, health_factor_monitoring, dev_automation,
creative_design, marketing_content, data_analytics, security_compliance,
admin_ops, other.

---

## Sync API

The `/api/sync` endpoints let you trigger sync runs over HTTP (curl, n8n, a
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

# checkpoint + running state
curl https://your-app.example/api/sync/status -H "X-API-Key: $SYNC_API_KEY"
# -> {"running": false, "last_token_id": 42, "last_sync_at": "...", "failed_count": 0}
```

The run happens in the background; `POST` returns as soon as it is
dispatched. A second `POST` while a run is in flight gets `409`.

---

## On-chain indexer

The app indexes $U transfer events on BSC into the `onchain_transfers` table
via two workers (`app/services/onchain_indexer.py`):

| Worker | RPC provider | Purpose |
|---|---|---|
| Backfill | **Alchemy** (`ALCHEMY_API_KEY`) | Walk historical blocks in chunks, catch up from genesis |
| Realtime | **Chainstack** (`CHAINSTACK_API_KEY`) | Follow new blocks as they land |

**Activation**: the app lifespan starts `run_indexer_loop` only when
`ALCHEMY_API_KEY` is set (`app/main.py`). With both keys empty the indexer is
disabled and the app runs without on-chain data. The `rpc_client` module
fails over between providers per request.

Read paths: `GET /api/onchain/health` (per-provider status), `GET
/api/onchain/index/{block}` (single-block index), `GET
/api/onchain/stats`, and per-agent transfer history on
`GET /api/agents/{chain_id}/{token_id}/payments`.

---

## Categories & hero

The home page hero shows **10 category cards** (2×5 grid on desktop
`≥1024px`, horizontal scroll on smaller screens); clicking a card filters
the listing via `/?category=`. The filter select offers the same 11 slugs
(10 categories + `other`). The taxonomy and its signal mapping are defined
in `docs/category-study.md` (accepted) and implemented in
`app/services/categories.py`.

---

## Agent payment history

`GET /api/agents/{chain_id}/{token_id}/payments` returns the agent's incoming
$U transfers (public onchain data, no auth) — newest first, last ~50000 blocks
by default, capped at `limit` (default 50, max 200). Requires the agent to be
cached locally; an agent without a payment wallet answers `payments: []`.

```bash
curl "https://your-app.example/api/agents/97/42/payments?limit=10"
# -> {"agent_id":"97:0x8004...:42","chain_id":97,"token_id":42,"wallet":"0x...",
#     "token":"0xc70B...","payments":[{"tx_hash":"0x...","from":"0x...",
#     "to":"0x...","value_wei":"1000000","block_number":3141592}]}
```

---

## Wallet auth (curl preview)

The auth flow is EIP-191 `personal_sign` with a single-use 10-minute nonce,
a signed session cookie and CSRF on state-changing writes:

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

Authenticated endpoints (`POST /api/favorites`, `POST /api/hires`) require
the CSRF header derived from the session cookie.

---

## x402 payments (B402)

The marketplace acts as a **B402 merchant + facilitator**: hiring an agent
creates a payment challenge over **$U** (United Stables, `eip3009` rail),
the browser signs it with ethers v6 (vendored, MetaMask), and the
facilitator EOA settles on BSC. Flow:

1. **Signed-in user** opens an agent detail page → the Hire CTA shows the
   flat price (**$1.00**, `X402_DEFAULT_PRICE_USD`) — disabled when the
   agent has no payment wallet (`agent_wallet`).
2. Clicking the CTA POSTs `/api/hires` → **201 + B402 challenge**
   (`x402Version: 2`, `accepts[]` eip3009 $U, `payTo` = agent wallet,
   `maxTimeoutSeconds` 300).
3. `app/static/js/payment.js` (ethers v6) signs the EIP-712
   `TransferWithAuthorization` envelope: random 32-byte nonce,
   `validAfter = now - 120s`, `validBefore = now + maxTimeoutSeconds`.
4. POST `/api/hires/{id}/pay` carries the base64 envelope in `X-PAYMENT`
   (or `PAYMENT-SIGNATURE`). The server verifies chain → token → amount →
   payTo → validity → signature, then the facilitator broadcasts the
   settlement and the hire flips to `paid` + `tx_hash`.
5. The browser redirects to the agent's own `agent_url` (http(s) only) or
   back to the detail page. Failures leave the hire `failed` with the error
   shown on the page — never a dead-end.

### Chain switch: testnet 97 → mainnet 56

Testnet (97) is the default. For a demo-day mainnet run, flip one env var —
the $U address and default RPC follow the chain automatically:

```bash
X402_CHAIN_ID=56
X402_FACILITATOR_KEY=<mainnet facilitator key, never committed>
```

### Facilitator key handling — READ THIS

- The facilitator EOA **only pays gas** — funds are recipient-bound inside
  the signed envelope (the signature names the `payTo`), so a compromised
  facilitator key cannot redirect money.
- `X402_FACILITATOR_KEY` is **env-guarded and never committed**: `.env.example`
  ships only a `change-me` placeholder. An empty key disables payments
  (pay → 503 `payment_gateway_unconfigured`).
- Use a dedicated key per environment (never the testnet key on mainnet),
  funded with a small BNB balance (~0.01 BNB covers 50+ settlements).

---

## Alembic workflow

The schema is managed by Alembic with a sync `psycopg2` driver against the
same `DATABASE_URL`. Local day-to-day loop:

```bash
# 1. edit a model in app/db/models/*.py
# 2. autogenerate the migration
uv run alembic revision --autogenerate -m "add_x"
# 3. READ the diff in migrations/versions/ and edit if needed
#    (autogenerate misses enum value changes and server defaults on
#    existing columns)
# 4. apply
uv run alembic upgrade head
# 5. drift gate (local): raises if model <-> migration drift
uv run alembic check
```

For a clean re-run in dev: `uv run alembic downgrade base && uv run alembic upgrade head`.

---

## Dev tooling

- **`n8n-sync-workflow.json`** — the sync scheduler: runs every 12 min,
  `GET /api/sync/status` with `X-API-Key`, then `POST /api/sync` incremental
  when idle (no full run). Import it into n8n and set the `SYNC_API_KEY`
  variable.
- **`entrypoint.sh`** — container entrypoint: preflight requires
  `DATABASE_URL` + `SECRET_KEY` (rejects `change-me*`), then runs
  `alembic upgrade head`, then starts `uvicorn` on port 8000.
- **`scripts/`** — onchain dev tooling:
  - `random_indexer.py` — random-block $U transfer indexer hitting the
    production API, with SQLite dedupe (`data/indexer_used_blocks.db`) so a
    block is never indexed twice. Usage: `python3 scripts/random_indexer.py
    --from 72122100 --to 72500000 --count 100`.
  - `index-blocks.html` — standalone dev UI that posts a start block to the
    n8n `index-blocks` webhook (indexes 2000 blocks per run, ~33 min) and
    tells you where to continue.

---

## CI / code quality

There is **no CI pipeline** — the repo has no `.github/` directory and
nothing runs automatically on push. Quality is enforced by local commands,
all wired in `pyproject.toml`:

```bash
# lint (rules: E, F, W, I, TID; line length 100)
uv run ruff check .

# format check (no writes)
uv run ruff format --check .

# static type check (strict on app/)
uv run mypy app

# full smoke suite
uv run pytest
```

Ruff rules enabled: `E` (pycodestyle), `F` (pyflakes), `W` (pycodestyle
warnings), `I` (isort), `TID` (tidy imports — `TID252` enforces the module
boundary: `routers/` never imports `db/models/` directly). Line length is
100, target Python is 3.12. Mypy runs in `--strict` mode against `app/`;
tests relax `disallow_untyped_defs` so the smoke suite stays concise.

> `uv sync --extra dev` requires [uv](https://astral.sh/uv) on `PATH`. If you
> do not have it, install with `curl -LsSf https://astral.sh/uv/install.sh | sh`
> or fall back to the `python -m venv` + `pip install -e ".[dev]"` recipe
> shown above.

---

## Environment variables

Every var lives in `.env.example`; secrets use a `change-me` placeholder.
The full list (grouped by concern) is:

| Var | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+asyncpg://bnb:change-me@db:5432/bnb_agent` | asyncpg DSN used by the app |
| `8004SCAN_BASE` | `https://8004scan.io/api/v1/public` | upstream API base |
| `8004SCAN_API_KEY` | `change-me` (optional) | optional Pro-tier key; worker logs `[WARN]` if empty |
| `ALCHEMY_API_KEY` | empty | Alchemy RPC key — on-chain **backfill** worker; also **activates** the indexer loop at startup |
| `CHAINSTACK_API_KEY` | empty | Chainstack RPC key — on-chain **realtime** worker |
| `SECRET_KEY` | `change-me-32-bytes-min` | session cookie signing + CSRF derivation |
| `SESSION_TTL_MIN` | `60` | session lifetime in minutes |
| `LOG_LEVEL` | `INFO` | uvicorn + app loggers |
| `WORKER_RATE_PER_SEC` | `4` | soft cap on sync worker upstream rate |
| `SYNC_API_KEY` | empty | optional shared secret for the remote sync API (`POST/GET /api/sync`); empty disables those endpoints (503) |
| `X402_CHAIN_ID` | `97` | BSC chain for x402 payments (`56` = mainnet demo override) |
| `X402_FACILITATOR_KEY` | empty | facilitator EOA key (gas only); empty disables payments (503); never committed |
| `X402_RPC_URL` | per chain | optional BSC RPC override; per-chain public node otherwise |
| `X402_DEFAULT_PRICE_USD` | `1.00` | flat hire price, shown on the Hire CTA |
| `X402_U_TOKEN_ADDRESS_56` / `X402_U_TOKEN_ADDRESS_97` | pinned $U addresses | United Stables per chain |
| `X402_PERMIT2_ADDRESS` | Permit2 | reserved for future rails (unused in v1) |
| `POSTGRES_USER` | `bnb` | docker-compose `db` user |
| `POSTGRES_PASSWORD` | `change-me` | docker-compose `db` password |
| `POSTGRES_DB` | `bnb_agent` | docker-compose `db` database name |

---

## License

MIT (see `pyproject.toml`).