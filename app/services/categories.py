"""Python-side category enrichment (post-pass for the GENERATED column).

The `agent_cache.category` column is a Postgres GENERATED ALWAYS AS STORED
column (design D3). It produces the dominant-80% mapping:

  - x402_supported=true        → 'rebalancing'
  - 'oasf' in supported_protocols → 'rebalancing'
  - else                       → 'other'

This module upgrades the rows that have rich oasf skills to one of the
full five-value enum. The sync worker calls `compute_category` after each
upsert and UPDATEs `category` when the rich mapping differs from the
GENERATED default.

MVP mapping (locked in design D3, to be expanded as more oasf skills are
observed on BSC):

  rebalancing, grid_trading, yield_optimisation, health_factor_monitoring, other
"""
from __future__ import annotations

from typing import Final

from app.db.models.agent import BSC_IDENTITY_REGISTRY

#: Full category enum (locked in design D3).
CATEGORIES: Final[tuple[str, ...]] = (
    "rebalancing",
    "grid_trading",
    "yield_optimisation",
    "health_factor_monitoring",
    "other",
)

#: Substrings inside oasf skills that map to a richer category. Match is
#: case-insensitive. The MVP list is intentionally small — we refine as
#: real data arrives.
_SKILL_HINTS: Final[dict[str, tuple[str, ...]]] = {
    "grid_trading": ("grid", "range", "dca"),
    "yield_optimisation": ("yield", "farm", "staking", "vault", "lend"),
    "health_factor_monitoring": ("health", "liquidation", "collateral"),
}

#: Protocol-prefix hints. The full oasf taxonomy is broader than this; the
#: MVP only encodes what the prototype has actually seen.
_PROTOCOL_HINTS: Final[dict[str, tuple[str, ...]]] = {
    "yield_optimisation": ("pancakeswap", "venus", "beefy", "aave"),
    "grid_trading": ("ascendex", "binance_grid"),
}


def compute_category(supported_protocols: list[str] | None, x402_supported: bool) -> str:
    """Return the category for a row.

    Order of precedence:
      1. `x402_supported` always wins → `rebalancing`.
      2. Otherwise, the first protocol/skill hint that matches determines
         the category.
      3. Otherwise, the GENERATED default would have been `rebalancing` if
         `oasf` is in protocols, else `other`. We mirror that here so the
         post-pass is a no-op for sparse rows.
    """
    if x402_supported:
        return "rebalancing"

    protocols = [p.lower() for p in (supported_protocols or []) if isinstance(p, str)]

    # First try skill hints; the worker passes the oasf `skills` array as
    # extra protocols so this single function can serve both fields.
    for cat, hints in _SKILL_HINTS.items():
        for token in protocols:
            if any(hint in token for hint in hints):
                return cat

    for cat, hints in _PROTOCOL_HINTS.items():
        for token in protocols:
            if any(token.startswith(prefix) or prefix in token for prefix in hints):
                return cat

    if "oasf" in protocols:
        return "rebalancing"
    return "other"


def canonical_agent_id(chain_id: int, token_id: int) -> str:
    """Build the canonical agent_id for BSC (single registry in this slice)."""
    return f"{chain_id}:{BSC_IDENTITY_REGISTRY}:{token_id}"
