"""GET /api/onchain — on-chain analytics from the indexed database.

Provides hire stats, trends, and wallet activity from the onchain_transfers
table (populated by the background indexer). All queries are local — no RPC.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/onchain", tags=["onchain"])


@router.get("/stats/{agent_id}")
async def get_agent_onchain_stats(
    agent_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Get on-chain hire stats for an agent from the indexed database.

    Returns hire count, total volume, unique wallets, and trend data.
    """
    from app.db.models.onchain_index import OnchainTransfer

    # Count $U transfers TO this agent's wallet
    result = await db.execute(
        select(
            func.count().label("total_transfers"),
            func.count(func.distinct(OnchainTransfer.from_address)).label("unique_senders"),
            func.coalesce(func.sum(OnchainTransfer.value), 0).label("total_volume"),
        ).where(
            OnchainTransfer.linked_agent_id == agent_id,
            OnchainTransfer.transfer_type == "erc20_u",
        )
    )
    row = result.one()

    # Trend: last 7 days vs previous 7 days
    now = datetime.utcnow()
    week_ago = now - timedelta(days=7)
    two_weeks_ago = now - timedelta(days=14)

    recent = await db.execute(
        select(func.count()).where(
            OnchainTransfer.linked_agent_id == agent_id,
            OnchainTransfer.transfer_type == "erc20_u",
            OnchainTransfer.timestamp >= week_ago,
        )
    )
    recent_count = recent.scalar() or 0

    previous = await db.execute(
        select(func.count()).where(
            OnchainTransfer.linked_agent_id == agent_id,
            OnchainTransfer.transfer_type == "erc20_u",
            OnchainTransfer.timestamp >= two_weeks_ago,
            OnchainTransfer.timestamp < week_ago,
        )
    )
    previous_count = previous.scalar() or 0

    trend = "stable"
    if recent_count > previous_count * 1.2:
        trend = "increasing"
    elif recent_count < previous_count * 0.8:
        trend = "decreasing"

    return {
        "agent_id": agent_id,
        "total_hires": row.unique_senders,
        "total_transfers": row.total_transfers,
        "total_volume": str(row.total_volume),
        "unique_wallets": row.unique_senders,
        "trend": trend,
        "recent_7d": recent_count,
        "previous_7d": previous_count,
    }


@router.get("/stats/wallet/{wallet_address}")
async def get_wallet_onchain_stats(
    wallet_address: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Get on-chain stats for a wallet address (sender or receiver)."""
    from app.db.models.onchain_index import OnchainTransfer

    addr = wallet_address.lower()

    # Transfers FROM this wallet
    sent = await db.execute(
        select(
            func.count().label("count"),
            func.coalesce(func.sum(OnchainTransfer.value), 0).label("volume"),
        ).where(
            func.lower(OnchainTransfer.from_address) == addr,
            OnchainTransfer.transfer_type == "erc20_u",
        )
    )
    sent_row = sent.one()

    # Transfers TO this wallet
    received = await db.execute(
        select(
            func.count().label("count"),
            func.coalesce(func.sum(OnchainTransfer.value), 0).label("volume"),
        ).where(
            func.lower(OnchainTransfer.to_address) == addr,
            OnchainTransfer.transfer_type == "erc20_u",
        )
    )
    received_row = received.one()

    # Unique counterparties
    counterparties = await db.execute(
        select(func.count(func.distinct(OnchainTransfer.from_address))).where(
            func.lower(OnchainTransfer.to_address) == addr,
            OnchainTransfer.transfer_type == "erc20_u",
        )
    )
    unique_senders = counterparties.scalar() or 0

    return {
        "wallet": wallet_address,
        "sent_count": sent_row.count,
        "sent_volume": str(sent_row.volume),
        "received_count": received_row.count,
        "received_volume": str(received_row.volume),
        "unique_senders": unique_senders,
    }


@router.get("/trends")
async def get_global_trends(
    db: Annotated[AsyncSession, Depends(get_db)],
    days: int = Query(default=30, ge=1, le=365),
) -> dict:
    """Get global on-chain transfer trends for the marketplace."""
    from app.db.models.onchain_index import OnchainTransfer

    since = datetime.utcnow() - timedelta(days=days)

    # Daily breakdown
    result = await db.execute(
        select(
            text("date_trunc('day', timestamp) as day"),
            func.count().label("transfers"),
            func.count(func.distinct(OnchainTransfer.from_address)).label("unique_wallets"),
            func.coalesce(func.sum(OnchainTransfer.value), 0).label("volume"),
        )
        .where(OnchainTransfer.timestamp >= since)
        .group_by(text("day"))
        .order_by(text("day"))
    )
    rows = result.all()

    daily = [
        {
            "date": row.day.isoformat(),
            "transfers": row.transfers,
            "unique_wallets": row.unique_wallets,
            "volume": str(row.volume),
        }
        for row in rows
    ]

    # Top agents by transfer count
    top_agents = await db.execute(
        select(
            OnchainTransfer.linked_agent_id,
            func.count().label("transfers"),
            func.count(func.distinct(OnchainTransfer.from_address)).label("unique_wallets"),
            func.coalesce(func.sum(OnchainTransfer.value), 0).label("volume"),
        )
        .where(
            OnchainTransfer.linked_agent_id.isnot(None),
            OnchainTransfer.timestamp >= since,
        )
        .group_by(OnchainTransfer.linked_agent_id)
        .order_by(text("transfers DESC"))
        .limit(10)
    )
    top_rows = top_agents.all()

    top = [
        {
            "agent_id": row.linked_agent_id,
            "transfers": row.transfers,
            "unique_wallets": row.unique_wallets,
            "volume": str(row.volume),
        }
        for row in top_rows
    ]

    return {
        "period_days": days,
        "daily": daily,
        "top_agents": top,
    }


@router.get("/health")
async def indexer_health(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Check indexer health: last indexed block and transfer count."""
    result = await db.execute(
        select(
            func.max(text("block_number")).label("last_block"),
            func.count().label("total_transfers"),
        )
    )
    row = result.one()

    return {
        "last_block": row.last_block or 0,
        "total_transfers": row.total_transfers or 0,
        "status": "healthy" if row.last_block else "empty",
    }


__all__ = ["router"]
