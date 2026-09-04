"""Mass re-classification of cached agents with the current classifier.

The category column used to default x402 rows to 'rebalancing' (dropped in
migration 0009). Existing rows keep their stored value until re-classified,
and the incremental sync only re-classifies ~100 agents per run — so a
one-shot pass over the cached rows applies the description/tags/termix
signals to the whole catalog without any upstream calls.

Reads only local columns (raw_metadata, tags, supported_protocols,
x402_supported, description) and updates rows whose rich category differs
from the stored one. Idempotent: a second run updates nothing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, text

from app.db.models.agent import AgentCache
from app.services.categories import compute_category

logger = logging.getLogger(__name__)

_BATCH = 500


@dataclass
class ReclassifyReport:
    """Totals of the re-classification pass."""

    total: int = 0
    updated: int = 0
    by_category: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "updated": self.updated,
            "by_category": self.by_category,
        }


def _classification_signals(row: AgentCache) -> tuple[str | None, list[str]]:
    """Extract the classifier inputs from a cached row (mirrors the sync
    worker's `_maybe_enrich_category` extraction)."""
    off = row.raw_metadata or {}
    if isinstance(off, dict):
        off = off.get("offchain_content") or {}
    if not isinstance(off, dict):
        off = {}
    termix = off.get("termix") or {}
    if not isinstance(termix, dict):
        termix = {}
    profile = termix.get("profile") or {}
    if not isinstance(profile, dict):
        profile = {}
    termix_category = profile.get("category")
    if not isinstance(termix_category, str):
        termix_category = None
    off_tags = off.get("tags")
    tags = off_tags if isinstance(off_tags, list) else (row.tags or [])
    return termix_category, list(tags)


async def reclassify_cached_agents(session: Any) -> ReclassifyReport:
    """Re-classify every cached agent locally; returns the change report.

    Batched commits so the pass is safe on large catalogs. Only rows whose
    stored category differs from the rich classification are updated.
    """
    report = ReclassifyReport()
    rows = (
        await session.scalars(
            select(AgentCache).order_by(AgentCache.token_id)
        )
    ).all()
    report.total = len(rows)
    updates: list[tuple[str, str]] = []
    for row in rows:
        termix_category, tags = _classification_signals(row)
        rich = compute_category(
            termix_category,
            tags,
            row.supported_protocols or [],
            bool(row.x402_supported),
            row.description,
        )
        report.by_category[rich] = report.by_category.get(rich, 0) + 1
        if rich != row.category:
            updates.append((rich, row.agent_id))

    for i in range(0, len(updates), _BATCH):
        batch = updates[i : i + _BATCH]
        for cat, agent_id in batch:
            await session.execute(
                text("UPDATE agent_cache SET category = :cat WHERE agent_id = :aid"),
                {"cat": cat, "aid": agent_id},
            )
        await session.commit()
    report.updated = len(updates)
    logger.info("reclassify: total=%s updated=%s %s", report.total, report.updated, report.by_category)
    return report


__all__ = ["ReclassifyReport", "reclassify_cached_agents"]