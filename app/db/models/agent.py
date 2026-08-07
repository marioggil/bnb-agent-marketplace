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
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
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

    owner_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    x402_supported: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    supported_protocols: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    # D3 — GENERATED ALWAYS AS STORED. Persisted so it can be indexed and
    # filtered directly. The expression follows the design's MVP mapping:
    # x402 → rebalancing, oasf → rebalancing, else other. The Python
    # `services/categories.py` may later UPDATE this for richer rows.
    category: Mapped[str] = mapped_column(
        Text,
        Computed(
            "CASE "
            "WHEN x402_supported THEN 'rebalancing' "
            "WHEN 'oasf' = ANY (ARRAY("
            "SELECT jsonb_array_elements_text(supported_protocols)"
            ")) THEN 'rebalancing' "
            "ELSE 'other' "
            "END",
            persisted=True,
        ),
        nullable=False,
    )

    average_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    total_feedbacks: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    is_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    cross_chain_versions: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
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
            "ix_agent_cache_x402_true",
            "id",
            postgresql_where=text("x402_supported"),
        ),
        Index(
            "ix_agent_cache_verified_true",
            "id",
            postgresql_where=text("is_verified"),
        ),
        # GIN on the protocols list — used by ?protocols=oasf style filters.
        Index(
            "ix_agent_cache_supported_protocols_gin",
            "supported_protocols",
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
