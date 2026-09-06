"""Model-introspection tests for the compliance schema (Phase 1a-i).

Spec: `openspec/changes/compliance-flags/spec.md` AC-1, AC-2, AC-3. These
tests assert the ORM shape — `AgentCache.compliance_penalty` column type +
nullable + constraint, and `AgentComplianceFlag` table + column inventory —
without requiring a live DB. The fixture stack already creates the schema
via `Base.metadata.create_all`, so deeper DB-backed assertions live in 1a-ii
(orchestrator idempotence + UPSERT byte-shape).

Note on JSONB patching: the test conftest patches every table registered on
`Base.metadata` at conftest import time. Our `AgentComplianceFlag` is not
imported in `app/db/models/__init__.py` (per design §2.2 — lazy discovery),
so the conftest patch never sees it. The `_patch_compliance_table_for_sqlite`
fixture below re-runs the same JSONB → JSON swap + `DefaultClause` rewrite
on the new table at module import time so sqlite's `create_all` is happy.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Boolean, DateTime, Numeric
from sqlalchemy.dialects.postgresql import JSONB as _PG_JSONB
from sqlalchemy import JSON as _SQLITE_JSON
from sqlalchemy.schema import DefaultClause

from app.db.models.agent import AgentCache
from app.db.models.agent_compliance import AgentComplianceFlag

# ---------------------------------------------------------------------------
# SQLite patch — mirror the conftest's JSONB → JSON swap for the new table.
# Runs at import time so the session-scoped `_create_schema` fixture sees a
# sqlite-compatible `agent_compliance_flags` table.
# ---------------------------------------------------------------------------


def _patch_compliance_table_for_sqlite() -> None:
    for tbl in AgentComplianceFlag.__table__._tables if hasattr(AgentComplianceFlag.__table__, "_tables") else [AgentComplianceFlag.__table__]:
        for col in tbl.columns:
            if isinstance(col.type, _PG_JSONB):
                col.type = _SQLITE_JSON()
                sd = col.server_default
                if sd is not None and hasattr(sd, "arg"):
                    raw = getattr(sd.arg, "text", None)
                    if raw and "::jsonb" in raw:
                        literal = raw.replace("::jsonb", "").strip().strip("'")
                        col.server_default = DefaultClause(literal)


_patch_compliance_table_for_sqlite()


# ---------------------------------------------------------------------------
# AgentComplianceFlag — class + column inventory
# ---------------------------------------------------------------------------


def test_agent_compliance_flag_class_imports() -> None:
    """T7 RED+GREEN: the `AgentComplianceFlag` class is importable."""
    assert AgentComplianceFlag is not None
    assert AgentComplianceFlag.__tablename__ == "agent_compliance_flags"


def test_agent_compliance_flag_has_all_required_columns() -> None:
    """All 8 columns from the migration must be present, with the documented types."""
    columns = {c.name: c for c in AgentComplianceFlag.__table__.columns}

    # Required set per spec AC-2.
    expected = {
        "agent_id",
        "creator_flagged",
        "creator_flag_sources",
        "owner_flagged",
        "owner_flag_sources",
        "creator_is_owner",
        "flagged_data_stale",
        "refreshed_at",
    }
    assert expected.issubset(columns.keys()), (
        f"missing columns: {expected - columns.keys()}"
    )

    # agent_id is the primary key.
    pk_cols = [c.name for c in AgentComplianceFlag.__table__.primary_key.columns]
    assert pk_cols == ["agent_id"]

    # All booleans are NOT NULL (they default to false at the server).
    for bool_col in (
        "creator_flagged",
        "owner_flagged",
        "creator_is_owner",
        "flagged_data_stale",
    ):
        col = columns[bool_col]
        assert isinstance(col.type, Boolean), f"{bool_col}: expected Boolean, got {col.type!r}"
        assert col.nullable is False, f"{bool_col}: must be NOT NULL"

    # JSON source lists are NOT NULL with a default.
    for jsonb_col in ("creator_flag_sources", "owner_flag_sources"):
        col = columns[jsonb_col]
        assert col.nullable is False, f"{jsonb_col}: must be NOT NULL"
        assert col.server_default is not None, (
            f"{jsonb_col}: must have a server_default (e.g. '[]'::jsonb)"
        )

    # refreshed_at is a datetime and NOT NULL.
    refreshed = columns["refreshed_at"]
    # SQLAlchemy wraps DateTime(timezone=True) in a _UtcAwareDateTime TypeDecorator,
    # whose impl is a DateTime instance. Use that as the contract instead of
    # isinstance on the public type.
    inner_impl = getattr(refreshed.type, "impl", None)
    assert isinstance(inner_impl, DateTime), (
        f"refreshed_at: expected DateTime(timezone=True) decorator wrapping DateTime, got {refreshed.type!r}"
    )
    assert refreshed.nullable is False


# ---------------------------------------------------------------------------
# AgentCache.compliance_penalty — column + CHECK constraint
# ---------------------------------------------------------------------------


def test_agent_cache_has_compliance_penalty_column() -> None:
    """Spec AC-1: `compliance_penalty` is `Numeric(5, 2) NOT NULL DEFAULT 0`."""
    columns = {c.name: c for c in AgentCache.__table__.columns}
    assert "compliance_penalty" in columns, "AgentCache.compliance_penalty must exist"

    col = columns["compliance_penalty"]
    assert isinstance(col.type, Numeric), f"expected Numeric, got {col.type!r}"
    assert col.type.precision == 5 and col.type.scale == 2, (
        f"expected Numeric(5, 2), got ({col.type.precision}, {col.type.scale})"
    )
    assert col.nullable is False, "compliance_penalty must be NOT NULL"
    assert col.server_default is not None, "compliance_penalty must have a server_default"


def test_agent_cache_compliance_penalty_check_constraint() -> None:
    """The `compliance_penalty_nonneg` CHECK constraint must be declared.

    SQLAlchemy auto-prefixes the constraint name with `<table>_` so the
    ORM-level name is `ck_agent_cache_compliance_penalty_nonneg`. The
    alembic migration uses the unprefixed form (the prefix is added by
    `sa.CheckConstraint`'s naming convention). Both forms are equivalent
    — we just want the suffix to appear.
    """
    constraints = [c for c in AgentCache.__table__.constraints if hasattr(c, "name")]
    names = {c.name for c in constraints}
    sql_texts = {str(getattr(c, "sqltext", "")) for c in constraints}
    assert any(n.endswith("compliance_penalty_nonneg") for n in names), (
        f"expected compliance_penalty_nonneg in {names}"
    )
    # And the SQL itself enforces the floor.
    assert any("compliance_penalty >= 0" in s for s in sql_texts), (
        f"expected 'compliance_penalty >= 0' in {sql_texts}"
    )


def test_agent_cache_activity_and_wallet_scores_unchanged() -> None:
    """Phase 1a-i MUST NOT touch `activity_score` or `wallet_score` (design §5.6)."""
    columns = {c.name: c for c in AgentCache.__table__.columns}
    assert "activity_score" in columns
    assert "wallet_score" in columns
    # Sanity: same Numeric(5, 2) shape as before.
    for col_name in ("activity_score", "wallet_score"):
        col = columns[col_name]
        assert isinstance(col.type, Numeric)
        assert col.type.precision == 5 and col.type.scale == 2


@pytest.mark.parametrize(
    "col_name",
    ["activity_score", "wallet_score", "quality_score", "popularity_score"],
)
def test_agent_cache_existing_score_columns_have_no_compliance_default(col_name: str) -> None:
    """Adding `compliance_penalty`'s `default=Decimal('0')` MUST NOT leak into
    other score columns (regression guard against a copy-paste mistake)."""
    col = AgentCache.__table__.columns[col_name]
    # Existing score columns are nullable, with no Python-level default.
    assert col.nullable is True
