"""GET /api/hires/onchain — on-chain hire verification.

Queries BSCScan for $U token transfers to an agent's wallet.
Provides verified hire counts from on-chain data.
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.agent import AgentCache
from app.db.session import get_db
from app.errors import NotFound
from app.services.client_bscscan import get_onchain_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/hires", tags=["hires"])


@router.get("/onchain/{chain_id}/{token_id}")
async def get_onchain_hire_stats(
    chain_id: int,
    token_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    min_amount: float = Query(default=0.01, description="Minimum $U amount to count"),
) -> dict:
    """Get on-chain hire statistics for an agent.

    Returns verified hire count from $U token transfers on BSC.
    """
    # Get agent from cache
    agent = await db.scalar(
        select(AgentCache).where(
            AgentCache.chain_id == chain_id, AgentCache.token_id == token_id
        )
    )
    if agent is None:
        raise NotFound(f"agent {chain_id}:{token_id} not cached")

    wallet = agent.agent_wallet
    if not wallet:
        return {
            "agent_id": f"{chain_id}:{token_id}",
            "wallet": None,
            "total_hires": 0,
            "total_volume": "0",
            "unique_senders": [],
            "error": "No wallet address found",
        }

    # Query Alchemy RPC
    client = get_onchain_client()
    if not client.api_key:
        return {
            "agent_id": f"{chain_id}:{token_id}",
            "wallet": wallet,
            "total_hires": 0,
            "total_volume": "0",
            "unique_senders": [],
            "error": "BSCScan API key not configured",
        }

    from decimal import Decimal

    stats = await client.get_hire_stats(
        wallet_address=wallet,
        min_amount=Decimal(str(min_amount)),
    )

    return {
        "agent_id": f"{chain_id}:{token_id}",
        "wallet": wallet,
        "total_hires": stats["total_hires"],
        "total_volume": stats["total_volume"],
        "unique_senders": stats["unique_senders"],
        "transfers_count": len(stats["transfers"]),
    }


@router.get("/onchain/wallet/{wallet_address}")
async def get_onchain_hire_stats_by_wallet(
    wallet_address: str,
    min_amount: float = Query(default=0.01, description="Minimum $U amount to count"),
) -> dict:
    """Get on-chain hire statistics by wallet address (no agent lookup)."""
    client = get_onchain_client()
    if not client.api_key:
        return {
            "wallet": wallet_address,
            "total_hires": 0,
            "total_volume": "0",
            "unique_senders": [],
            "error": "BSCScan API key not configured",
        }

    from decimal import Decimal

    stats = await client.get_hire_stats(
        wallet_address=wallet_address,
        min_amount=Decimal(str(min_amount)),
    )

    return {
        "wallet": wallet_address,
        "total_hires": stats["total_hires"],
        "total_volume": stats["total_volume"],
        "unique_senders": stats["unique_senders"],
        "transfers_count": len(stats["transfers"]),
    }


__all__ = ["router"]
