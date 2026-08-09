"""Seed script: pull a batch of real BSC agents from 8004scan and upsert them.

Usage:
  python -m app.seed_agents [--limit N] [--page-size N]

Default is `--limit 50 --page-size 200`. The 8004scan /agents endpoint is
not strict about `chain_id` server-side, so we filter client-side
(BSC = 56) the same way the production sync worker does.

This is a one-shot helper, not a long-running worker. Once you have
agents in the table, use `python -m app.worker.sync` to keep them
up to date.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from typing import Sequence

from app.db.models.agent import BSC_CHAIN_ID
from app.db.session import AsyncSessionLocal
from app.services.client_8004scan import Client8004Scan
from app.services.sync_worker import _maybe_enrich_category, _row_from_agent, _upsert_agent


logger = logging.getLogger(__name__)

DEFAULT_LIMIT: int = 50
DEFAULT_PAGE_SIZE: int = 200
PROGRESS_EVERY: int = 25


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.seed_agents",
        description="Pull real BSC agents from 8004scan and upsert them.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Stop once this many BSC agents have been upserted (default: {DEFAULT_LIMIT}).",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help=(
            "Page size for the upstream /agents request "
            f"(default: {DEFAULT_PAGE_SIZE}; the upstream caps at ~200)."
        ),
    )
    return parser.parse_args(argv)


async def main(argv: Sequence[str] | None = None) -> int:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-5s %(name)s %(message)s",
    )

    args = _parse_args(argv)
    if args.limit <= 0:
        raise SystemExit("--limit must be > 0")
    if args.page_size <= 0:
        raise SystemExit("--page-size must be > 0")

    if not os.environ.get("8004SCAN_API_KEY", "").strip():
        logger.warning("8004SCAN_API_KEY not set; rate limit ~50 rpm (Pro tier 500 rpm)")

    started = time.monotonic()
    upserted = 0
    skipped_wrong_chain = 0
    fetched = 0

    async with AsyncSessionLocal() as session, Client8004Scan() as client:
        async for agent in client.iter_agents(chain_id=BSC_CHAIN_ID, page_size=args.page_size):
            fetched += 1
            if agent.chain_id is not None and int(agent.chain_id) != BSC_CHAIN_ID:
                skipped_wrong_chain += 1
                continue
            row = _row_from_agent(agent, category_override="")
            await _upsert_agent(session, row)
            await _maybe_enrich_category(session, agent, row["agent_id"])
            upserted += 1
            if upserted % PROGRESS_EVERY == 0:
                await session.commit()
                logger.info(
                    "seed: fetched=%s upserted=%s skipped_wrong_chain=%s",
                    fetched, upserted, skipped_wrong_chain,
                )
            if upserted >= args.limit:
                break
        await session.commit()

    duration = time.monotonic() - started
    print(
        f"SeedReport(fetched={fetched} upserted={upserted} "
        f"skipped_wrong_chain={skipped_wrong_chain} duration_s={duration:.2f})"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(main()))
