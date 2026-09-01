"""Seed script: pull a batch of real BSC agents from 8004scan and upsert them.

Usage:
  python -m app.seed_agents [--limit N] [--page-size N]

Two phases:
  1. Walk the paginated /agents listing to discover BSC token_ids
     (200 per page, client-side chain filter). Cheap: 1 request per page.
  2. For each token_id, hit the per-agent /agents/{chain}/{token}
     detail endpoint (~50 fields) and upsert. Heavier: 1 request per
     agent, but the data lands in the full set of agent_cache columns
     (services, raw_metadata, quality scores, etc.) instead of just
     the listing subset.

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
# 8004scan free tier is 50 rpm (~1.2 s/request). Sleep 1.5 s between
# detail requests to stay safely under the limit and avoid 429s.
DEFAULT_DETAIL_SLEEP_S: float = 1.5


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
            "Page size for the upstream /agents listing request "
            f"(default: {DEFAULT_PAGE_SIZE}; the upstream caps at ~200)."
        ),
    )
    parser.add_argument(
        "--detail-sleep",
        type=float,
        default=DEFAULT_DETAIL_SLEEP_S,
        help=(
            "Seconds to sleep between per-agent detail requests "
            f"(default: {DEFAULT_DETAIL_SLEEP_S}). Tune up if you hit 429s; "
            "tune down if you have a Pro API key."
        ),
    )
    return parser.parse_args(argv)


async def _discover_bsc_token_ids(
    client: Client8004Scan, limit: int, page_size: int
) -> tuple[list[int], int, int]:
    """Walk the paginated listing to collect BSC token_ids.

    Returns (token_ids, fetched_total, skipped_wrong_chain).
    """
    token_ids: list[int] = []
    fetched = 0
    skipped_wrong_chain = 0
    async for agent in client.iter_agents(chain_id=BSC_CHAIN_ID, page_size=page_size):
        fetched += 1
        if agent.chain_id is not None and int(agent.chain_id) != BSC_CHAIN_ID:
            skipped_wrong_chain += 1
            continue
        if agent.token_id is None:
            continue
        token_ids.append(int(agent.token_id))
        if len(token_ids) >= limit:
            break
    return token_ids, fetched, skipped_wrong_chain


async def _enrich_and_upsert(
    client: Client8004Scan,
    token_ids: list[int],
    detail_sleep_s: float,
) -> tuple[int, int]:
    """For each token_id, fetch the full detail and upsert.

    Returns (upserted, not_found).
    """
    upserted = 0
    not_found = 0
    async with AsyncSessionLocal() as session:
        for idx, token_id in enumerate(token_ids, start=1):
            agent = await client.get_agent(BSC_CHAIN_ID, token_id)
            if agent is None:
                not_found += 1
                continue
            if agent.chain_id is not None and int(agent.chain_id) != BSC_CHAIN_ID:
                # Defense in depth: the listing should have filtered, but
                # the token_id could in theory have been re-minted on a
                # different chain.
                continue
            row = _row_from_agent(agent, category_override="")
            await _upsert_agent(session, row)
            await _maybe_enrich_category(session, agent, row["agent_id"])
            upserted += 1
            if upserted % PROGRESS_EVERY == 0 or idx == len(token_ids):
                await session.commit()
                logger.info(
                    "seed enrich: progress=%s/%s upserted=%s not_found=%s",
                    idx,
                    len(token_ids),
                    upserted,
                    not_found,
                )
            # Stay under the 50 rpm free tier (1.2 s/request).
            if idx < len(token_ids) and detail_sleep_s > 0:
                await asyncio.sleep(detail_sleep_s)
    return upserted, not_found


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
    if args.detail_sleep < 0:
        raise SystemExit("--detail-sleep must be >= 0")

    if not os.environ.get("8004SCAN_API_KEY", "").strip():
        logger.warning("8004SCAN_API_KEY not set; rate limit ~50 rpm (Pro tier 500 rpm)")

    started = time.monotonic()

    async with Client8004Scan() as client:
        token_ids, fetched, skipped_wrong_chain = await _discover_bsc_token_ids(
            client, args.limit, args.page_size
        )
        logger.info(
            "seed discover: fetched=%s bsc_candidates=%s skipped_wrong_chain=%s",
            fetched,
            len(token_ids),
            skipped_wrong_chain,
        )
        upserted, not_found = await _enrich_and_upsert(client, token_ids, args.detail_sleep)

    duration = time.monotonic() - started
    print(
        f"SeedReport(fetched={fetched} bsc_candidates={len(token_ids)} "
        f"upserted={upserted} not_found={not_found} "
        f"skipped_wrong_chain={skipped_wrong_chain} duration_s={duration:.2f})"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(main()))
