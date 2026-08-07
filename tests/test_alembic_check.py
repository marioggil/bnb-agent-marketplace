"""Alembic migration sanity check (default) + opt-in `alembic check`.

Spec: `sdd/marketplace-scaffold-tests/spec` bootstrap-tests R2, R3.
R3 (default) — file exists, parses, carries key DDL fragments.
R2 (postgres-only) — `alembic upgrade head` + `alembic check` (D6 CI gate).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "versions" / "0001_initial.py"


# R3 — file exists and parses; key DDL fragments present.
def test_migration_parses_and_callables():
    assert MIGRATION.exists()
    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    funcs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert "upgrade" in funcs and "downgrade" in funcs


def test_migration_ddl_fragments():
    src = MIGRATION.read_text(encoding="utf-8")
    # pg_trgm extension for the GIN trigram index on agent_cache.name.
    assert "pg_trgm" in src and "CREATE EXTENSION" in src
    # GENERATED `category` materialises x402 → rebalancing.
    assert "GENERATED ALWAYS AS" in src
    assert "jsonb_array_elements_text" in src
    assert "rebalancing" in src and "x402_supported" in src
    # hired_status enum + values.
    assert "hired_status" in src
    for v in ("pending", "paid", "failed", "cancelled"):
        assert f'"{v}"' in src or f"'{v}'" in src, f"missing enum value: {v}"


# R2 (postgres-only) — alembic upgrade head + alembic check.
@pytest.mark.postgres
def test_alembic_upgrade_then_check_passes():
    import os, subprocess
    dsn = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL not set")
    env = {**os.environ, "DATABASE_URL": dsn}
    up = subprocess.run(["alembic", "upgrade", "head"], capture_output=True, text=True, env=env)
    assert up.returncode == 0, up.stderr
    chk = subprocess.run(["alembic", "check"], capture_output=True, text=True, env=env)
    assert chk.returncode == 0, chk.stderr
