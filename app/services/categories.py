"""Python-side category enrichment (post-pass for the GENERATED column).

The `agent_cache.category` column is a Postgres GENERATED ALWAYS AS STORED
column (design D3). It produces the dominant-80% mapping:

  - x402_supported=true        → 'rebalancing'
  - 'oasf' in supported_protocols → 'rebalancing'
  - else                       → 'other'

This module upgrades the rows that have rich signals to one of the full
11-value taxonomy. The sync worker calls `compute_category` after each
upsert and UPDATEs `category` when the rich mapping differs from the
GENERATED default.

Taxonomy: 10 categories + `other` (accepted from docs/category-study.md,
2026-08-26; that study is the source of truth for the taxonomy). The
signal priority (design D2) is:

  1. `termix.profile.category` (offchain_content) — mapped 1:1 per study §4
  2. offchain tags — `_TAG_HINTS` per study §5, tiebreak = CATEGORIES order
  3. `x402_supported` → `rebalancing` (the GENERATED default)
  4. existing skill/protocol hints (kept for back-compat)
  5. `other` (mirrors the GENERATED default for sparse rows)
"""

from __future__ import annotations

from typing import Final

from app.db.models.agent import BSC_IDENTITY_REGISTRY

#: Full category enum (taxonomy accepted from category-study.md; TAX-1).
#: Order is the tiebreak order for multi-tag agents (design D5).
CATEGORIES: Final[tuple[str, ...]] = (
    "rebalancing",
    "grid_trading",
    "yield_optimisation",
    "health_factor_monitoring",
    "dev_automation",
    "creative_design",
    "marketing_content",
    "data_analytics",
    "security_compliance",
    "admin_ops",
    "other",
)

#: Termix source categories → our taxonomy, per study §4 (design D4).
#: "general" and unknown values are intentionally absent: they fall through
#: to the next priority stage and never raise.
_TERMIX_CATEGORY_MAP: Final[dict[str, str]] = {
    "Code & Smart Contracts": "dev_automation",
    "Data & Research": "data_analytics",
    "Writing & Content": "marketing_content",
    "Design & Brand": "creative_design",
    "Security & Verification": "security_compliance",
    "Market & Protocol Research": "grid_trading",
    "Automation & Ops": "admin_ops",
    "Model & Dataset Ops": "data_analytics",
}

#: Offchain tag substrings → category (study §5 signal lists). Match is
#: case-insensitive; ties resolve by CATEGORIES order (design D5).
_TAG_HINTS: Final[dict[str, tuple[str, ...]]] = {
    "rebalancing": ("rebalanc", "portfolio management"),
    "grid_trading": (
        "grid",
        "arbitrage",
        "dca",
        "scalping",
        "backtest",
        "quant",
        "trading bot",
        "technical analysis",
        "alpha hunter",
        "smart money",
        "ai trading",
    ),
    "yield_optimisation": ("yield", "defi", "farm", "staking", "vault", "lend", "liquidity"),
    "health_factor_monitoring": ("health factor", "liquidation", "collateral", "risk management"),
    "dev_automation": (
        "agent development",
        "automation",
        "orchestration",
        "api",
        "backend",
        "bot",
        "browser",
        "workflow",
        "software",
        "mobile",
        "web",
        "llm",
        "chatbot",
        "function calling",
        "multi-agent",
        "prompt engineering",
        "computer vision",
        "node",
        "rpc",
    ),
    "creative_design": (
        "3d modeling",
        "image",
        "video",
        "graphic design",
        "illustration",
        "logo",
        "nft art",
        "animation",
        "ui/ux",
        "branding",
        "design system",
    ),
    "marketing_content": (
        "ad campaign",
        "kol",
        "influencer",
        "seo",
        "social media",
        "email",
        "blog",
        "copywriting",
        "content",
        "translation",
        "pr",
        "growth",
    ),
    "data_analytics": (
        "data analysis",
        "data labeling",
        "data engineering",
        "data entry",
        "data extraction",
        "data visualization",
        "web research",
        "market research",
        "business analysis",
        "sql",
        "embeddings",
    ),
    "security_compliance": (
        "anti-phishing",
        "wallet security",
        "contract review",
        "smart contract audit",
        "security review",
        "bug bounty",
        "forensics",
        "compliance",
        "due diligence",
        "legal",
    ),
    "admin_ops": (
        "bookkeeping",
        "customer support",
        "virtual assistant",
        "email management",
        "project management",
        "product management",
        "report writing",
    ),
}

