"""AgentCache model — the single read path for /api/agents and HTMX views.

See `sdd/marketplace-scaffold/spec/agents-cache` (#19) and design #26 (data
model + D3 generated category). The canonical `agent_id` is
`{chainId}:{registry}:{tokenId}` per the ERC-8004 spec.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Float,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

if TYPE_CHECKING:
    pass


# Constants — also used by the sync worker when it builds canonical ids.
#: Default ERC-8004 IdentityRegistry on BSC mainnet. Hardcoded per design
#: because the marketplace only syncs BSC in this slice (decision Q6).
BSC_IDENTITY_REGISTRY: str = "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432"
BSC_CHAIN_ID: int = 56


def build_agent_id(chain_id: int, registry: str, token_id: int) -> str:
    """Build the canonical agent_id from its three parts."""
    return f"{chain_id}:{registry}:{token_id}"


class AgentCache(Base):
    """Mirrored 8004scan agent row.

    The `category` column is a Postgres GENERATED ALWAYS AS STORED expression
    (D3). It is indexable, auto-recomputes on upsert, and serves as the
    default. The sync worker may run a Python-side `categories.compute_category`
    post-pass to override this for rows that have rich oasf metadata.
    """

    __tablename__ = "agent_cache"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    chain_id: Mapped[int] = mapped_column(Integer, nullable=False)
    token_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    registry_address: Mapped[str] = mapped_column(Text, nullable=False)

    # ------------------------------------------------------------------
    # Identity / owner
    # ------------------------------------------------------------------
    #: 8004scan-internal UUID for the agent (used as a join key into
    #: /feedbacks and as a stable cross-source identifier).
    agent_internal_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    chain_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    contract_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_testnet: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    creator_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    owner_ens: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_username: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_publisher_tier: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_certified_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_address: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ------------------------------------------------------------------
    # Presentation
    # ------------------------------------------------------------------
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_wallet: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    star_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    watch_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    tags: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    categories: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    # ------------------------------------------------------------------
    # Service endpoints (A2A, MCP, ENS, DID) — the marketplace can
    # render deep-links to each surface from this single object.
    # ------------------------------------------------------------------
    services: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # ------------------------------------------------------------------
    # Protocols / payments
    # ------------------------------------------------------------------
    x402_supported: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    supported_protocols: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    supported_trust_models: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    # ------------------------------------------------------------------
    # Score + feedback aggregates
    # ------------------------------------------------------------------
    average_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    total_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    total_feedbacks: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    total_validations: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    successful_validations: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    network_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scores: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # D3 — GENERATED ALWAYS AS STORED. Persisted so it can be indexed and
    # filtered directly. The expression follows the design's MVP mapping:
    # x402 → rebalancing, oasf → rebalancing, else other. The Python
    # `services/categories.py` may later UPDATE this for richer rows.
    #
    # The 'oasf in supported_protocols' branch uses the `?` JSONB
    # containment operator because Postgres rejects subqueries in
    # GENERATED column expressions. See migration 0001_initial for the
    # matching SQL.
    category: Mapped[str] = mapped_column(
        Text,
        Computed(
            "CASE "
            "WHEN x402_supported THEN 'rebalancing' "
            "WHEN supported_protocols ? 'oasf' THEN 'rebalancing' "
            "ELSE 'other' "
            "END",
            persisted=True,
        ),
        nullable=False,
    )

    # ------------------------------------------------------------------
    # Cross-chain
    # ------------------------------------------------------------------
    cross_chain_links: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    cross_chain_versions: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    # ------------------------------------------------------------------
    # On-chain provenance
    # ------------------------------------------------------------------
    created_block_number: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_tx_hash: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ------------------------------------------------------------------
    # Endpoint health / verification
    # ------------------------------------------------------------------
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    is_endpoint_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    endpoint_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    endpoint_verified_domain: Mapped[str | None] = mapped_column(Text, nullable=True)
    endpoint_verification_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    endpoint_last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    health_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    health_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    health_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ------------------------------------------------------------------
    # Quality scores (0-100)
    # ------------------------------------------------------------------
    quality_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    popularity_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    activity_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    wallet_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    freshness_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    metadata_completeness_score: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), nullable=True
    )

    # ------------------------------------------------------------------
    # Supplementary identity
    # ------------------------------------------------------------------
    ens: Mapped[str | None] = mapped_column(Text, nullable=True)
    did: Mapped[str | None] = mapped_column(Text, nullable=True)
    mcp_server: Mapped[str | None] = mapped_column(Text, nullable=True)
    mcp_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    a2a_endpoint: Mapped[str | None] = mapped_column(Text, nullable=True)
    a2a_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ------------------------------------------------------------------
    # Parse / metadata diagnostics
    # ------------------------------------------------------------------
    parse_status: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    raw_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # ------------------------------------------------------------------
    # Upstream timestamps (vs. our `created_at`/`updated_at` for the mirror).
    # ------------------------------------------------------------------
    upstream_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    upstream_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ------------------------------------------------------------------
    # Catch-all for fields we don't model explicitly.
    # ------------------------------------------------------------------
    raw: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        UniqueConstraint("chain_id", "token_id", name="uq_agent_cache_chain_token"),
        UniqueConstraint("agent_internal_id", name="uq_agent_cache_internal_id"),
        # btree on category (used by the filter UI).
        Index("ix_agent_cache_category", "category"),
        Index(
            "ix_agent_cache_average_score_desc",
            text("average_score DESC NULLS LAST"),
        ),
        Index(
            "ix_agent_cache_total_feedbacks_desc",
            text("total_feedbacks DESC"),
        ),
        Index(
            "ix_agent_cache_created_at_desc",
            text("created_at DESC"),
        ),
        Index(
            "ix_agent_cache_star_count_desc",
            text("star_count DESC"),
        ),
        Index(
            "ix_agent_cache_x402_true",
            "id",
            postgresql_where=text("x402_supported"),
        ),
        Index(
            "ix_agent_cache_verified_true",
            "id",
            postgresql_where=text("is_verified"),
        ),
        Index(
            "ix_agent_cache_endpoint_verified_true",
            "id",
            postgresql_where=text("is_endpoint_verified"),
        ),
        # GIN on the protocols list — used by ?protocols=oasf style filters.
        Index(
            "ix_agent_cache_supported_protocols_gin",
            "supported_protocols",
            postgresql_using="gin",
        ),
        # GIN on services — used to filter agents exposing A2A/MCP surfaces.
        Index(
            "ix_agent_cache_services_gin",
            "services",
            postgresql_using="gin",
        ),
        # GIN trigram on `name` requires the pg_trgm extension. Created in
        # 0001_initial via op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm").
        Index(
            "ix_agent_cache_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
        CheckConstraint(
            "average_score IS NULL OR (average_score >= 0 AND average_score <= 100)",
            name="agent_cache_score_range",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"AgentCache(agent_id={self.agent_id!r})"
