"""Pydantic schemas for the agent cache read API."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AgentOut(BaseModel):
    """One agent row in the listing/detail responses.

    Mirrors the full agent_cache model populated by the 8004scan
    detail endpoint. The marketplace UI consumes this in two modes:
    listing (only the headline fields are read) and detail page
    (everything). Pydantic dumps everything by default; the router
    layer is what decides what to send.
    """

    model_config = ConfigDict(from_attributes=True)

    # -- identity ---------------------------------------------------------
    id: int
    agent_id: str
    agent_internal_id: str | None = None
    chain_id: int
    chain_type: str | None = None
    token_id: int
    contract_address: str | None = None
    registry_address: str
    is_testnet: bool = False

    # -- owner ------------------------------------------------------------
    owner_id: str | None = None
    owner_address: str | None = None
    owner_ens: str | None = None
    owner_username: str | None = None
    owner_avatar_url: str | None = None
    owner_publisher_tier: str | None = None
    owner_certified_name: str | None = None
    creator_address: str | None = None

    # -- presentation -----------------------------------------------------
    name: str | None = None
    description: str | None = None
    agent_type: str | None = None
    image_url: str | None = None
    agent_wallet: str | None = None
    is_verified: bool = False
    star_count: int = 0
    watch_count: int = 0
    tags: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)

    # -- service endpoints (A2A, MCP, ENS, DID) ---------------------------
    services: dict[str, Any] = Field(default_factory=dict)

    # -- protocols / payments ---------------------------------------------
    x402_supported: bool = False
    supported_protocols: list[str] = Field(default_factory=list)
    supported_trust_models: list[str] = Field(default_factory=list)

    # -- score + feedback aggregates --------------------------------------
    average_score: Decimal | None = None
    total_score: Decimal | None = None
    total_feedbacks: int = 0
    total_validations: int = 0
    successful_validations: int = 0
    rank: int | None = None
    network_rank: int | None = None
    scores: dict[str, Any] | None = None

    # -- category (GENERATED column, persisted) ----------------------------
    category: str

    # -- cross-chain ------------------------------------------------------
    cross_chain_links: list[dict[str, Any]] = Field(default_factory=list)
    cross_chain_versions: list[dict[str, Any]] = Field(default_factory=list)

    # -- on-chain provenance ----------------------------------------------
    created_block_number: int | None = None
    created_tx_hash: str | None = None

    # -- endpoint health --------------------------------------------------
    is_active: bool = True
    is_endpoint_verified: bool = False
    endpoint_verified_at: datetime | None = None
    endpoint_verified_domain: str | None = None
    endpoint_verification_error: str | None = None
    endpoint_last_checked_at: datetime | None = None
    health_status: dict[str, Any] | None = None
    health_score: Decimal | None = None
    health_checked_at: datetime | None = None

    # -- quality scores (0-100) -------------------------------------------
    quality_score: Decimal | None = None
    popularity_score: Decimal | None = None
    activity_score: Decimal | None = None
    wallet_score: Decimal | None = None
    freshness_score: Decimal | None = None
    metadata_completeness_score: Decimal | None = None

    # -- supplementary identity -------------------------------------------
    ens: str | None = None
    did: str | None = None
    mcp_server: str | None = None
    mcp_version: str | None = None
    a2a_endpoint: str | None = None
    a2a_version: str | None = None
    agent_url: str | None = None

    # -- parse / metadata diagnostics -------------------------------------
    parse_status: dict[str, Any] | None = None
    raw_metadata: dict[str, Any] | None = None

    # -- upstream timestamps vs local mirror ------------------------------
    upstream_created_at: datetime | None = None
    upstream_updated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    # -- catch-all (unmodelled upstream fields) ---------------------------
    raw: dict[str, Any] = Field(default_factory=dict)