#: Substrings inside oasf skills that map to a richer category. Match is
#: case-insensitive. Kept as the stage-4 fallback (design D2).
_SKILL_HINTS: Final[dict[str, tuple[str, ...]]] = {
    "grid_trading": ("grid", "range", "dca"),
    "yield_optimisation": ("yield", "farm", "staking", "vault", "lend"),
    "health_factor_monitoring": ("health", "liquidation", "collateral"),
}

#: Protocol-prefix hints. Kept as the stage-4 fallback (design D2).
_PROTOCOL_HINTS: Final[dict[str, tuple[str, ...]]] = {
    "yield_optimisation": ("pancakeswap", "venus", "beefy", "aave"),
    "grid_trading": ("ascendex", "binance_grid"),
}

#: Free-text description hints (stage 3). Substring, case-insensitive over
#: the lowercased description. Conservative on purpose: generic words
#: ("management", "platform") are avoided so sparse rows fall to `other`.
_DESCRIPTION_HINTS: Final[dict[str, tuple[str, ...]]] = {
    "dev_automation": (
        "deploy",
        "website",
        "web app",
        "code",
        "developer",
        "workflow",
        "pipeline",
        "automation system",
        "api integration",
        "smart contract development",
    ),
    "creative_design": (
        "design",
        "creative",
        "branding",
        "image generation",
        "logo",
        "graphic",
    ),
    "marketing_content": (
        "marketing",
        "content",
        "social media",
        "seo",
        "blog",
        "copywriting",
        "campaign",
    ),
    "data_analytics": (
        "data analytics",
        "data analysis",
        "research",
        "insights",
        "dashboard",
        "market research",
        "reporting",
    ),
    "security_compliance": (
        "security audit",
        "smart contract audit",
        "compliance",
        "vulnerability",
        "penetration",
        "verification",
    ),
    "admin_ops": (
        "admin",
        "operations",
        "scheduling",
        "task management",
        "community management",
    ),
    "grid_trading": ("trading bot", "arbitrage", "grid trading", "dca bot", "scalping"),
    "yield_optimisation": ("yield", "staking", "liquidity farming", "defi vault"),
    "rebalancing": ("rebalanc", "portfolio management"),
    "health_factor_monitoring": ("liquidation", "health factor", "collateral monitoring"),
}


def compute_category(
    termix_category: str | None,
    tags: list[str] | None,
    supported_protocols: list[str] | None,
    x402_supported: bool,
    description: str | None = None,
) -> str:
    """Return the category for a row (5-stage priority chain, design D2).

    Order of precedence:
      1. `termix_category` — source-assigned category mapped 1:1 per study
         §4; unknown values (incl. "general") fall through, never error.
      2. `tags` — offchain tag substrings via `_TAG_HINTS`; the first
         category (in CATEGORIES order) with any matching hint wins.
      3. `description` — free-text hints (deploy/website/code → dev_automation
         etc.); x402 support alone no longer classifies anything, since it is
         a payment rail, not a category (fixes 2,840 x402 agents wrongly
         grouped as rebalancing).
      4. `supported_protocols` — existing skill/protocol hints.
      5. `other` — mirrors the GENERATED default for sparse rows.
    """
    if termix_category:
        mapped = _TERMIX_CATEGORY_MAP.get(termix_category)
        if mapped is not None:
            return mapped

    for cat in CATEGORIES:
        hints = _TAG_HINTS.get(cat)
        if not hints:
            continue
        for tag in tags or []:
            if isinstance(tag, str) and any(hint in tag.lower() for hint in hints):
                return cat

    if description:
        desc = description.lower()
        for cat in CATEGORIES:
            hints = _DESCRIPTION_HINTS.get(cat)
            if not hints:
                continue
            if any(hint in desc for hint in hints):
                return cat

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
