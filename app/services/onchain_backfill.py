"""Historical backfill for on-chain data.

Scans BSC from the $U token creation block (71,922,111) forward in
5,000-block chunks. Each chunk is scanned with 10-block batches
(Alchemy free tier limit). Stores $U transfers and agent NFT events.

Usage:
    python -m app.services.onchain_backfill
    python -m app.services.onchain_backfill --start-block 100000000
    python -m app.services.onchain_backfill --dry-run

CU cost per 5,000-block chunk:
    eth_getLogs:    500 × 75 CU  = 37,500 CU
    eth_getBlock:   500 × 16 CU  =  8,000 CU
    eth_blockNumber: 1 × 10 CU   =     10 CU
    Total per chunk:             ≈ 45,510 CU

Full backfill (71.9M → current): ~9,300 chunks ≈ 423M CU
At 24.7M CU/month free tier: ~17 months for full backfill.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

# Env preflight — must precede app.* imports
os.environ.setdefault("SECRET_KEY", "backfill-runner-not-a-real-server")

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import get_settings
from app.db.session import get_sessionmaker
from app.services.onchain_indexer import (
    BLOCK_RANGE,
    IDENTITY_REGISTRY,
    TRANSFER_TOPIC,
    U_TOKEN_MAINNET,
    U_TOKEN_TESTNET,
    OnchainIndexer,
    _extract_addr,
    _extract_token_id,
)

logger = logging.getLogger(__name__)

# $U token creation block on BSC
U_TOKEN_CREATION_BLOCK = 71_922_111

# Backfill parameters
CHUNK_SIZE = 5_000  # blocks per backfill chunk
BATCH_SIZE = BLOCK_RANGE  # 10 blocks per eth_getLogs call (Alchemy free tier)
BATCH_DELAY = 0.05  # seconds between RPC calls (rate limit safety)


async def _resolve_agent_wallets(session) -> dict[str, str]:
    """Build a mapping: lowercase wallet_address -> agent_id."""
    from app.db.models.agent import AgentCache

    result = await session.execute(
        select(AgentCache.agent_wallet, AgentCache.agent_id).where(
            AgentCache.agent_wallet.isnot(None)
        )
    )
    return {row[0].lower(): row[1] for row in result.all()}


async def _process_chunk(
    indexer: OnchainIndexer,
    session,
    from_block: int,
    to_block: int,
    wallet_to_agent: dict[str, str],
    u_token: str,
) -> tuple[int, int]:
    """Scan one chunk of blocks and insert into DB. Returns (transfers, events)."""
    from app.db.models.onchain_index import OnchainAgentEvent, OnchainTransfer

    # ---- Scan $U ERC-20 transfers ----
    u_logs = await indexer.scan_u_transfers(from_block, to_block, u_token)

    transfers_inserted = 0
    for log in u_logs:
        from_addr = _extract_addr(log["topics"][1])
        to_addr = _extract_addr(log["topics"][2])
        value = Decimal(int(log["data"], 16)) / Decimal(10**18)
        block_num = int(log["blockNumber"], 16)
        tx = log["transactionHash"]

        ts = await indexer.get_block_timestamp(block_num)
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
    nft_logs = await indexer.scan_agent_nft_events(from_block, to_block)

    events_inserted = 0
    for log in nft_logs:
        from_addr = _extract_addr(log["topics"][1])
        to_addr = _extract_addr(log["topics"][2])
        token_id = _extract_token_id(log["topics"][3])
        block_num = int(log["blockNumber"], 16)
        tx = log["transactionHash"]

        ts = await indexer.get_block_timestamp(block_num)
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


async def run_backfill(
    start_block: int = U_TOKEN_CREATION_BLOCK,
    dry_run: bool = False,
) -> None:
    """Run the historical backfill from start_block to current block."""
    settings = get_settings()
    alchemy_key = getattr(settings, "alchemy_api_key", "")
    if not alchemy_key and not dry_run:
        print("[backfill] ERROR: ALCHEMY_API_KEY not configured", flush=True)
        return

    rpc_url = f"https://bnb-mainnet.g.alchemy.com/v2/{alchemy_key}" if alchemy_key else ""
    u_token = U_TOKEN_MAINNET if settings.x402_chain_id == 56 else U_TOKEN_TESTNET

    indexer = OnchainIndexer(rpc_url) if rpc_url else None
    session_factory = get_sessionmaker()

    try:
        if dry_run:
            # For dry run, estimate from known values
            current_block_mock = 118_500_000  # approximate current
            total_blocks = current_block_mock - start_block
            total_chunks = (total_blocks + CHUNK_SIZE - 1) // CHUNK_SIZE
            est_cu = total_chunks * 45_510
            est_months = est_cu / 24_700_000

            print(f"[backfill] Start block:   {start_block:,}", flush=True)
            print(f"[backfill] Current block: ~{current_block_mock:,} (estimated)", flush=True)
            print(f"[backfill] Total blocks:  ~{total_blocks:,}", flush=True)
            print(f"[backfill] Chunk size:    {CHUNK_SIZE:,}", flush=True)
            print(f"[backfill] Total chunks:  ~{total_chunks:,}", flush=True)
            print(f"[backfill] Est. CU:       ~{est_cu:,} ({est_months:.1f} months at 24.7M/mo)", flush=True)
            print(f"[backfill] Dry run:       True", flush=True)
            print(flush=True)
            print("[backfill] Dry run — exiting without scanning", flush=True)
            return

        current_block = await indexer.get_current_block()
        if current_block is None:
            print("[backfill] ERROR: Could not get current block", flush=True)
            return

        total_blocks = current_block - start_block
        total_chunks = (total_blocks + CHUNK_SIZE - 1) // CHUNK_SIZE
        est_cu = total_chunks * 45_510
        est_months = est_cu / 24_700_000

        print(f"[backfill] Start block:   {start_block:,}", flush=True)
        print(f"[backfill] Current block: {current_block:,}", flush=True)
        print(f"[backfill] Total blocks:  {total_blocks:,}", flush=True)
        print(f"[backfill] Chunk size:    {CHUNK_SIZE:,}", flush=True)
        print(f"[backfill] Total chunks:  {total_chunks:,}", flush=True)
        print(f"[backfill] Est. CU:       {est_cu:,} ({est_months:.1f} months at 24.7M/mo)", flush=True)
        print(f"[backfill] Dry run:       {dry_run}", flush=True)
        print(flush=True)

        if dry_run:
            print("[backfill] Dry run — exiting without scanning", flush=True)
            return

        # Resolve agent wallets once
        async with session_factory() as session:
            wallet_to_agent = await _resolve_agent_wallets(session)
        print(f"[backfill] Loaded {len(wallet_to_agent)} agent wallets", flush=True)

        # Get last indexed block to optionally resume
        async with session_factory() as session:
            result = await session.execute(
                text("SELECT COALESCE(MAX(block_number), 0) FROM onchain_transfers")
            )
            last_indexed = result.scalar()

        if last_indexed > start_block:
            print(f"[backfill] Resuming from block {last_indexed + 1:,} (last indexed)", flush=True)
            start_block = last_indexed + 1

        # Process chunks
        chunk_num = 0
        total_transfers = 0
        total_events = 0
        t_start = time.time()

        current = start_block
        while current <= current_block:
            chunk_num += 1
            chunk_end = min(current + CHUNK_SIZE - 1, current_block)

            async with session_factory() as session:
                transfers, events = await _process_chunk(
                    indexer, session, current, chunk_end, wallet_to_agent, u_token
                )

            total_transfers += transfers
            total_events += events

            elapsed = time.time() - t_start
            rate = chunk_num / elapsed if elapsed > 0 else 0
            eta_chunks = total_chunks - chunk_num
            eta_seconds = eta_chunks / rate if rate > 0 else 0
            eta_hours = eta_seconds / 3600

            print(
                f"[backfill] Chunk {chunk_num}/{total_chunks} "
                f"(blocks {current:,}-{chunk_end:,}): "
                f"{transfers} transfers + {events} events | "
                f"Total: {total_transfers}T + {total_events}E | "
                f"ETA: {eta_hours:.1f}h",
                flush=True,
            )

            current = chunk_end + 1

        elapsed = time.time() - t_start
        print(flush=True)
        print(f"[backfill] DONE in {elapsed/3600:.1f} hours", flush=True)
        print(f"[backfill] Total: {total_transfers} transfers + {total_events} events", flush=True)

    finally:
        if indexer:
            await indexer.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Historical backfill for on-chain data")
    parser.add_argument(
        "--start-block",
        type=int,
        default=U_TOKEN_CREATION_BLOCK,
        help=f"Block to start from (default: {U_TOKEN_CREATION_BLOCK}, $U token creation)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show estimates without scanning",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    asyncio.run(run_backfill(start_block=args.start_block, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
