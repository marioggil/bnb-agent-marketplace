"""OFAC flagged-address sync (T2 trust signal, DESIGN.md).

Mirrors the nightly-updated OFAC sanctioned digital-currency address lists
(0xB10C mirror, MIT) into the local `flagged_addresses` table. Semantics
are REPLACE-per-source: every run fetches the list for a source, normalizes
the addresses to lowercase, and swaps that source's rows — idempotent, and
addresses that drop off the upstream list disappear here too.

The lists live on public GitHub raw, so no auth is needed. A failed fetch
raises and leaves the previous rows untouched (the swap only happens after
a successful fetch + parse), so a transient upstream outage never wipes the
local mirror.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import delete

from app.db.models.flagged_address import FlaggedAddress

logger = logging.getLogger(__name__)

#: Source name → raw list URL (0xB10C/ofac-sanctioned-digital-currency-addresses).
FLAGGED_SOURCES: dict[str, str] = {
    "ofac-bsc": (
        "https://raw.githubusercontent.com/0xB10C/ofac-sanctioned-digital-currency-addresses"
        "/lists/sanctioned_addresses_BSC.json"
    ),
    "ofac-eth": (
        "https://raw.githubusercontent.com/0xB10C/ofac-sanctioned-digital-currency-addresses"
        "/lists/sanctioned_addresses_ETH.json"
    ),
}

#: Display labels for the /flagged page (keyed by FLAGGED_SOURCES key).
SOURCE_LABELS: dict[str, str] = {
    "ofac-bsc": "OFAC BSC",
    "ofac-eth": "OFAC ETH",
}

#: Repo page linked from the /flagged page.
SOURCE_REPO_URL: str = "https://github.com/0xB10C/ofac-sanctioned-digital-currency-addresses"

#: Per-list fetch timeout — the lists are a few MB of JSON; 15s is generous.
FETCH_TIMEOUT_S: float = 15.0


@dataclass(slots=True)
class SourceReport:
    """Fetch + insert counts for one source list."""

    source: str
    fetched: int
    inserted: int


@dataclass(slots=True)
class FlaggedSyncReport:
    """Outcome of one flagged-address sync run (per-source + totals)."""

    sources: list[SourceReport]
    total_fetched: int = 0
    total_inserted: int = 0

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly shape for the sync endpoint response."""
        return {
            "sources": {
                s.source: {"fetched": s.fetched, "inserted": s.inserted} for s in self.sources
            },
            "total_fetched": self.total_fetched,
            "total_inserted": self.total_inserted,
        }


def _normalize_addresses(raw: Any) -> list[str]:
    """Lowercase + dedupe the raw JSON list, dropping non-string junk.

    The upstream lists are clean arrays of hex addresses; the normalization
    is defensive (a stray uppercase or whitespace entry must never create a
    second row for the same wallet, and the PK is lowercase by contract).
    """
    if not isinstance(raw, list):
        raise ValueError(f"expected a JSON list of addresses, got {type(raw).__name__}")
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        addr = item.strip().lower()
        if addr and addr not in seen:
            seen.add(addr)
            out.append(addr)
    return out


async def _replace_source(session: Any, source: str, addresses: list[str]) -> int:
    """Swap every row of `source` for the freshly fetched list.

    DELETE-then-INSERT makes the mirror convergent with upstream (removals
    disappear) and is idempotent under re-runs. Returns the inserted count.
    """
    await session.execute(delete(FlaggedAddress).where(FlaggedAddress.source == source))
    if addresses:
        session.add_all(FlaggedAddress(address=addr, source=source) for addr in addresses)
    await session.commit()
    return len(addresses)


async def refresh_flagged_addresses() -> FlaggedSyncReport:
    """Fetch every source list and REPLACE its rows; return per-source counts.

    One commit per source: a failure on a later list (raised, not swallowed)
    keeps the sources already refreshed. Uses `AsyncSessionLocal` like the
    agent sync worker.
    """
    from app.db.session import AsyncSessionLocal

    report = FlaggedSyncReport(sources=[])
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_S) as client:
        async with AsyncSessionLocal() as session:
            for source, url in FLAGGED_SOURCES.items():
                logger.info("flagged sync: fetching %s (%s)", source, url)
                resp = await client.get(url)
                resp.raise_for_status()
                raw = resp.json()
                addresses = _normalize_addresses(raw)
                inserted = await _replace_source(session, source, addresses)
                logger.info(
                    "flagged sync: %s fetched=%s inserted=%s", source, len(raw), inserted
                )
                report.sources.append(
                    SourceReport(source=source, fetched=len(raw), inserted=inserted)
                )
                report.total_fetched += len(raw)
                report.total_inserted += inserted
    return report


__all__ = [
    "FETCH_TIMEOUT_S",
    "FLAGGED_SOURCES",
    "FlaggedSyncReport",
    "SOURCE_LABELS",
    "SOURCE_REPO_URL",
    "SourceReport",
    "refresh_flagged_addresses",
]