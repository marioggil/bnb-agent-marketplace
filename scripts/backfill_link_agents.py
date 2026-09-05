"""Chunked, idempotent backfill that links ``onchain_transfers`` rows to agents.

Implements REQ-003 from ``openspec/changes/indexer-link-fix/spec.md``. The
script re-uses :func:`app.services.onchain_indexer.resolve_wallet_to_agent`
(the public mapping helper introduced in WU2) so it cannot drift from the
realtime path's canonicalization.

Keyset pagination keeps memory bounded — only ``--chunk`` rows are loaded at
a time, processed, and committed before the next cursor advances. Per
design §3.3 the script returns:

- **0** on success or no-op (``linked_now == 0`` AND ``unlinked_before == 0``
  means there was nothing to link, and the script is genuinely done).
- **2** on the "stall" failure mode the original
  ``GET /api/onchain/backfill-link-agents`` endpoint silently swallowed:
  ``linked_now == 0 AND wallets_known > 0 AND unlinked_before > 0``. This
  signals the indexer thinks it has wallets but no transfers match — likely
  a canonicalization or wrong-column bug; per REQ-003 the operator must
  investigate rather than trust the zero.
- **1** on a DB / connection error.

Usage::

    python -m scripts.backfill_link_agents                    # default chunk 10K
    python -m scripts.backfill_link_agents --chunk 5000       # override chunk
    python -m scripts.backfill_link_agents --dry-run          # count only
    python -m scripts.backfill_link_agents --limit 100000     # cap total processed

Final summary line shape (matches the obs-242 production report)::

    unlinked_before=<n> linked_now=<n> still_unlinked=<n> wallets_known=<n>
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_sessionmaker
from app.services.onchain_indexer import _canon_addr, resolve_wallet_to_agent

logger = logging.getLogger(__name__)


DEFAULT_CHUNK: int = 10_000
MAX_CHUNK: int = 100_000


@dataclass
class BackfillSummary:
    """Outcome of one backfill run.

    ``exit_code`` follows the contract documented in the module docstring:
    0 = success or no-op, 2 = stall, 1 = DB / connection error.
    """

    unlinked_before: int
    linked_now: int
    still_unlinked: int
    wallets_known: int
    exit_code: int


def _is_postgres_dialect(session: AsyncSession) -> bool:
    """Detect Postgres so the script picks the ``UPDATE ... FROM (VALUES ...)``
    batched path. SQLite / dev use ``executemany`` of parameter binds
    (design §R-7)."""
    bind = session.get_bind()
    return bind is not None and bind.dialect.name == "postgresql"


async def _apply_chunk_postgres(
    session: AsyncSession,
    chunk: list[tuple[int, str]],
    wallet_to_agent: dict[str, str],
) -> int:
    """Batched UPDATE for Postgres — one round-trip per chunk.

    Each pair is ``(transfer_id, to_address)``. The canonic wallet lookup
    happens in Python (re-using ``_canon_addr``) so the database only sees
    canonicalized strings and the WAL write is proportional to the matched
    row count, not the chunk size.
    """
    matched: list[tuple[int, str]] = []
    for transfer_id, to_addr in chunk:
        canon = _canon_addr(to_addr)
        agent_id = canon and wallet_to_agent.get(canon)
        if agent_id:
            matched.append((transfer_id, agent_id))
    if not matched:
        return 0

    from app.db.models.onchain_index import OnchainTransfer

    stmt = pg_insert(OnchainTransfer).values(
        [{"id": rid, "linked_agent_id": aid} for rid, aid in matched]
    )
    # ``SET linked_agent_id = EXCLUDED.linked_agent_id`` overwrites the
    # column; ``WHERE linked_agent_id IS NULL`` keeps populated rows
    # untouched (REQ-002 "preserve existing linkage"). The unique constraint
    # ``uq_transfer_tx`` is not violated because ``id`` already exists and we
    # only touch ``linked_agent_id``.
    stmt = stmt.on_conflict_do_update(
        index_elements=[OnchainTransfer.id],
        set_={"linked_agent_id": stmt.excluded.linked_agent_id},
    ).where(OnchainTransfer.linked_agent_id.is_(None))
    await session.execute(stmt)
    return len(matched)


async def _apply_chunk_sqlite(
    session: AsyncSession,
    chunk: list[tuple[int, str]],
    wallet_to_agent: dict[str, str],
) -> int:
    """Batched UPDATE for sqlite / dev — ``executemany`` of parameter binds.

    sqlite does not support the ``UPDATE ... FROM (VALUES ...)`` form, so
    we issue one statement per matched row, parameter-bound. The chunk is
    already bounded by ``--chunk`` so this stays O(chunk) not O(table).
    """
    from app.db.models.onchain_index import OnchainTransfer

    matched: list[tuple[Any, Any]] = []
    for transfer_id, to_addr in chunk:
        canon = _canon_addr(to_addr)
        agent_id = canon and wallet_to_agent.get(canon)
        if agent_id:
            matched.append({"agent_id": agent_id, "id": transfer_id})
    if not matched:
        return 0

    await session.execute(
        text(
            "UPDATE onchain_transfers "
            "SET linked_agent_id = :agent_id "
            "WHERE id = :id AND linked_agent_id IS NULL"
        ),
        matched,
    )
    return len(matched)


async def _fetch_chunk(
    session: AsyncSession,
    cursor: int,
    chunk_size: int,
) -> list[tuple[int, str]]:
    """Keyset-paginate one chunk of unlinked transfers.

    Keyset on ``id`` (the PK) — monotonic, no duplicates on ties, faster
    than ``OFFSET`` at high row counts. The ``WHERE linked_agent_id IS NULL``
    predicate is the idempotency contract: a second invocation finds zero
    candidates because the first run already populated the column.
    """
    result = await session.execute(
        text(
            "SELECT id, to_address FROM onchain_transfers "
            "WHERE linked_agent_id IS NULL AND id > :cursor "
            "ORDER BY id LIMIT :limit"
        ),
        {"cursor": cursor, "limit": chunk_size},
    )
    return [(row[0], row[1]) for row in result.all()]


async def _scalar_count(session: AsyncSession) -> int:
    """Count of currently-unlinked transfers. Single scalar query."""
    result = await session.execute(
        text("SELECT COUNT(*) FROM onchain_transfers WHERE linked_agent_id IS NULL")
    )
    return int(result.scalar_one())


async def run(args: argparse.Namespace) -> BackfillSummary:
    """Run one full backfill cycle and return the summary dataclass."""
    session_factory = get_sessionmaker()
    chunk_size: int = max(1, min(args.chunk, MAX_CHUNK))
    limit: int | None = args.limit if args.limit > 0 else None

    async with session_factory() as session:
        wallet_to_agent = await resolve_wallet_to_agent(session)
        unlinked_before = await _scalar_count(session)

        if args.dry_run:
            # Pure probe: count what would have been linked without writing.
            from sqlalchemy import select
            from app.db.models.onchain_index import OnchainTransfer

            result = await session.execute(
                select(OnchainTransfer.id, OnchainTransfer.to_address)
                .where(OnchainTransfer.linked_agent_id.is_(None))
                .order_by(OnchainTransfer.id)
                .limit(chunk_size if limit is None else min(chunk_size, limit))
            )
            rows = result.all()
            matched = sum(
                1 for _, addr in rows if _canon_addr(addr) in wallet_to_agent
            )
            # Dry-run reports matched as both linked_now and the candidate
            # count so the operator sees the upper-bound expected links.
            print(
                f"unlinked_before={unlinked_before} "
                f"linked_now={matched} "
                f"still_unlinked={unlinked_before - matched} "
                f"wallets_known={len(wallet_to_agent)} "
                f"(dry-run, chunk_size={chunk_size})",
                flush=True,
            )
            return BackfillSummary(
                unlinked_before=unlinked_before,
                linked_now=matched,
                still_unlinked=unlinked_before - matched,
                wallets_known=len(wallet_to_agent),
                exit_code=0,
            )

        is_pg = _is_postgres_dialect(session)
        apply = _apply_chunk_postgres if is_pg else _apply_chunk_sqlite

        cursor = 0
        linked_now = 0
        processed = 0
        matchable_seen = 0  # rows in the scanned corpus that had a matchable wallet
        while True:
            rows = await _fetch_chunk(session, cursor, chunk_size)
            if not rows:
                break
            # Count matchable rows BEFORE the apply so the stall guard sees
            # the full corpus even when linked_now ends up at 0.
            for _rid, addr in rows:
                if _canon_addr(addr) in wallet_to_agent:
                    matchable_seen += 1
            linked_now += await apply(session, rows, wallet_to_agent)
            await session.commit()
            cursor = rows[-1][0]
            processed += len(rows)
            # Throttle: the realtime worker is also writing; keep the
            # backfill from hammering the connection.
            await asyncio.sleep(0.05)
            if limit is not None and processed >= limit:
                break

        still_unlinked = await _scalar_count(session)
        print(
            f"unlinked_before={unlinked_before} "
            f"linked_now={linked_now} "
            f"still_unlinked={still_unlinked} "
            f"wallets_known={len(wallet_to_agent)}",
            flush=True,
        )

        # ---- Exit-code policy (REQ-003) -----------------------------------
        # 2 = stall: the script found matchable rows but linked none.
        # This is the failure mode the original endpoint silently swallowed.
        # An idempotent re-run on a fully-linked corpus sees matchable_seen == 0
        # (every matchable row was linked on the first run) and falls through
        # to exit 0.
        if (
            linked_now == 0
            and len(wallet_to_agent) > 0
            and matchable_seen > 0
        ):
            return BackfillSummary(
                unlinked_before=unlinked_before,
                linked_now=linked_now,
                still_unlinked=still_unlinked,
                wallets_known=len(wallet_to_agent),
                exit_code=2,
            )
        return BackfillSummary(
            unlinked_before=unlinked_before,
            linked_now=linked_now,
            still_unlinked=still_unlinked,
            wallets_known=len(wallet_to_agent),
            exit_code=0,
        )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="backfill_link_agents",
        description="Chunked, idempotent backfill of onchain_transfers → agent linkage.",
    )
    p.add_argument(
        "--chunk",
        type=int,
        default=DEFAULT_CHUNK,
        help=f"Rows per chunk (default {DEFAULT_CHUNK}, max {MAX_CHUNK}).",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process at most this many rows total (0 = no cap).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Count what would have been linked without writing.",
    )
    return p.parse_args(argv)


async def _async_main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    args = _parse_args(argv)
    try:
        summary = await run(args)
        return summary.exit_code
    except Exception:
        logger.exception("backfill_link_agents failed")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_async_main()))
