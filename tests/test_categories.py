"""Unit tests for the category classifier (10-category taxonomy).

Spec: `sdd/doc-refresh/spec` Domain 2 (TAX-1, TAX-2). Priority chain:
1) termix source category (study §4 map) -> 2) offchain tags -> 3) x402
-> 4) skill/protocol hints -> 5) other. Unknown termix values fall
through, never error.
"""
from __future__ import annotations

import pytest

from app.services.categories import CATEGORIES, compute_category


# TAX-1 — exactly the 11 slugs, stable order.
def test_categories_enum_has_eleven_slugs_in_stable_order() -> None:
    assert CATEGORIES == (
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


# TAX-2 S1 — termix wins over conflicting tags and x402.
def test_termix_category_overrides_tags_and_x402() -> None:
    result = compute_category(
        termix_category="Code & Smart Contracts",
        tags=["grid trading"],
        supported_protocols=["Web"],
        x402_supported=True,
    )
    assert result == "dev_automation"


# TAX-2 S2 — unknown termix category falls through to other.
def test_unknown_termix_category_falls_through_to_other() -> None:
    assert compute_category("Mystery", [], [], False) == "other"


# TAX-2 S3 — no termix signal: tags decide.
def test_tags_decide_when_no_termix() -> None:
    assert compute_category(None, ["yield", "staking"], ["Web"], False) == "yield_optimisation"


# x402 maps to rebalancing only when no richer signal matched.
def test_x402_maps_to_rebalancing_without_richer_signals() -> None:
    assert compute_category(None, [], ["Web"], True) == "rebalancing"


# "general" falls through to the next stage (tags), never errors.
def test_general_termix_category_falls_through_to_tags() -> None:
    assert compute_category("general", ["grid"], ["Web"], True) == "grid_trading"


# Protocol hints (stage 4) still map.
def test_protocol_hint_maps_to_grid_trading() -> None:
    assert compute_category(None, [], ["binance_grid"], False) == "grid_trading"


# Skill hints (stage 4) still map.
def test_skill_hint_maps_to_yield_optimisation() -> None:
    assert compute_category(None, [], ["pancakeswap"], False) == "yield_optimisation"


# GENERATED-default mirror: oasf with no richer signal -> rebalancing.
def test_oasf_protocol_mirrors_generated_default() -> None:
    assert compute_category(None, [], ["oasf"], False) == "rebalancing"


# No signals at all -> other.
def test_no_signals_maps_to_other() -> None:
    assert compute_category(None, None, None, False) == "other"


# Determinism: identical inputs produce identical output.
def test_compute_category_is_deterministic() -> None:
    args = ("Market & Protocol Research", ["web research"], ["Web"], False)
    assert compute_category(*args) == compute_category(*args)


# All 8 mapped termix categories per study §4 + "general" falls through.
@pytest.mark.parametrize(
    ("termix_category", "expected"),
    [
        ("Code & Smart Contracts", "dev_automation"),
        ("Data & Research", "data_analytics"),
        ("Writing & Content", "marketing_content"),
        ("Design & Brand", "creative_design"),
        ("Security & Verification", "security_compliance"),
        ("Market & Protocol Research", "grid_trading"),
        ("Automation & Ops", "admin_ops"),
        ("Model & Dataset Ops", "data_analytics"),
        ("general", "other"),
    ],
)
def test_termix_category_map(termix_category: str, expected: str) -> None:
    assert compute_category(termix_category, [], [], False) == expected
