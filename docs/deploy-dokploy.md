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
