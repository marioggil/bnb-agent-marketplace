"""Initial schema: 6 tables, hired_status enum, GENERATED `category` (D3).

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-07

This migration is hand-authored. autogenerate would miss the GENERATED
column expression, the partial indexes, and the hired_status enum values
(design D6, id 26). Future migrations may use autogenerate but must be
reviewed per the policy.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Reused across the DDL. Matches the constants in app.db.models.
_BSC_IDENTITY_REGISTRY = "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432"
_BSC_CHAIN_ID = 56
_ADDRESS_LEN = 42
_HIRED_STATUS_ENUM_NAME = "hired_status"


def upgrade() -> None:
    # pg_trgm is required by the GIN trigram index on agent_cache.name
    # (spec agents-cache R4). IF NOT EXISTS keeps re-runs idempotent.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # hired_status enum. Defined up-front because hired_agents.status
    # references it.
    hired_status = postgresql.ENUM(
        "pending",
        "paid",
        "failed",
        "cancelled",
        name=_HIRED_STATUS_ENUM_NAME,
        create_type=False,  # we create it explicitly below
    )
    hired_status.create(op.get_bind(), checkfirst=True)

    # ------------------------------------------------------------------
    # users
    # ------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column(
            "address",
            sa.String(length=_ADDRESS_LEN),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # ------------------------------------------------------------------
    # agent_cache
    # ------------------------------------------------------------------
    # The category GENERATED column follows the design D3 expression:
    # x402 → 'rebalancing'; 'oasf' in supported_protocols → 'rebalancing';
    # else 'other'. STORED so the btree on `category` works.
    category_sql = (
        "CASE "
        "WHEN x402_supported THEN 'rebalancing' "
        "WHEN 'oasf' = ANY (ARRAY("
        "SELECT jsonb_array_elements_text(supported_protocols)"
        ")) THEN 'rebalancing' "
        "ELSE 'other' "
        "END"
    )

    op.create_table(
        "agent_cache",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("chain_id", sa.Integer(), nullable=False),
        sa.Column("token_id", sa.BigInteger(), nullable=False),
        sa.Column("registry_address", sa.Text(), nullable=False),
        sa.Column("owner_address", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column(
            "x402_supported",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "supported_protocols",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "category",
            sa.Text(),
            sa.Computed(category_sql, persisted=True),
            nullable=False,
        ),
        sa.Column("average_score", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column(
            "total_feedbacks",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "is_verified",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "cross_chain_versions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "raw",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("agent_id", name="uq_agent_cache_agent_id"),
        sa.UniqueConstraint("chain_id", "token_id", name="uq_agent_cache_chain_token"),
        sa.CheckConstraint(
            "average_score IS NULL OR (average_score >= 0 AND average_score <= 100)",
            name="ck_agent_cache_agent_cache_score_range",
        ),
    )
    # Indexes that are not auto-derived from the UniqueConstraint.
    op.create_index("ix_agent_cache_category", "agent_cache", ["category"])
    op.execute(
        "CREATE INDEX ix_agent_cache_average_score_desc "
        "ON agent_cache (average_score DESC NULLS LAST)"
    )
    op.execute(
        "CREATE INDEX ix_agent_cache_total_feedbacks_desc "
        "ON agent_cache (total_feedbacks DESC)"
    )
    op.execute(
        "CREATE INDEX ix_agent_cache_created_at_desc "
        "ON agent_cache (created_at DESC)"
    )
    op.create_index(
        "ix_agent_cache_x402_true",
        "agent_cache",
        ["id"],
        postgresql_where=sa.text("x402_supported"),
    )
    op.create_index(
        "ix_agent_cache_verified_true",
        "agent_cache",
        ["id"],
        postgresql_where=sa.text("is_verified"),
    )
    op.create_index(
        "ix_agent_cache_supported_protocols_gin",
        "agent_cache",
        ["supported_protocols"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_agent_cache_name_trgm",
        "agent_cache",
        ["name"],
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )

    # ------------------------------------------------------------------
    # favorites
    # ------------------------------------------------------------------
    op.create_table(
        "favorites",
        sa.Column(
            "address",
            sa.String(length=_ADDRESS_LEN),
            sa.ForeignKey("users.address", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            sa.Text(),
            sa.ForeignKey("agent_cache.agent_id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_favorites_created_at", "favorites", ["created_at"])

    # ------------------------------------------------------------------
    # hired_agents
    # ------------------------------------------------------------------
    hired_status_col = postgresql.ENUM(
        "pending",
        "paid",
        "failed",
        "cancelled",
        name=_HIRED_STATUS_ENUM_NAME,
        create_type=False,
    )
    op.create_table(
        "hired_agents",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column(
            "address",
            sa.String(length=_ADDRESS_LEN),
            sa.ForeignKey("users.address", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            sa.Text(),
            sa.ForeignKey("agent_cache.agent_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            hired_status_col,
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("tx_hash", sa.String(length=66), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_hired_agents_pending",
        "hired_agents",
        ["created_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )

    # ------------------------------------------------------------------
    # sync_state
    # ------------------------------------------------------------------
    op.create_table(
        "sync_state",
        sa.Column(
            "id",
            sa.SmallInteger(),
            primary_key=True,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "last_token_id",
            sa.BigInteger(),
            server_default=sa.text("-1"),
            nullable=False,
        ),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "failed_token_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name="ck_sync_state_sync_state_singleton"),
    )

    # ------------------------------------------------------------------
    # auth_nonce
    # ------------------------------------------------------------------
    op.create_table(
        "auth_nonce",
        sa.Column(
            "address",
            sa.String(length=_ADDRESS_LEN),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("nonce", sa.String(length=64), nullable=False),
        sa.Column(
            "used",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_auth_nonce_address_used", "auth_nonce", ["address", "used"]
    )
    op.create_index("ix_auth_nonce_expires_at", "auth_nonce", ["expires_at"])


def downgrade() -> None:
    # Drop in reverse dependency order.
    op.drop_index("ix_auth_nonce_expires_at", table_name="auth_nonce")
    op.drop_index("ix_auth_nonce_address_used", table_name="auth_nonce")
    op.drop_table("auth_nonce")

    op.drop_table("sync_state")

    op.drop_index("ix_hired_agents_pending", table_name="hired_agents")
    op.drop_table("hired_agents")
    op.execute(f"DROP TYPE IF EXISTS {_HIRED_STATUS_ENUM_NAME}")

    op.drop_index("ix_favorites_created_at", table_name="favorites")
    op.drop_table("favorites")

    op.drop_index("ix_agent_cache_name_trgm", table_name="agent_cache")
    op.drop_index(
        "ix_agent_cache_supported_protocols_gin", table_name="agent_cache"
    )
    op.drop_index("ix_agent_cache_verified_true", table_name="agent_cache")
    op.drop_index("ix_agent_cache_x402_true", table_name="agent_cache")
    op.execute("DROP INDEX IF EXISTS ix_agent_cache_created_at_desc")
    op.execute("DROP INDEX IF EXISTS ix_agent_cache_total_feedbacks_desc")
    op.execute("DROP INDEX IF EXISTS ix_agent_cache_average_score_desc")
    op.drop_index("ix_agent_cache_category", table_name="agent_cache")
    op.drop_table("agent_cache")

    op.drop_table("users")

    # pg_trgm is left in place — it is harmless if unused and may be needed
    # by a follow-up change that re-runs 0001 in a partially-migrated db.
