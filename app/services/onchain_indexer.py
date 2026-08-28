"""On-chain indexer worker — scans BSC and populates the onchain_transfers table.

Runs as a background task during the FastAPI lifespan. Scans $U (ERC-20)
transfers and agent NFT (ERC-721) events, links them to known agents,
and stores everything in the database for fast local queries.

Uses MultiRPCClient for load balancing:
    - Chainstack primary: 1 RU/request, 3M RU/month free
    - Alchemy fallback: CU-based, 30M CU/month free

Modes:
    - BACKFILL: when behind, scans 1,200 blocks/cycle (Chainstack budget)
    - REALTIME: when caught up, scans 250 blocks every 4 minutes

Usage:
    The worker is started automatically by the FastAPI lifespan in main.py.
    It can also be run standalone: python -m app.services.onchain_indexer
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import get_settings
from app.db.session import get_sessionmaker
from app.services.rpc_client import MultiRPCClient

logger = logging.getLogger(__name__)

# Contract addresses
U_TOKEN_MAINNET = "0xcE24439F2D9C6a2289F741120FE202248B666666"
U_TOKEN_TESTNET = "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565"
IDENTITY_REGISTRY = "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432"

# ERC-20 Transfer topic (same signature as ERC-721)
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# Alchemy free tier: 10 blocks per eth_getLogs request
BLOCK_RANGE = 10

# $U token creation block on BSC
U_TOKEN_CREATION_BLOCK = 71_922_111

# Real-time mode: 250 blocks every 4 minutes
INDEX_INTERVAL = 240  # 4 minutes
BLOCKS_PER_CYCLE = 250

# Backfill mode: 1,200 blocks every 4 minutes
# Chainstack: 3M RU/month, ~300 RU/cycle = ~3.25M RU/month (fits)
# Alchemy fallback only used on errors, negligible CU
BACKFILL_CHUNK_SIZE = 1200
BACKFILL_INTERVAL = 240  # same interval as real-time


def _extract_addr(topic: str | bytes) -> str:
    """Extract a 20-byte address from a padded 32-byte topic."""
    if isinstance(topic, bytes):
        return "0x" + topic.hex()[-40:]
    return "0x" + topic[-40:]


def _extract_token_id(topic: str | bytes) -> int:
    """Extract a token ID from a 32-byte topic."""
    if isinstance(topic, bytes):
        return int.from_bytes(topic, "big")
    return int(topic, 16)


async def _rpc_call(
    client: MultiRPCClient, method: str, params: list[Any]
) -> dict[str, Any] | None:
    """Shorthand for client.rpc_call."""
    return await client.rpc_call(method, params)


async def get_current_block(client: MultiRPCClient) -> int | None:
    """Get the latest block number."""
    data = await client.rpc_call("eth_blockNumber", [])
    if data and "result" in data:
        return int(data["result"], 16)
    return None


async def get_block_timestamp(client: MultiRPCClient, block_number: int) -> datetime | None:
    """Get the timestamp for a block number."""
    data = await client.rpc_call("eth_getBlockByNumber", [hex(block_number), False])
    if data and "result" in data and data["result"]:
        ts = int(data["result"]["timestamp"], 16)
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    return None


async def get_logs(
    client: MultiRPCClient,
    from_block: int,
    to_block: int,
    address: str,
    topics: list[str | None],
) -> list[dict[str, Any]]:
    """Get event logs in a block range (max 10 blocks for Alchemy free tier)."""
    if to_block - from_block > 9:
        to_block = from_block + 9

    data = await client.rpc_call(
        "eth_getLogs",
        [{"address": address, "topics": topics, "fromBlock": hex(from_block), "toBlock": hex(to_block)}],
    )
    if data and "result" in data and isinstance(data["result"], list):
        return data["result"]
    return []


async def scan_u_transfers(
    client: MultiRPCClient,
    from_block: int,
    to_block: int,
    token_address: str = U_TOKEN_MAINNET,
) -> list[dict[str, Any]]:
    """Scan $U ERC-20 transfers in a block range."""
    all_logs = []
    current = from_block
    while current <= to_block:
        batch_end = min(current + 9, to_block)
        logs = await get_logs(
            client, current, batch_end, token_address, [TRANSFER_TOPIC, None, None]
        )
        all_logs.extend(logs)
        current = batch_end + 1
        await asyncio.sleep(0.05)
    return all_logs


async def scan_agent_nft_events(
    client: MultiRPCClient,
    from_block: int,
    to_block: int,
) -> list[dict[str, Any]]:
    """Scan agent NFT (ERC-721) Transfer events on the IdentityRegistry."""
    all_logs = []
    current = from_block
    while current <= to_block:
        batch_end = min(current + 9, to_block)
        logs = await get_logs(
            client, current, batch_end, IDENTITY_REGISTRY, [TRANSFER_TOPIC, None, None, None]
        )
        all_logs.extend(logs)
        current = batch_end + 1
        await asyncio.sleep(0.05)
    return all_logs


async def _resolve_agent_wallets(session) -> dict[str, str]:
    """Build a mapping: lowercase wallet_address -> agent_id."""
    from app.db.models.agent import AgentCache

    result = await session.execute(
        select(AgentCache.agent_wallet, AgentCache.agent_id).where(
            AgentCache.agent_wallet.isnot(None)
        )
    )
    return {row[0].lower(): row[1] for row in result.all()}


async def _scan_and_store(
    client: MultiRPCClient,
    session,
    from_block: int,
    to_block: int,
    wallet_to_agent: dict[str, str],
    u_token: str,
) -> tuple[int, int]:
    """Scan a block range and store results. Returns (transfers, events)."""
    from app.db.models.onchain_index import OnchainAgentEvent, OnchainTransfer

    # ---- Scan $U ERC-20 transfers ----
    u_logs = await scan_u_transfers(client, from_block, to_block, u_token)

    transfers_inserted = 0
    for log in u_logs:
        from_addr = _extract_addr(log["topics"][1])
        to_addr = _extract_addr(log["topics"][2])
        value = Decimal(int(log["data"], 16)) / Decimal(10**18)
        block_num = int(log["blockNumber"], 16)
        tx = log["transactionHash"]

        ts = await get_block_timestamp(client, block_num)
        if ts is None:
            ts = datetime.now(timezone.utc)

        linked_agent = wallet_to_agent.get(to_addr.lower())

        stmt = pg_insert(OnchainTransfer).values(
            from_address=from_addr,
            to_address=to_addr,
            value=value,
            block_number=block_num,
            timestamp=ts,
            tx_hash=tx,
            transfer_type="erc20_u",
            linked_agent_id=linked_agent,
        )
        stmt = stmt.on_conflict_do_nothing()
        await session.execute(stmt)
        transfers_inserted += 1

    # ---- Scan agent NFT events ----
    nft_logs = await scan_agent_nft_events(client, from_block, to_block)

    events_inserted = 0
    for log in nft_logs:
        from_addr = _extract_addr(log["topics"][1])
        to_addr = _extract_addr(log["topics"][2])
        token_id = _extract_token_id(log["topics"][3])
        block_num = int(log["blockNumber"], 16)
        tx = log["transactionHash"]

        ts = await get_block_timestamp(client, block_num)
        if ts is None:
            ts = datetime.now(timezone.utc)

        event_type = "mint" if from_addr == "0x" + "0" * 40 else "transfer"

        from app.db.models.agent import BSC_CHAIN_ID, BSC_IDENTITY_REGISTRY, build_agent_id

        agent_id = build_agent_id(BSC_CHAIN_ID, BSC_IDENTITY_REGISTRY, token_id)

        stmt = pg_insert(OnchainAgentEvent).values(
            agent_id=agent_id,
            token_id=token_id,
            event_type=event_type,
            from_address=from_addr,
            to_address=to_addr,
            block_number=block_num,
            timestamp=ts,
            tx_hash=tx,
        )
        stmt = stmt.on_conflict_do_nothing()
        await session.execute(stmt)
        events_inserted += 1

    await session.commit()
    return transfers_inserted, events_inserted


async def _index_cycle(client: MultiRPCClient) -> tuple[str, int]:
    """Run one indexing cycle.

    Returns:
        (mode, blocks_processed) — mode is "backfill", "realtime", or "caught_up"
    """
    settings = get_settings()

    current_block = await get_current_block(client)
    if current_block is None:
        logger.warning("Could not get current block, skipping indexer cycle")
        return "error", 0

    # Read last indexed block from DB
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        from sqlalchemy import text

        result = await session.execute(
            text("SELECT COALESCE(MAX(block_number), 0) FROM onchain_transfers")
        )
        last_block = result.scalar()

        # First run: start from $U token creation block
        if last_block == 0:
            last_block = U_TOKEN_CREATION_BLOCK - 1

        from_block = last_block + 1
        gap = current_block - from_block

        # Decide mode based on gap size
        if gap > BACKFILL_CHUNK_SIZE:
            # BACKFILL MODE: scan a large chunk (Chainstack budget allows 1,200)
            to_block = min(current_block, from_block + BACKFILL_CHUNK_SIZE - 1)
            mode = "backfill"
        elif gap > 0:
            # REALTIME MODE: scan a small chunk to keep up
            to_block = min(current_block, from_block + BLOCKS_PER_CYCLE - 1)
            mode = "realtime"
        else:
            # CAUGHT UP: nothing to do
            return "caught_up", 0

        logger.info("Indexing blocks %d → %d (%s mode, gap=%d)", from_block, to_block, mode, gap)

        # Resolve agent wallets
        wallet_to_agent = await _resolve_agent_wallets(session)

        # Scan and store
        u_token = U_TOKEN_MAINNET if settings.x402_chain_id == 56 else U_TOKEN_TESTNET
        transfers, events = await _scan_and_store(
            client, session, from_block, to_block, wallet_to_agent, u_token
        )

        blocks_processed = to_block - from_block + 1
        remaining = current_block - to_block
        usage = client.get_usage_summary()

        print(
            f"[indexer] {mode.upper()}: {transfers} transfers + {events} events "
            f"(blocks {from_block:,}-{to_block:,}) | gap: {remaining:,} | "
            f"Chainstack: {usage['usage']['chainstack']:,} RU | "
            f"Alchemy: {usage['usage']['alchemy']:,} CU",
            flush=True,
        )

        return mode, blocks_processed


async def run_indexer_loop() -> None:
    """Background loop that runs the indexer with multi-RPC load balancing."""
    settings = get_settings()

    chainstack_key = getattr(settings, "chainstack_api_key", "")
    alchemy_key = getattr(settings, "alchemy_api_key", "")

    if not chainstack_key and not alchemy_key:
        print("[indexer] DISABLED: no CHAINSTACK_API_KEY or ALCHEMY_API_KEY", flush=True)
        logger.info("On-chain indexer disabled: no RPC keys configured")
        return

    client = MultiRPCClient(chainstack_key=chainstack_key, alchemy_key=alchemy_key)

    print(
        f"[indexer] STARTED "
        f"(primary={client._primary}, "
        f"backfill={BACKFILL_CHUNK_SIZE}bl, realtime={BLOCKS_PER_CYCLE}bl/{INDEX_INTERVAL}s)",
        flush=True,
    )
    logger.info(
        "On-chain indexer started (primary=%s, backfill=%d, realtime=%d/%ds)",
        client._primary, BACKFILL_CHUNK_SIZE, BLOCKS_PER_CYCLE, INDEX_INTERVAL,
    )

    try:
        while True:
            try:
                mode, blocks = await _index_cycle(client)
                if mode == "caught_up":
                    await asyncio.sleep(INDEX_INTERVAL)
                else:
                    await asyncio.sleep(BACKFILL_INTERVAL if mode == "backfill" else INDEX_INTERVAL)
            except Exception as exc:
                print(f"[indexer] CYCLE FAILED: {exc}", flush=True)
                logger.exception("Indexer cycle failed")
                await asyncio.sleep(INDEX_INTERVAL)
    finally:
        await client.close()


# ---- Standalone entry point ----
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_indexer_loop())
