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
MIGRATION_0004 = (
    Path(__file__).resolve().parent.parent / "migrations" / "versions" / "0004_hired_payment_cols.py"
)


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
    # GENERATED `category` materialises x402 → rebalancing. The migration
    # uses SQLAlchemy `sa.Computed(...)` (emits GENERATED ALWAYS AS at
    # runtime), so assert on the Computed marker + the CASE expression.
    assert "Computed" in src
    assert "supported_protocols ? 'oasf'" in src
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


# ---------------------------------------------------------------------------
# FU-2 (B2) — 0004_hired_payment_cols up/down parity, statically verified.
# ---------------------------------------------------------------------------


def test_migration_0004_exists_and_parses():
    assert MIGRATION_0004.exists()
    tree = ast.parse(MIGRATION_0004.read_text(encoding="utf-8"))
    funcs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert "upgrade" in funcs and "downgrade" in funcs


def test_migration_0004_up_ddl_fragments():
    src = MIGRATION_0004.read_text(encoding="utf-8")
    for col in ("amount", "token", "rail", "pay_to", "challenge_expiry"):
        assert f'"{col}"' in src, f"upgrade missing column {col}"
    assert "Numeric(38, 18)" in src
    assert "create_index" in src and "ix_hired_agents_address_status" in src


def test_migration_0004_down_mirrors_up():
    import re

    src = MIGRATION_0004.read_text(encoding="utf-8")
    up, down = src.split("def downgrade")
    assert "drop_index" in down and "ix_hired_agents_address_status" in down
    # every add_column in upgrade has a matching drop_column in downgrade.
    # Column names are the 2nd quoted string inside sa.Column(...).
    added = set(re.findall(r'add_column\([^,]+,\s*sa\.Column\("([^"]+)"', up))
    dropped = set(re.findall(r'drop_column\([^,]+,\s*"([^"]+)"', down))
    assert added == dropped == {"amount", "token", "rail", "pay_to", "challenge_expiry"}
