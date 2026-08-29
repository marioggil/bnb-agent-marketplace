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
    """Check indexer health: last indexed block and counts for both tables."""
    from app.db.models.onchain_index import OnchainTransfer, OnchainAgentEvent
    from app.config import get_settings
    import httpx

    settings = get_settings()

    transfers = await db.execute(
        select(
            func.max(OnchainTransfer.block_number).label("last_block"),
            func.min(OnchainTransfer.block_number).label("first_block"),
            func.count().label("total"),
        ).select_from(OnchainTransfer)
    )
    t_row = transfers.one()

    events = await db.execute(
        select(
            func.max(OnchainAgentEvent.block_number).label("last_block"),
            func.min(OnchainAgentEvent.block_number).label("first_block"),
            func.count().label("total"),
        ).select_from(OnchainAgentEvent)
    )
    e_row = events.one()

    last_block = max(t_row.last_block or 0, e_row.last_block or 0)

    # Quick Alchemy connectivity test
    alchemy_status = "not_configured"
    alchemy_key = getattr(settings, "alchemy_api_key", "")
    if alchemy_key:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"https://bnb-mainnet.g.alchemy.com/v2/{alchemy_key}",
                    json={"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1},
                )
                data = resp.json()
                if "result" in data:
                    alchemy_block = int(data["result"], 16)
                    alchemy_status = f"ok (block {alchemy_block})"
                elif "error" in data:
                    alchemy_status = f"error: {data['error'].get('message', 'unknown')[:80]}"
                else:
                    alchemy_status = "unexpected response"
        except Exception as e:
            alchemy_status = f"connection failed: {str(e)[:60]}"

    return {
        "last_block": last_block,
        "transfers": t_row.total or 0,
        "transfers_first_block": t_row.first_block,
        "agent_events": e_row.total or 0,
        "events_first_block": e_row.first_block,
        "status": "healthy" if last_block else "empty",
        "keys": {
            "alchemy": "set" if alchemy_key else "missing",
            "chainstack": "set" if getattr(settings, "chainstack_api_key", "") else "missing",
        },
        "alchemy_test": alchemy_status,
    }


@router.get("/test-block/{block_number}")
async def test_block_transfers(
    block_number: int,
) -> dict:
    """TEMP: Test if Alchemy can see $U transfers at a specific block."""
    from app.config import get_settings
    import httpx

    settings = get_settings()
    alchemy_key = getattr(settings, "alchemy_api_key", "")
    if not alchemy_key:
        return {"error": "no ALCHEMY_API_KEY"}

    U_TOKEN = "0xcE24439F2D9C6a2289F741120FE202248B666666"
    TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

    results = {}

    # Test 1: eth_getLogs for $U transfers at this block (10-block range)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"https://bnb-mainnet.g.alchemy.com/v2/{alchemy_key}",
                json={
                    "jsonrpc": "2.0",
                    "method": "eth_getLogs",
                    "params": [{
                        "fromBlock": hex(block_number),
                        "toBlock": hex(block_number + 9),
                        "address": U_TOKEN,
                        "topics": [TRANSFER_TOPIC, None, None],
                    }],
                    "id": 1,
                },
            )
            data = resp.json()
            if "result" in data:
                logs = data["result"]
                results["u_transfers"] = len(logs)
                results["sample"] = [
                    {
                        "from": "0x" + log["topics"][1][-40:],
                        "to": "0x" + log["topics"][2][-40:],
                        "value": str(int(log["data"], 16)),
                        "block": int(log["blockNumber"], 16),
                        "tx": log["transactionHash"][:20] + "...",
                    }
                    for log in logs[:3]
                ]
            elif "error" in data:
                results["u_transfers_error"] = data["error"].get("message", "unknown")
            else:
                results["u_transfers"] = "unexpected"
    except Exception as e:
        results["u_transfers_error"] = str(e)[:100]

    # Test 2: eth_getLogs for agent NFT events at this block
    IDENTITY_REGISTRY = "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"https://bnb-mainnet.g.alchemy.com/v2/{alchemy_key}",
                json={
                    "jsonrpc": "2.0",
                    "method": "eth_getLogs",
                    "params": [{
                        "fromBlock": hex(block_number),
                        "toBlock": hex(block_number + 9),
                        "address": IDENTITY_REGISTRY,
                        "topics": [TRANSFER_TOPIC, None, None, None],
                    }],
                    "id": 2,
                },
            )
            data = resp.json()
            if "result" in data:
                results["nft_events"] = len(data["result"])
            elif "error" in data:
                results["nft_events_error"] = data["error"].get("message", "unknown")
    except Exception as e:
        results["nft_events_error"] = str(e)[:100]

    results["block_tested"] = block_number
    results["range"] = f"{block_number}-{block_number + 9}"
    return results


