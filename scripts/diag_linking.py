"""One-shot diagnostic for the indexer↔agent linking gap.

Implements REQ-001 from ``openspec/changes/indexer-link-fix/spec.md``. The
script prints, for a recent sample of ``onchain_transfers`` rows, how many of
them overlap with each of the four candidate wallet columns on
``agent_cache``: ``agent_wallet``, ``creator_address``, ``owner_address``,
``contract_address``. It also prints the four REQ-001 candidate counters
(case mismatch, embedded whitespace / null-byte padding, ``0x``-prefix drift,
length != 42) so the operator can decide which root cause dominates.

Always exits 0 — the script is a diagnostic, not a gate. The decision line
at the end is the operator-readable signal.

Usage::

    python -m scripts.diag_linking                  # default DATABASE_URL
    python -m scripts.diag_linking --sample 500    # override sample size
    python -m scripts.diag_linking --help
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.agent import AgentCache
from app.db.models.onchain_index import OnchainTransfer
from app.db.session import get_sessionmaker

logger = logging.getLogger(__name__)


_HEX_CHARS = frozenset("0123456789abcdef")


def _canon(addr: str | None) -> str | None:
    """Inline copy of the canonicalization helper used by the indexer.

    Kept dependency-free so this script can run against a database whose
    ``app.services.onchain_indexer`` is mid-edit (e.g. a half-applied
    ``pr-a-link-fix`` branch). The logic mirrors design §3.2 verbatim.
    """
    if not addr:
        return None
    s = addr.strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    if len(s) != 40 or any(c not in _HEX_CHARS for c in s):
        return None
    return "0x" + s


async def _candidate_columns(session: AsyncSession) -> dict[str, set[str]]:
    """Return ``{column_name: {canon(wallet), ...}}`` for every wallet-shaped
    column on ``agent_cache`` that REQ-001 names.

    A wallet is included only when its canonicalization succeeds; rows whose
    stored value is unparseable are silently dropped (consistent with the
    helper's contract).
    """
    cols = ("agent_wallet", "creator_address", "owner_address", "contract_address")
    out: dict[str, set[str]] = {c: set() for c in cols}
    for col in cols:
        result = await session.execute(
            select(getattr(AgentCache, col)).where(getattr(AgentCache, col).isnot(None))
        )
        for (raw,) in result.all():
            canon = _canon(raw)
            if canon is not None:
                out[col].add(canon)
    return out


def _counters_for_sample(sample_to_addrs: list[str]) -> dict[str, int]:
    """Count the four REQ-001 candidate-bug occurrences across ``to_address``
    values. Defensive — most rows are already canonical hex; the counters
    exist so a structurally-bad sync can be ruled in/out."""
    out = Counter(
        case_only_mismatch=0,
        whitespace_or_nullbyte=0,
        prefix_drift=0,
        length_bad=0,
        total=len(sample_to_addrs),
    )
    for raw in sample_to_addrs:
        if raw is None:
            continue
        if any(ch in raw for ch in (" ", "\t", "\n", "\x00")):
            out["whitespace_or_nullbyte"] += 1
        if not raw.lower().startswith("0x"):
            out["prefix_drift"] += 1
        body = raw[2:] if raw.lower().startswith("0x") else raw
        if len(body) != 40:
            out["length_bad"] += 1
        # Case-only mismatch is byte-equality with at least one uppercase hex
        # letter in `body`. The point is to rule in/out the case-only bug;
        # addresses whose hex body is already lowercase trivially have 0.
        if any(c.isalpha() for c in body):
            out["case_only_mismatch"] += 1
    return dict(out)


async def main(args: argparse.Namespace) -> int:
    session_factory = get_sessionmaker()
    sample_size: int = args.sample
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"=== indexer↔agent linking diagnostic @ {timestamp} ===", flush=True)
    print(f"DATABASE_URL={os.environ.get('DATABASE_URL', '<unset>')}", flush=True)
    print(f"sample_size={sample_size}", flush=True)

    async with session_factory() as session:
        # ---- Corpus size + already-linked count ----------------------
        total = (
            await session.execute(select(func.count()).select_from(OnchainTransfer))
        ).scalar_one()
        linked = (
            await session.execute(
                select(func.count())
                .select_from(OnchainTransfer)
                .where(OnchainTransfer.linked_agent_id.isnot(None))
            )
        ).scalar_one()
        print(f"\n[corpus] total={total} linked={linked} unlinked={total - linked}", flush=True)

        # ---- Recent sample ---------------------------------------------
        sample = (
            await session.execute(
                select(OnchainTransfer.id, OnchainTransfer.to_address)
                .order_by(OnchainTransfer.id.desc())
                .limit(sample_size)
            )
        ).all()
        distinct_to = {row[1].lower() for row in sample if row[1]}
        print(
            f"[sample] rows={len(sample)} distinct_to_address={len(distinct_to)}",
            flush=True,
        )

        # ---- REQ-001 candidate counters -------------------------------
        counters = _counters_for_sample([row[1] for row in sample])
        print("\n[REQ-001 candidate counters across sample to_address]", flush=True)
        for k in (
            "case_only_mismatch",
            "whitespace_or_nullbyte",
            "prefix_drift",
            "length_bad",
            "total",
        ):
            print(f"  {k}={counters[k]}", flush=True)

        # ---- Per-wallet-column overlap ---------------------------------
        wallets = await _candidate_columns(session)
        print("\n[agent_cache known-wallet sets (canon sizes)]", flush=True)
        for col, addrs in wallets.items():
            print(f"  {col}: {len(addrs)}", flush=True)

        print(
            "\n[overlap — distinct sample to_address vs each wallet column "
            "(lowercase / canon)]",
            flush=True,
        )
        results: dict[str, int] = {}
        for col, known in wallets.items():
            overlap = sum(1 for addr in distinct_to if _canon(addr) in known)
            results[col] = overlap
            print(f"  {col}: {overlap}", flush=True)

    # ---- Decision line --------------------------------------------------
    non_zero = {k: v for k, v in results.items() if v > 0}
    winner = max(results, key=results.get) if results else "none"
    if all(v == 0 for v in results.values()):
        decision = "structural"
    elif winner == "agent_wallet":
        # The canonical-code path: even a partial case-only match is fine,
        # so prefer "case" if the case counter is non-zero, otherwise report
        # the column that won (which IS agent_wallet).
        decision = "case" if counters["case_only_mismatch"] > 0 else "wrong-column"
    elif winner in ("creator_address", "owner_address", "contract_address"):
        decision = "wrong-column"
    else:
        decision = "structural"
    print(f"\ndecision: {decision} (winner column: {winner}, counts: {results})", flush=True)
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="diag_linking",
        description="One-shot diagnostic for the onchain_indexer↔agent_cache "
        "linking gap (REQ-001).",
    )
    p.add_argument(
        "--sample",
        type=int,
        default=100,
        help="Number of recent onchain_transfers rows to sample (default: 100).",
    )
    return p.parse_args(argv)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(asyncio.run(main(_parse_args())))
