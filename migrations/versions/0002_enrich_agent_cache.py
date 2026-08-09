"""0002_enrich_agent_cache — extend agent_cache with the full 8004scan detail payload.

Migration 0001 captured the 25-field subset returned by `/agents`
(listing). The detail endpoint `/agents/{chain}/{token}` returns ~50
additional fields that are useful for the marketplace UI (services,
raw_metadata, quality scores, on-chain provenance, endpoint health)
and the sync_worker / seed_agents (now using the detail endpoint).

This migration adds nullable columns for everything we'd reasonably
want to filter / display; the upstream `raw` JSONB still captures
every unmodelled field for round-trip safety.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0002_enrich_agent_cache"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_COLUMNS: list[tuple[str, sa.Column]] = [
    # identity / owner
    ("agent_internal_id", sa.Column("agent_internal_id", postgresql.UUID(as_uuid=False), nullable=True)),
    ("chain_type", sa.Column("chain_type", sa.Text(), nullable=True)),
    ("contract_address", sa.Column("contract_address", sa.Text(), nullable=True)),
    ("is_testnet", sa.Column("is_testnet", sa.Boolean(), nullable=False, server_default=sa.text("false"))),
    ("creator_address", sa.Column("creator_address", sa.Text(), nullable=True)),
    ("owner_id", sa.Column("owner_id", postgresql.UUID(as_uuid=False), nullable=True)),
    ("owner_ens", sa.Column("owner_ens", sa.Text(), nullable=True)),
    ("owner_username", sa.Column("owner_username", sa.Text(), nullable=True)),
    ("owner_avatar_url", sa.Column("owner_avatar_url", sa.Text(), nullable=True)),
    ("owner_publisher_tier", sa.Column("owner_publisher_tier", sa.Text(), nullable=True)),
    ("owner_certified_name", sa.Column("owner_certified_name", sa.Text(), nullable=True)),
    # presentation
    ("agent_type", sa.Column("agent_type", sa.Text(), nullable=True)),
    ("agent_wallet", sa.Column("agent_wallet", sa.Text(), nullable=True)),
    ("star_count", sa.Column("star_count", sa.Integer(), nullable=False, server_default=sa.text("0"))),
    ("watch_count", sa.Column("watch_count", sa.Integer(), nullable=False, server_default=sa.text("0"))),
    ("tags", sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb"))),
    ("categories", sa.Column("categories", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb"))),
    # service endpoints
    ("services", sa.Column("services", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb"))),
    # protocols / payments
    ("supported_trust_models", sa.Column("supported_trust_models", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb"))),
    # scores / aggregates
    ("total_score", sa.Column("total_score", sa.Numeric(precision=8, scale=2), nullable=True)),
    ("total_validations", sa.Column("total_validations", sa.Integer(), nullable=False, server_default=sa.text("0"))),
    ("successful_validations", sa.Column("successful_validations", sa.Integer(), nullable=False, server_default=sa.text("0"))),
    ("rank", sa.Column("rank", sa.Integer(), nullable=True)),
    ("network_rank", sa.Column("network_rank", sa.Integer(), nullable=True)),
    ("scores", sa.Column("scores", postgresql.JSONB(astext_type=sa.Text()), nullable=True)),
    # cross-chain
    ("cross_chain_links", sa.Column("cross_chain_links", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb"))),
    # on-chain provenance
    ("created_block_number", sa.Column("created_block_number", sa.BigInteger(), nullable=True)),
    ("created_tx_hash", sa.Column("created_tx_hash", sa.Text(), nullable=True)),
    # endpoint health
    ("is_active", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true"))),
    ("is_endpoint_verified", sa.Column("is_endpoint_verified", sa.Boolean(), nullable=False, server_default=sa.text("false"))),
    ("endpoint_verified_at", sa.Column("endpoint_verified_at", sa.DateTime(timezone=True), nullable=True)),
    ("endpoint_verified_domain", sa.Column("endpoint_verified_domain", sa.Text(), nullable=True)),
    ("endpoint_verification_error", sa.Column("endpoint_verification_error", sa.Text(), nullable=True)),
    ("endpoint_last_checked_at", sa.Column("endpoint_last_checked_at", sa.DateTime(timezone=True), nullable=True)),
    ("health_status", sa.Column("health_status", sa.Text(), nullable=True)),
    ("health_score", sa.Column("health_score", sa.Numeric(precision=5, scale=2), nullable=True)),
    ("health_checked_at", sa.Column("health_checked_at", sa.DateTime(timezone=True), nullable=True)),
    # quality scores
    ("quality_score", sa.Column("quality_score", sa.Numeric(precision=5, scale=2), nullable=True)),
    ("popularity_score", sa.Column("popularity_score", sa.Numeric(precision=5, scale=2), nullable=True)),
    ("activity_score", sa.Column("activity_score", sa.Numeric(precision=5, scale=2), nullable=True)),
    ("wallet_score", sa.Column("wallet_score", sa.Numeric(precision=5, scale=2), nullable=True)),
    ("freshness_score", sa.Column("freshness_score", sa.Numeric(precision=5, scale=2), nullable=True)),
    ("metadata_completeness_score", sa.Column("metadata_completeness_score", sa.Numeric(precision=5, scale=2), nullable=True)),
    # supplementary identity
    ("ens", sa.Column("ens", sa.Text(), nullable=True)),
    ("did", sa.Column("did", sa.Text(), nullable=True)),
    ("mcp_server", sa.Column("mcp_server", sa.Text(), nullable=True)),
    ("mcp_version", sa.Column("mcp_version", sa.Text(), nullable=True)),
    ("a2a_endpoint", sa.Column("a2a_endpoint", sa.Text(), nullable=True)),
    ("a2a_version", sa.Column("a2a_version", sa.Text(), nullable=True)),
    ("agent_url", sa.Column("agent_url", sa.Text(), nullable=True)),
    # parse / metadata
    ("parse_status", sa.Column("parse_status", postgresql.JSONB(astext_type=sa.Text()), nullable=True)),
    ("raw_metadata", sa.Column("raw_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True)),
    # upstream timestamps
    ("upstream_created_at", sa.Column("upstream_created_at", sa.DateTime(timezone=True), nullable=True)),
    ("upstream_updated_at", sa.Column("upstream_updated_at", sa.DateTime(timezone=True), nullable=True)),
]


def upgrade() -> None:
    for name, col in _NEW_COLUMNS:
        op.add_column("agent_cache", col)

    op.create_unique_constraint(
        "uq_agent_cache_internal_id",
        "agent_cache",
        ["agent_internal_id"],
    )
    op.create_index(
        "ix_agent_cache_star_count_desc",
        "agent_cache",
        [sa.text("star_count DESC")],
    )
    op.create_index(
        "ix_agent_cache_endpoint_verified_true",
        "agent_cache",
        ["id"],
        postgresql_where=sa.text("is_endpoint_verified"),
    )
    op.create_index(
        "ix_agent_cache_services_gin",
        "agent_cache",
        ["services"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_agent_cache_services_gin", table_name="agent_cache")
    op.drop_index("ix_agent_cache_endpoint_verified_true", table_name="agent_cache")
    op.drop_index("ix_agent_cache_star_count_desc", table_name="agent_cache")
    op.drop_constraint("uq_agent_cache_internal_id", "agent_cache", type_="unique")

    for name, _ in reversed(_NEW_COLUMNS):
        op.drop_column("agent_cache", name)
