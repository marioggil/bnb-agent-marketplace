"""Compliance refresh service (OFAC penalty signal, agent-compliance domain).

Spec: `openspec/changes/compliance-flags/spec.md`. Phase 1a-i ships only the
skeleton: module constants, the `ComplianceRefreshReport` dataclass, and the
pure `compute_penalty` helper. **No DB-touching functions** in this slice —
those arrive in 1a-ii (the orchestrator + admin router).

The helper is the public surface both 1a-ii (writes `compliance_penalty` on
every refresh) and 1b (renders `displayed_activity_score`) call. Keeping it
pure and `Decimal`-typed preserves the boundary against the `Numeric(5, 2)`
column at every call site — see design §5.4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

_PENALTY_PER_FLAG: int = 30
_PENALTY_CAP: Decimal = Decimal("50")
_STALE_THRESHOLD_HOURS: int = 24


@dataclass(slots=True)
class ComplianceRefreshReport:
    """Outcome of one `run_compliance_refresh()` invocation.

    Defined here (1a-i) so 1a-ii can extend the module without re-exporting
    the type — the orchestrator returns this and the admin endpoint serializes
    `as_dict()` straight to JSON.
    """

    sources_total_fetched: int = 0
    sources_total_inserted: int = 0
    agents_matched: int = 0
    agents_upserted: int = 0
    penalty_distribution: dict[str, int] = field(default_factory=dict)
    # ISO-8601 UTC; None when the mirror is empty (last_refreshed_at fallback).
    last_refreshed_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """JSON-friendly shape for the admin endpoint response."""
        return {
            "sources_total_fetched": self.sources_total_fetched,
            "sources_total_inserted": self.sources_total_inserted,
            "agents_matched": self.agents_matched,
            "agents_upserted": self.agents_upserted,
            "penalty_distribution": dict(self.penalty_distribution),
            "last_refreshed_at": self.last_refreshed_at,
        }


def compute_penalty(creator_flagged: bool, owner_flagged: bool) -> Decimal:
    """Pure: 30 points per flag, capped at 50. Returns `Decimal`, never `float`.

    Spec AC-4 / design §5.1 truth table:

        (False, False) -> Decimal("0.00")
        (True,  False) -> Decimal("30.00")
        (False, True ) -> Decimal("30.00")
        (True,  True ) -> Decimal("50.00")  # cap, not 60.00

    `Decimal` matches the `Numeric(5, 2)` column type so the read-path math
    (`displayed_activity_score = max(0, activity_score - compliance_penalty)`)
    never touches IEEE-754 (design §5.4).
    """
    raw = _PENALTY_PER_FLAG * int(creator_flagged) + _PENALTY_PER_FLAG * int(owner_flagged)
    return min(Decimal(raw), _PENALTY_CAP)


__all__ = [
    "ComplianceRefreshReport",
    "compute_penalty",
]
