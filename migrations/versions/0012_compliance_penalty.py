"""Add `agent_cache.compliance_penalty` + `agent_compliance_flags` table.

Spec: `openspec/changes/compliance-flags/spec.md` AC-1 + AC-2. phase 1a-i adds
exactly two pieces of schema, both of which the agent-compliance pipeline
needs before 1a-ii can populate them and 1b can read them:

1. `agent_cache.compliance_penalty: Numeric(5, 2) NOT NULL DEFAULT 0` with a
   `CHECK (compliance_penalty >= 0)` constraint. Existing rows backfill to 0
   via the server default. `activity_score` and `wallet_score` are NOT
   altered by this migration (design §5.6).

2. `agent_compliance_flags` table keyed by `agent_id` (one row per agent),
   recording per-agent creator/owner flag state, JSONB source lists, the
   `creator_is_owner` derivation, a `flagged_data_stale` boolean, and the
   `refreshed_at` timestamp. The JSONB `server_default='[]'::jsonb` is the
   same pattern as `tags` / `categories` in `0002_enrich_agent_cache`.

The migration is symmetric: `downgrade()` drops the indexes, the check
constraint, the column, and the table in reverse order.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0012_compliance_penalty"
down_revision: Union[str, None] = "0011_fix_onchain_null_array"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- agent_cache.compliance_penalty -----------------------------------
    # `Numeric(5, 2)` matches the existing quality-score columns
    # (activity_score, wallet_score, ...). NOT NULL with `server_default=0`
    # so existing rows backfill cleanly.
    op.add_column(
        "agent_cache",
        sa.Column(
            "compliance_penalty",
            sa.Numeric(precision=5, scale=2),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.create_check_constraint(
        "compliance_penalty_nonneg",
        "agent_cache",
        "compliance_penalty >= 0",
    )

    # --- agent_compliance_flags table -------------------------------------
    # One row per agent (PK on `agent_id`). All booleans + JSONB lists
    # default to "no flag" / "empty list" so the table is writable by the
    # upserts in 1a-ii without explicit defaults in the ORM layer.
    op.create_table(
        "agent_compliance_flags",
        sa.Column("agent_id", sa.String(length=255), nullable=False),
        sa.Column(
            "creator_flagged",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "creator_flag_sources",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "owner_flagged",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "owner_flag_sources",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "creator_is_owner",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "flagged_data_stale",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "refreshed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("agent_id", name="pk_agent_compliance_flags"),
    )
    op.create_index(
        "ix_agent_compliance_flags_creator_flagged",
        "agent_compliance_flags",
        ["creator_flagged"],
    )
    op.create_index(
        "ix_agent_compliance_flags_owner_flagged",
        "agent_compliance_flags",
        ["owner_flagged"],
    )


def downgrade() -> None:
    # Symmetric reverse — drop indexes, table, check, column.
    op.drop_index(
        "ix_agent_compliance_flags_owner_flagged",
        table_name="agent_compliance_flags",
    )
    op.drop_index(
        "ix_agent_compliance_flags_creator_flagged",
        table_name="agent_compliance_flags",
    )
    op.drop_table("agent_compliance_flags")
    op.drop_constraint(
        "compliance_penalty_nonneg",
        "agent_cache",
        type_="check",
    )
    op.drop_column("agent_cache", "compliance_penalty")
