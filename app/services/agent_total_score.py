"""Total score for the agent detail page (anatomy v2 score formula).

Formula (simple sum + binary bonuses, no cap):
    base = sum of available components (None excluded)
    + 10 if hires > 10
    + 10 if reviews > 10

The base sum can exceed 100 because we sum components that are each 0..100
instead of averaging them. This makes the score reflect "how many good
signals exist" rather than "how good is the average signal" — appropriate
for a marketplace summary, not for ranking.

Components (each 0..100 or None):
  - endpoint_health         : profile.health_score (0..100)
  - endpoint_verification   : 100 if verified, 0 if not (None if unknown)
  - metadata_completeness   : profile.metadata_completeness (0..100)
  - wallet_activity         : wallet_activity_score (0..100)
  - activity_score          : agent.activity_score (0..100)
  - score_breakdown         : avg of profile.score_dimensions[*].score (or None)

Pure function. DB-free. Called from the agent_detail page renderer to
populate the at-a-glance total score.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ScoreBreakdown:
    """Transparent breakdown of the total score.

    `components` lists only the inputs that were provided (None excluded)
    so the UI can show what contributed and what was missing.
    """

    base: int
    bonus_hires: int
    bonus_reviews: int
    total: int
    components: dict[str, int] = field(default_factory=dict)

    @property
    def displayed_components(self) -> list[tuple[str, int]]:
        """Components ordered by name (stable for tooltip rendering)."""
        return sorted(self.components.items())


# Threshold for the binary bonuses: strictly greater than this triggers +10.
_BONUS_THRESHOLD: int = 10
_BONUS_AMOUNT: int = 10


def _clamp_non_negative(value: int | None) -> int:
    """Coerce None to 0; clamp negatives to 0 (defensive)."""
    if value is None or value < 0:
        return 0
    return value


def compute_total_score(
    *,
    endpoint_health: int | None,
    endpoint_verification: int | None,
    metadata_completeness: int | None,
    wallet_activity: int | None,
    activity_score: int | None,
    score_breakdown: int | None,
    hires: int,
    reviews: int,
) -> ScoreBreakdown:
    """Compute the total score displayed in the agent detail Metrics section.

    See module docstring for the formula.
    """
    components: dict[str, int] = {}
    for name, raw in (
        ("endpoint_health", endpoint_health),
        ("endpoint_verification", endpoint_verification),
        ("metadata_completeness", metadata_completeness),
        ("wallet_activity", wallet_activity),
        ("activity_score", activity_score),
        ("score_breakdown", score_breakdown),
    ):
        if raw is not None:
            components[name] = _clamp_non_negative(raw)

    base = sum(components.values())
    bonus_hires = _BONUS_AMOUNT if hires > _BONUS_THRESHOLD else 0
    bonus_reviews = _BONUS_AMOUNT if reviews > _BONUS_THRESHOLD else 0
    total = base + bonus_hires + bonus_reviews

    return ScoreBreakdown(
        base=base,
        bonus_hires=bonus_hires,
        bonus_reviews=bonus_reviews,
        total=total,
        components=components,
    )


__all__ = ["ScoreBreakdown", "compute_total_score"]
