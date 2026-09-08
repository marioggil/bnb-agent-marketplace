"""Tests for the agent score calculation (anatomy v2 score formula).

Formula:
    base = sum of available components (None excluded)
    + 10 if hires > 10
    + 10 if reviews > 10
    (no cap; can exceed 100)

Components (each 0..100 or None):
  - endpoint_health: profile.health_score
  - endpoint_verification: 100 if is_verified else 0 (or None if no info)
  - metadata_completeness: profile.metadata_completeness
  - wallet_activity: wallet_activity_score
  - activity_score: agent.activity_score
  - score_breakdown: profile.score_dimensions average (or None if empty)
"""
from __future__ import annotations

from app.services.agent_total_score import compute_total_score

# ============================================================================
# Base calculation: sum of components, None excluded
# ============================================================================


def test_zero_when_all_none():
    """All components None -> base=0 (no components to sum)."""
    breakdown = compute_total_score(
        endpoint_health=None,
        endpoint_verification=None,
        metadata_completeness=None,
        wallet_activity=None,
        activity_score=None,
        score_breakdown=None,
        hires=0,
        reviews=0,
    )
    assert breakdown.base == 0
    assert breakdown.bonus_hires == 0
    assert breakdown.bonus_reviews == 0
    assert breakdown.total == 0


def test_sum_of_all_components_when_present():
    """All components present -> base is the sum."""
    breakdown = compute_total_score(
        endpoint_health=80,
        endpoint_verification=100,
        metadata_completeness=90,
        wallet_activity=70,
        activity_score=60,
        score_breakdown=85,
        hires=0,
        reviews=0,
    )
    assert breakdown.base == 80 + 100 + 90 + 70 + 60 + 85


def test_none_components_excluded_from_sum():
    """None components are skipped, not counted as 0."""
    breakdown = compute_total_score(
        endpoint_health=80,
        endpoint_verification=None,  # missing
        metadata_completeness=None,  # missing
        wallet_activity=70,
        activity_score=None,  # missing
        score_breakdown=85,
        hires=0,
        reviews=0,
    )
    assert breakdown.base == 80 + 70 + 85
    # And the breakdown dict reports which components contributed.
    assert "endpoint_health" in breakdown.components
    assert "endpoint_verification" not in breakdown.components
    assert "wallet_activity" in breakdown.components


def test_components_dict_lists_only_provided():
    """The components dict only includes the values that were provided."""
    breakdown = compute_total_score(
        endpoint_health=80,
        endpoint_verification=None,
        metadata_completeness=90,
        wallet_activity=None,
        activity_score=None,
        score_breakdown=None,
        hires=0,
        reviews=0,
    )
    assert set(breakdown.components.keys()) == {"endpoint_health", "metadata_completeness"}


# ============================================================================
# Bonuses
# ============================================================================


def test_no_bonus_when_hires_and_reviews_under_threshold():
    """hires <= 10 AND reviews <= 10 -> no bonuses."""
    breakdown = compute_total_score(
        endpoint_health=50,
        endpoint_verification=100,
        metadata_completeness=90,
        wallet_activity=70,
        activity_score=60,
        score_breakdown=85,
        hires=10,
        reviews=10,
    )
    assert breakdown.bonus_hires == 0
    assert breakdown.bonus_reviews == 0


def test_hires_bonus_when_above_10():
    """hires > 10 -> +10 bonus."""
    breakdown = compute_total_score(
        endpoint_health=50,
        endpoint_verification=100,
        metadata_completeness=90,
        wallet_activity=70,
        activity_score=60,
        score_breakdown=85,
        hires=11,
        reviews=0,
    )
    assert breakdown.bonus_hires == 10


def test_reviews_bonus_when_above_10():
    """reviews > 10 -> +10 bonus."""
    breakdown = compute_total_score(
        endpoint_health=50,
        endpoint_verification=100,
        metadata_completeness=90,
        wallet_activity=70,
        activity_score=60,
        score_breakdown=85,
        hires=0,
        reviews=11,
    )
    assert breakdown.bonus_reviews == 10


def test_both_bonuses_when_both_above_threshold():
    """hires > 10 AND reviews > 10 -> +20 total bonus."""
    breakdown = compute_total_score(
        endpoint_health=50,
        endpoint_verification=100,
        metadata_completeness=90,
        wallet_activity=70,
        activity_score=60,
        score_breakdown=85,
        hires=100,
        reviews=50,
    )
    assert breakdown.bonus_hires == 10
    assert breakdown.bonus_reviews == 10
    assert breakdown.total == breakdown.base + 20


# ============================================================================
# Total: base + bonuses, no cap
# ============================================================================


def test_total_is_base_plus_bonuses():
    breakdown = compute_total_score(
        endpoint_health=100,
        endpoint_verification=100,
        metadata_completeness=100,
        wallet_activity=100,
        activity_score=100,
        score_breakdown=100,
        hires=50,
        reviews=50,
    )
    # Base = 600 (no cap), bonuses = 20, total = 620
    assert breakdown.base == 600
    assert breakdown.total == 620


def test_total_explains_itself():
    """total == base + bonus_hires + bonus_reviews."""
    breakdown = compute_total_score(
        endpoint_health=20,
        endpoint_verification=None,
        metadata_completeness=40,
        wallet_activity=None,
        activity_score=None,
        score_breakdown=None,
        hires=15,
        reviews=2,
    )
    assert breakdown.total == breakdown.base + breakdown.bonus_hires + breakdown.bonus_reviews
    assert breakdown.total == (20 + 40) + 10 + 0


# ============================================================================
# Edge cases
# ============================================================================


def test_hires_exactly_10_no_bonus():
    """hires == 10 -> no bonus (strictly greater than 10)."""
    breakdown = compute_total_score(
        endpoint_health=None,
        endpoint_verification=None,
        metadata_completeness=None,
        wallet_activity=None,
        activity_score=None,
        score_breakdown=None,
        hires=10,
        reviews=0,
    )
    assert breakdown.bonus_hires == 0


def test_negative_inputs_clamped_to_zero():
    """Negative values are clamped to 0 (defensive)."""
    breakdown = compute_total_score(
        endpoint_health=-5,
        endpoint_verification=-10,
        metadata_completeness=-1,
        wallet_activity=0,
        activity_score=0,
        score_breakdown=0,
        hires=0,
        reviews=0,
    )
    assert breakdown.base == 0
    assert breakdown.total == 0