@router.get("/debug-scan")
async def debug_scan() -> dict:
    """TEMP: Run _scan_and_store_direct directly to test it."""
    from app.config import get_settings
    from app.db.session import get_sessionmaker
    from decimal import Decimal
    from datetime import datetime, timezone
    import httpx

    settings = get_settings()
    alchemy_key = getattr(settings, "alchemy_key", "") or getattr(settings, "alchemy_api_key", "")
    if not alchemy_key:
        return {"error": "no key"}

    RPC = f"https://bnb-mainnet.g.alchemy.com/v2/{alchemy_key}"
    U_TOKEN = "0xcE24439F2D9C6a2289F741120FE202248B666666"
    TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

    results = {}

    # Test: scan 10 blocks using same logic as _scan_and_store_direct
    try:
        async with httpx.AsyncClient(timeout=15.0) as hc:
            r = await hc.post(RPC, json={
                "jsonrpc": "2.0", "method": "eth_getLogs", "id": 1,
                "params": [{"fromBlock": hex(72_122_110), "toBlock": hex(72_122_119),
                           "address": U_TOKEN, "topics": [TOPIC, None, None]}]
            })
            data = r.json()
            if "result" in data:
                logs = data["result"]
                results["logs_found"] = len(logs)
            else:
                results["error"] = data.get("error", "unknown")
                return results
    except Exception as e:
        results["error"] = str(e)[:200]
        return results

    # Insert into DB
    if logs:
        from app.db.session import get_sessionmaker
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from app.db.models.onchain_index import OnchainTransfer

        session_factory = get_sessionmaker()
        async with session_factory() as session:
            inserted = 0
            for log in logs:
                from_addr = "0x" + log["topics"][1][-40:]
                to_addr = "0x" + log["topics"][2][-40:]
                value = Decimal(int(log["data"], 16)) / Decimal(10**18)
                block_num = int(log["blockNumber"], 16)
                tx = log["transactionHash"]

                stmt = pg_insert(OnchainTransfer).values(
                    from_address=from_addr, to_address=to_addr, value=value,
                    block_number=block_num, timestamp=datetime.now(timezone.utc),
                    tx_hash=tx, transfer_type="erc20_u", linked_agent_id=None,
                )
                stmt = stmt.on_conflict_do_nothing()
                r = await session.execute(stmt)
                inserted += r.rowcount
            await session.commit()
            results["inserted"] = inserted
            results["total_logs"] = len(logs)

    return results


@router.get("/backfill-state")
async def backfill_state() -> dict:
    """Show backfill worker state and DB state."""
    from app.services.onchain_indexer import get_backfill_state
    from app.db.session import get_sessionmaker
    from sqlalchemy import text

    state = get_backfill_state()
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        r1 = await session.execute(text("SELECT COALESCE(MAX(block_number), 0) FROM onchain_transfers"))
        r2 = await session.execute(text("SELECT COUNT(*) FROM onchain_transfers"))
        r3 = await session.execute(text("SELECT COALESCE(MAX(block_number), 0) FROM onchain_agent_events"))
        db_transfers_max = r1.scalar()
        db_transfers_count = r2.scalar()
        db_events_max = r3.scalar()

    return {
        "backfill_last_scanned": state["last_scanned"],
        "db_transfers_max_block": db_transfers_max,
        "db_transfers_count": db_transfers_count,
        "db_events_max_block": db_events_max,
    }


__all__ = ["router"]
