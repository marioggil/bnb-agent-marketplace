"""CLI entrypoint for the AgentCache sync worker.

Usage:
  python -m app.worker.sync [--full | --incremental] [--batch N]

Default is `--incremental --batch 100`. See spec #23 for the requirements
(R1-R9) and the design's error model for the exit-code contract (R6).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import Sequence

from app.services.client_8004scan import Client8004Scan  # noqa: F401  (imported for warning side-effect)
from app.services.sync_worker import (
    DEFAULT_FULL_BATCH,
    DEFAULT_INCREMENTAL_BATCH,
    sync_full,
    sync_incremental,
)


def _setup_logging() -> None:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-5s %(name)s %(message)s",
    )


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.worker.sync",
        description="Sync BSC agents from 8004scan into the local AgentCache.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--incremental",
        dest="mode",
        action="store_const",
        const="incremental",
        help="Resume from SyncState.last_token_id + 1 (default).",
    )
    mode.add_argument(
        "--full",
        dest="mode",
        action="store_const",
        const="full",
        help="Re-walk from token_id 0; idempotent via ON CONFLICT.",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=None,
        help=(
            f"Override batch size (default: {DEFAULT_INCREMENTAL_BATCH} for "
            f"incremental, {DEFAULT_FULL_BATCH} for full)."
        ),
    )
    parser.set_defaults(mode="incremental")
    return parser.parse_args(argv)


def _resolve_batch(args: argparse.Namespace) -> int:
    if args.batch is not None:
        if args.batch <= 0:
            raise SystemExit("--batch must be > 0")
        return args.batch
    if args.mode == "full":
        return DEFAULT_FULL_BATCH
    return DEFAULT_INCREMENTAL_BATCH


def main(argv: Sequence[str] | None = None) -> int:
    _setup_logging()
    args = _parse_args(argv)

    # Spec R8 / Q7 — warn loudly if the Pro key is missing so the operator
    # notices the rate-limit drop. We do this BEFORE building Client8004Scan
    # because the client only sees the key on the first request; the CLI is
    # the visible entry point.
    if not os.environ.get("8004SCAN_API_KEY", "").strip():
        logging.getLogger(__name__).warning(
            "8004SCAN_API_KEY not set; rate limit ~50 rpm (Pro tier 500 rpm)"
        )

    batch = _resolve_batch(args)
    try:
        if args.mode == "full":
            report = asyncio.run(sync_full(batch=batch))
        else:
            report = asyncio.run(sync_incremental(batch=batch))
    except KeyboardInterrupt:  # pragma: no cover - operational
        logging.getLogger(__name__).warning("interrupted")
        return 1
    except Exception as exc:  # WorkerFatal-equivalent
        logging.getLogger(__name__).exception("sync failed: %s", exc)
        return 1

    # Spec R6 — exit 0 on success/partial.
    print(report)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
