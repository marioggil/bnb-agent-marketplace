#!/bin/sh
# ---------------------------------------------------------------------------
# Container entrypoint for the `app` service.
#
# Runs in the Dokploy image (Debian-based) under `sh`. Order:
#   1. Preflight — fail fast with a clear message if a required env var
#      is missing or weak. The compose parser on Dokploy cannot interpolate
#      `${VAR}` in the environment block, so per-deploy values are set in
#      the panel and injected here; we only validate them.
#   2. `alembic upgrade head` — apply DB migrations (idempotent).
#   3. `uvicorn app.main:app` — start the API.
#
# Why this file lives on disk instead of inline in docker-compose.yml:
#   the inline multi-line `command:` interacts badly with Dokploy's
#   compose parser (mis-escaped quotes, `$$` shell-expansion issues). A
#   real file is portable across Compose, Docker, Dokploy, and plain
#   `docker exec`.
# ---------------------------------------------------------------------------
set -eu

# --- 1. preflight --------------------------------------------------------
if [ -z "${DATABASE_URL:-}" ]; then
  echo "FATAL: DATABASE_URL is not set." >&2
  echo "       Add it in the Dokploy panel as:" >&2
  echo "       postgresql+asyncpg://bnb:<password>@db:5432/bnb_agent" >&2
  exit 1
fi

if [ -z "${SECRET_KEY:-}" ]; then
  echo "FATAL: SECRET_KEY is not set." >&2
  echo "       Add a 32+ byte random value in the Dokploy panel." >&2
  exit 1
fi

# Reject the documented weak placeholders up front so a misconfiguration
# cannot silently boot the app with a guessable secret. The app's Settings
# validator also catches these, but failing here is faster and clearer.
case "$SECRET_KEY" in
  change-me*|"")
    echo "FATAL: SECRET_KEY is empty or starts with 'change-me'. Set a real random value." >&2
    exit 1
    ;;
esac

# --- 2. migrations -------------------------------------------------------
echo "preflight OK; running alembic upgrade head..."
alembic upgrade head

# --- 3. server -----------------------------------------------------------
echo "starting uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
