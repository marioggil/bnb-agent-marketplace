# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# Multi-stage Dockerfile for bnb-agent-marketplace
#
# - builder:  installs deps into a user site-packages (PEP 660-style, no venv)
# - runtime:  python:3.13-slim, non-root, curl /healthz probe
# - the runtime image DOES NOT contain tests, dev extras, or the .mjs prototype
#   scripts are kept in build context (see .dockerignore) but the README
#   reference to them is via the docs site, not the image
# ---------------------------------------------------------------------------

ARG PYTHON_VERSION=3.13
ARG APP_USER=appuser
ARG APP_UID=1000
ARG APP_GID=1000

# ---------- builder ---------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PYTHONDONTWRITEBYTECODE=1

# Build wheels only — keep the runtime image lean.
RUN pip install --no-cache-dir --upgrade pip wheel

WORKDIR /build

# Copy ONLY the project metadata first so the dep install layer is cached
# independently of source-code edits.
COPY pyproject.toml ./
COPY README.md ./

# We don't ship a sdist/venv here; install deps + the package itself in a
# user site so the runtime stage can `COPY --from=builder` them.
# `--no-deps` for the project itself so we get a deterministic dep set.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --user --no-cache-dir \
        "fastapi>=0.115" \
        "uvicorn[standard]>=0.30" \
        "sqlalchemy[asyncio]>=2.0" \
        "asyncpg>=0.29" \
        "alembic>=1.13" \
        "jinja2>=3.1" \
        "httpx>=0.27" \
        "pydantic>=2.7" \
        "pydantic-settings>=2.4" \
        "python-dotenv>=1.0" \
        "itsdangerous>=2.2" \
        "eth-account>=0.13"

# Copy the application source last so changes don't bust the deps cache.
# (app/ does not exist yet at PR-A; PR-B/C will add the modules referenced
# by the CMD below. The image will start successfully once PR-C lands.)
COPY app ./app

# ---------- runtime ---------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

# Re-declare user/group ARGs in the runtime stage: ARGs declared in the
# top-level only apply to the first FROM that consumes them. Without these,
# `${APP_USER}` / `${APP_UID}` / `${APP_GID}` would expand to empty strings
# and `groupadd --gid ''` would fail.
ARG APP_USER=appuser
ARG APP_UID=1000
ARG APP_GID=1000

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/home/${APP_USER}/.local/bin:${PATH}" \
    PYTHONPATH="/home/${APP_USER}/.local/lib/python${PYTHON_VERSION}/site-packages"

# curl is needed for HEALTHCHECK; libpq5 / libpq-dev aren't (we use asyncpg).
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Non-root user (R3).
RUN groupadd --system --gid "${APP_GID}" "${APP_USER}" \
    && useradd --system --uid "${APP_UID}" --gid "${APP_GID}" \
        --home-dir "/home/${APP_USER}" --shell /sbin/nologin "${APP_USER}"

WORKDIR /home/${APP_USER}/app

# Copy installed deps from builder.
COPY --from=builder --chown=${APP_USER}:${APP_USER} /root/.local /home/${APP_USER}/.local

# Copy application source.
COPY --from=builder --chown=${APP_USER}:${APP_USER} /build/app ./app
COPY --chown=${APP_USER}:${APP_USER} pyproject.toml README.md ./

USER ${APP_USER}

EXPOSE 8000

# R3: probe /healthz; the app answers 200 when DB is reachable, 503 otherwise.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

# app.main:app is created in PR-C (tasks 5.5, 6.5). Until then the container
# will fail to start — this is expected and documented in the README.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
