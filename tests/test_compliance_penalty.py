"""Pure-helper tests for `app.services.compliance_refresh.compute_penalty`.

Spec: `openspec/changes/compliance-flags/spec.md` AC-4, requirement
"Penalty math — pure helper `compute_penalty`", §5.1 truth table of the design.

The helper lives in `app/services/compliance_refresh.py` and is the public
surface both 1a-ii (orchestrator) and 1b (display path) call. It MUST stay
pure (no I/O, no module-level mutation, no logging) and return `Decimal`
to match the `agent_cache.compliance_penalty` `Numeric(5, 2)` column.

Truth table (literal `==` Decimal equality, never `pytest.approx`):

    (False, False) -> Decimal("0.00")
    (True,  False) -> Decimal("30.00")
    (False, True ) -> Decimal("30.00")
    (True,  True ) -> Decimal("50.00")  # cap, NOT 60.00
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.compliance_refresh import (
    ComplianceRefreshReport,
    compute_penalty,
)


# ---------------------------------------------------------------------------
# T3 — Full truth table + edge-case triangulation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("creator_flagged", "owner_flagged", "expected"),
    [
        (False, False, Decimal("0.00")),
        (True, False, Decimal("30.00")),
        (False, True, Decimal("30.00")),
        (True, True, Decimal("50.00")),  # cap, not 60.00
    ],
    ids=["none-none", "creator-only", "owner-only", "both-cap"],
)
def test_compute_penalty_truth_table(
    creator_flagged: bool, owner_flagged: bool, expected: Decimal
) -> None:
    """Truth table — exact `Decimal` equality (never `pytest.approx`)."""
    assert compute_penalty(creator_flagged, owner_flagged) == expected


def test_compute_penalty_cap_negative_assertion() -> None:
    """The cap MUST be 50.00, never 60.00 — negative assertion on the upper bound."""
    assert compute_penalty(True, True) == Decimal("50.00")
    # Negative assertion: 30 * 2 == 60 is a value the helper must never return.
    assert compute_penalty(True, True) != Decimal("60.00")


def test_compute_penalty_deterministic() -> None:
    """The helper is pure: repeated invocations with the same inputs MUST
    return equal `Decimal` values. The follow-up cross-input check ensures
    the equality is not trivially true by constant folding."""
    first = compute_penalty(True, True)
    second = compute_penalty(True, True)
    assert first == second == Decimal("50.00")
    # Different input must produce a different output, proving the
    # equality check is not tautological.
    assert compute_penalty(False, False) != first


def test_compute_penalty_returns_decimal_not_float() -> None:
    """Return type MUST be `Decimal` (not `float`) — keeps the column boundary clean."""
    result = compute_penalty(False, False)
    assert isinstance(result, Decimal)
    # `bool` is a subclass of `int`; `Decimal` is NOT a subclass of `float`, so
    # the second assertion is the load-bearing one.
    assert not isinstance(result, float)


def test_zero_zero_returns_zero() -> None:
    """T1 — minimal sanity assertion (kept for backwards-compat with the RED step)."""
    assert compute_penalty(False, False) == Decimal("0.00")


# ---------------------------------------------------------------------------
# Report dataclass — basic shape (1a-i ships the type so 1a-ii can extend)
# ---------------------------------------------------------------------------


def test_compliance_refresh_report_defaults() -> None:
    """The dataclass MUST have zero-valued defaults and a JSON-friendly `as_dict`."""
    report = ComplianceRefreshReport()
    as_dict = report.as_dict()
    assert as_dict == {
        "sources_total_fetched": 0,
        "sources_total_inserted": 0,
        "agents_matched": 0,
        "agents_upserted": 0,
        "penalty_distribution": {},
        "last_refreshed_at": None,
    }
