"""Alembic environment.

Per design D9 the runtime uses `asyncpg` but migrations use `psycopg2`
(sync) — autogenerate and offline SQL are more reliable on a sync engine.
We rewrite the same `DATABASE_URL` from the env, only swapping the driver
suffix.

PR-C introduces `app.config.settings`; at that point this file will read
`settings.DATABASE_URL` instead of `os.environ`. The fallback to
`os.environ` here keeps this PR self-contained and `alembic upgrade head`
runnable in compose before PR-C lands.
"""
from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

# Ensure the project root is importable when this script is run from
# anywhere (CI, compose, host). `alembic` adds the parent dir of the
# `script_location` to sys.path already, but we make it explicit.
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Importing the models registers every table on Base.metadata, which is
# what alembic compares against. Order is irrelevant here because every
# model module imports Base directly.
from app.db.base import Base  # noqa: E402
from app.db import models  # noqa: F401, E402  (side-effect: registers tables)

# Alembic Config object.
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _database_url() -> str:
    """Resolve the sync `postgresql+psycopg2://...` URL from the env.

    The same env var used by the runtime (`DATABASE_URL`, default
    `postgresql+asyncpg://...`) is rewritten by replacing the driver
    prefix. The user can also export `DATABASE_URL_SYNC` to override
    outright — useful for tests that want a separate migration database.
    """
    sync_override = os.environ.get("DATABASE_URL_SYNC", "").strip()
    if sync_override:
        return sync_override

    raw = os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://bnb:bnb@localhost:5432/bnb_agent",
    ).strip()

    if raw.startswith("postgresql+asyncpg://"):
        return raw.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
    if raw.startswith("postgresql://"):
        return raw.replace("postgresql://", "postgresql+psycopg2://", 1)
    return raw


# PR-C will replace this with `settings.DATABASE_URL`; see comment at top.
config.set_main_option("sqlalchemy.url", _database_url())

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (emits SQL to stdout)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live psycopg2 connection."""
    url = config.get_main_option("sqlalchemy.url")
    connectable = create_engine(
        url,
        poolclass=pool.NullPool,
        future=True,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
