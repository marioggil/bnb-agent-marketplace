"""On-chain indexer worker — scans BSC and populates the onchain_transfers table.

Runs TWO concurrent workers during the FastAPI lifespan:

1. BACKFILL worker (Alchemy): scans historical blocks from $U token creation
   - Uses Alchemy: 75 CU per eth_getLogs, 10 blocks/call
   - 250 blocks/cycle, 4 min interval → ~18,750 CU/cycle → ~16.9M CU/month
   - Only provider supporting historical eth_getLogs on free tier

2. REALTIME worker (Chainstack): scans recent blocks to stay current
   - Uses Chainstack: 1 RU per request, 100 blocks/call
   - 75 blocks/cycle, 4 min interval → 1 RU/cycle → 10,800 RU/month (0.36%)
   - Chainstack free tier: eth_getLogs works for last ~100 blocks only

Both workers write to the same DB with ON CONFLICT DO NOTHING,
so no duplicates even if ranges temporarily overlap.

Usage:
    The workers are started automatically by the FastAPI lifespan in main.py.
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

# $U token creation block on BSC
U_TOKEN_CREATION_BLOCK = 71_922_111

# First block with actual $U transfers (empirically determined).
# The first ~200K blocks after creation have no transfers or NFT events.
U_FIRST_TRANSFER_BLOCK = 72_122_100

# Provider-specific block ranges for eth_getLogs
ALCHEMY_MAX_BLOCK_RANGE = 10       # Alchemy free tier: 10 blocks per request
CHAINSTACK_MAX_BLOCK_RANGE = 100   # Chainstack free tier: 100 blocks per request

# Backfill worker (Alchemy): scans historical blocks
# Alchemy: 75 CU per eth_getLogs, 10 blocks/call
# Budget: 25M CU/month → 333K calls → 3.33M blocks/month
# 250 blocks/cycle, 240s interval → ~25 calls/cycle → 18,750 CU/cycle
BACKFILL_CHUNK_SIZE = 250
BACKFILL_INTERVAL = 240

# Realtime worker (Chainstack): keeps up with chain head
# Chainstack: 1 RU per request, 100 blocks/call
# 75 blocks/cycle = 1 request = 1 RU/cycle → 10,800 RU/month (0.36% of 3M)
REALTIME_CHUNK_SIZE = 75
REALTIME_INTERVAL = 240


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
    max_range: int = ALCHEMY_MAX_BLOCK_RANGE,
) -> list[dict[str, Any]]:
    """Get event logs in a block range, capped by max_range per request."""
    if to_block - from_block > max_range - 1:
        to_block = from_block + max_range - 1

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
    max_range: int = ALCHEMY_MAX_BLOCK_RANGE,
) -> list[dict[str, Any]]:
    """Scan $U ERC-20 transfers in a block range."""
    all_logs = []
    current = from_block
    while current <= to_block:
        batch_end = min(current + max_range - 1, to_block)
        logs = await get_logs(
            client, current, batch_end, token_address, [TRANSFER_TOPIC, None, None],
            max_range=max_range,
        )
        all_logs.extend(logs)
        current = batch_end + 1
        await asyncio.sleep(0.05)
    return all_logs


async def scan_agent_nft_events(
    client: MultiRPCClient,
    from_block: int,
    to_block: int,
    max_range: int = ALCHEMY_MAX_BLOCK_RANGE,
) -> list[dict[str, Any]]:
    """Scan agent NFT (ERC-721) Transfer events on the IdentityRegistry."""
    all_logs = []
    current = from_block
    while current <= to_block:
        batch_end = min(current + max_range - 1, to_block)
        logs = await get_logs(
            client, current, batch_end, IDENTITY_REGISTRY, [TRANSFER_TOPIC, None, None, None],
            max_range=max_range,
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
    max_range: int = ALCHEMY_MAX_BLOCK_RANGE,
) -> tuple[int, int]:
    """Scan a block range and store results. Returns (transfers, events)."""
    from app.db.models.onchain_index import OnchainAgentEvent, OnchainTransfer

    # ---- Scan $U ERC-20 transfers ----
    u_logs = await scan_u_transfers(client, from_block, to_block, u_token, max_range=max_range)

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
    nft_logs = await scan_agent_nft_events(client, from_block, to_block, max_range=max_range)

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


# ============================================================================
# BACKFILL WORKER — uses Alchemy (only provider supporting historical eth_getLogs)
# ============================================================================

# In-memory backfill position tracker.  Survives across cycles within a
# single process lifetime.  On restart the backfill re-reads from the DB
# (which now contains both workers' writes) and picks up from there.
_backfill_last_scanned: int = 0


async def _backfill_cycle(client: MultiRPCClient) -> tuple[str, int]:
    """Run one backfill cycle: scan a chunk of historical blocks.

    Returns: (mode, blocks_processed)
    """
    global _backfill_last_scanned
    settings = get_settings()

    current_block = await get_current_block(client)
    if current_block is None:
        return "error", 0

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        from sqlalchemy import text

        # Determine where to start.
        # On first run (or after restart), seed from the DB's highest block
        # so we don't re-scan blocks the realtime worker already handled.
        if _backfill_last_scanned == 0:
            result = await session.execute(text("""
                SELECT COALESCE(
                    (SELECT MAX(block_number) FROM onchain_transfers),
                    (SELECT MAX(block_number) FROM onchain_agent_events),
                    0
                )
            """))
            db_max = result.scalar() or 0
            # If DB has blocks near the chain head (from realtime worker),
            # start backfill from where transfers actually begin
            # (empirically ~200K blocks after $U token creation).
            if db_max > U_TOKEN_CREATION_BLOCK + 1_000_000:
                _backfill_last_scanned = U_FIRST_TRANSFER_BLOCK - 1
            else:
                _backfill_last_scanned = db_max if db_max > 0 else U_FIRST_TRANSFER_BLOCK - 1

        from_block = _backfill_last_scanned + 1
        gap = current_block - from_block

        if gap <= REALTIME_CHUNK_SIZE:
            return "caught_up", 0

        # Scan a backfill chunk
        to_block = min(current_block, from_block + BACKFILL_CHUNK_SIZE - 1)

        wallet_to_agent = await _resolve_agent_wallets(session)
        u_token = U_TOKEN_MAINNET if settings.x402_chain_id == 56 else U_TOKEN_TESTNET

        transfers, events = await _scan_and_store(
            client, session, from_block, to_block, wallet_to_agent, u_token,
            max_range=ALCHEMY_MAX_BLOCK_RANGE,
        )

        blocks_processed = to_block - from_block + 1
        remaining = current_block - to_block
        usage = client.get_usage_summary()

        # Advance the in-memory tracker
        _backfill_last_scanned = to_block

        print(
            f"[backfill] {transfers}T + {events}E "
            f"({from_block:,}-{to_block:,}) | gap: {remaining:,} | "
            f"Alchemy: {usage['usage']['alchemy']:,} CU",
            flush=True,
        )

        return "backfill", blocks_processed


async def run_backfill_worker(alchemy_key: str) -> None:
    """Backfill worker: scans historical blocks using Alchemy."""
    if not alchemy_key:
        print("[backfill] DISABLED: no ALCHEMY_API_KEY", flush=True)
        return

    client = MultiRPCClient(alchemy_key=alchemy_key)
    print(f"[backfill] STARTED (Alchemy, {BACKFILL_CHUNK_SIZE}bl/{BACKFILL_INTERVAL}s)", flush=True)

    try:
        while True:
            try:
                mode, blocks = await _backfill_cycle(client)
                await asyncio.sleep(BACKFILL_INTERVAL if mode == "backfill" else BACKFILL_INTERVAL)
            except Exception as exc:
                print(f"[backfill] CYCLE FAILED: {exc}", flush=True)
                logger.exception("Backfill cycle failed")
                await asyncio.sleep(BACKFILL_INTERVAL)
    finally:
        await client.close()


# ============================================================================
# REALTIME WORKER — uses Chainstack (cheap for recent eth_getLogs)
# ============================================================================

async def _realtime_cycle(client: MultiRPCClient) -> tuple[str, int]:
    """Run one realtime cycle: scan recent blocks to stay current.

    Chainstack only serves eth_getLogs for the last ~100 blocks.
    If the DB is far behind, we jump to the chain head instead of
    trying to catch up — the backfill worker handles history.

    Returns: (mode, blocks_processed)
    """
    settings = get_settings()

    current_block = await get_current_block(client)
    if current_block is None:
        return "error", 0

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        from sqlalchemy import text

        result = await session.execute(
            text("SELECT COALESCE(MAX(block_number), 0) FROM onchain_transfers")
        )
        last_block = result.scalar()

        if last_block == 0:
            # First run: start from chain head minus our chunk
            from_block = current_block - REALTIME_CHUNK_SIZE
        else:
            from_block = last_block + 1

        gap = current_block - from_block

        if gap <= 0:
            return "caught_up", 0

        # If gap is larger than what Chainstack can handle, jump to chain head
        # The backfill worker will fill in the historical gap
        if gap > CHAINSTACK_MAX_BLOCK_RANGE * 2:
            from_block = current_block - REALTIME_CHUNK_SIZE

        # Only scan a small realtime chunk
        to_block = min(current_block, from_block + REALTIME_CHUNK_SIZE - 1)

        wallet_to_agent = await _resolve_agent_wallets(session)
        u_token = U_TOKEN_MAINNET if settings.x402_chain_id == 56 else U_TOKEN_TESTNET

        transfers, events = await _scan_and_store(
            client, session, from_block, to_block, wallet_to_agent, u_token,
            max_range=CHAINSTACK_MAX_BLOCK_RANGE,
        )

        blocks_processed = to_block - from_block + 1
        remaining = current_block - to_block
        usage = client.get_usage_summary()

        print(
            f"[realtime] {transfers}T + {events}E "
            f"({from_block:,}-{to_block:,}) | gap: {remaining:,} | "
            f"Chainstack: {usage['usage']['chainstack']:,} RU",
            flush=True,
        )

        return "realtime", blocks_processed


async def run_realtime_worker(chainstack_key: str) -> None:
    """Realtime worker: keeps up with chain head using Chainstack."""
    if not chainstack_key:
        print("[realtime] DISABLED: no CHAINSTACK_API_KEY", flush=True)
        return

    client = MultiRPCClient(chainstack_key=chainstack_key)
    print(f"[realtime] STARTED (Chainstack, {REALTIME_CHUNK_SIZE}bl/{REALTIME_INTERVAL}s)", flush=True)

    try:
        while True:
            try:
                mode, blocks = await _realtime_cycle(client)
                await asyncio.sleep(REALTIME_INTERVAL)
            except Exception as exc:
                print(f"[realtime] CYCLE FAILED: {exc}", flush=True)
                logger.exception("Realtime cycle failed")
                await asyncio.sleep(REALTIME_INTERVAL)
    finally:
        await client.close()


# ============================================================================
# MAIN — runs both workers concurrently
# ============================================================================

async def run_indexer_loop() -> None:
    """Start both backfill and realtime workers concurrently."""
    settings = get_settings()

    chainstack_key = getattr(settings, "chainstack_api_key", "")
    alchemy_key = getattr(settings, "alchemy_api_key", "")

    if not chainstack_key and not alchemy_key:
        print("[indexer] DISABLED: no RPC keys configured", flush=True)
        return

    print("[indexer] STARTING DUAL WORKERS:", flush=True)
    print(f"  Backfill:  Alchemy     {'✅' if alchemy_key else '❌'} ({BACKFILL_CHUNK_SIZE}bl/{BACKFILL_INTERVAL}s)", flush=True)
    print(f"  Realtime:  Chainstack  {'✅' if chainstack_key else '❌'} ({REALTIME_CHUNK_SIZE}bl/{REALTIME_INTERVAL}s)", flush=True)

    # Run both workers concurrently
    tasks = []
    if alchemy_key:
        tasks.append(asyncio.create_task(run_backfill_worker(alchemy_key)))
    if chainstack_key:
        tasks.append(asyncio.create_task(run_realtime_worker(chainstack_key)))

    if tasks:
        await asyncio.gather(*tasks)


# ---- Standalone entry point ----
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_indexer_loop())
