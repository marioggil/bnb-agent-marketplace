"""API tests for the additive wallet-activity ScoreOut fields (wallet-activity).

Spec: `openspec/changes/wallet-activity/spec.md` AC-7.
Design: `openspec/changes/wallet-activity/design.md` §6 (JSON boundary) + §7 (UI).
Predecessor: `tests/test_compliance_api.py` (Phase 1b additive ScoreOut fields).

Pins:
  T13 — `wallet_activity_score` is present, float in [0, 100].
  T14 — additive fields exist on ScoreOut (covered by T13).
  T15 — existing keys byte-identical to frozen `tests/fixtures/score_response_reference.json`.
  T16 — populate the 3 fields in `app/routers/agents.py::get_agent_score`.
  aggregation failure swallows → wallet_activity_score=None, etc.
"""

from __future__ import annotations

import datetime as _dt
import json
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import bindparam, event, select, text
from sqlalchemy.types import DateTime

from app.db.models.agent import (
    BSC_CHAIN_ID,
    BSC_IDENTITY_REGISTRY,
    AgentCache,
    build_agent_id,
)
from app.db.models.agent_probe import AgentProbe
from app.db.models.onchain_index import OnchainAgentEvent
from tests.conftest import _now


# Frozen reference timestamp — matches `tests/fixtures/score_response_reference.json`.
FROZEN_TS: _dt.datetime = _dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc)


def _patch_datetime_now(module, frozen: _dt.datetime) -> None:
    """Replace `datetime.now` on a module with a frozen-time shim.

    Used by T15 to freeze time at the fixture's reference timestamp
    (`2026-01-01T00:00:00+00:00`). Pydantic + json.dumps round-trip is
    order-stable when seed data is deterministic.
    """

    class _FrozenDT(_dt.datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ARG003
            return frozen

    module.datetime = _FrozenDT


# ---------------------------------------------------------------------------
# Helpers — seed Alpha agent (chain 56, token 101) to match the frozen fixture.
# ---------------------------------------------------------------------------


async def _seed_alpha_agent(
    db,
    *,
    activity_score: Decimal = Decimal("80"),
    creator_address: str | None = None,
    owner_address: str | None = None,
) -> str:
    """Seed the canonical Alpha agent from `tests/test_score_api.py::test_score_returns_pillars_and_breakdown`."""
    aid = build_agent_id(BSC_CHAIN_ID, BSC_IDENTITY_REGISTRY, 101)
    db.add(
        AgentCache(
            agent_id=aid,
            chain_id=BSC_CHAIN_ID,
            token_id=101,
            registry_address=BSC_IDENTITY_REGISTRY,
            name="Alpha",
            activity_score=activity_score,
            upstream_created_at=FROZEN_TS - _dt.timedelta(days=183),  # ~6 months
            creator_address=creator_address,
            owner_address=owner_address,
            supported_protocols=[],
            cross_chain_versions=[],
            raw={},
            created_at=FROZEN_TS,
            updated_at=FROZEN_TS,
            tags=[],
            categories=[],
        )
    )
    await db.commit()
    return aid


def _seed_probe_for_alpha(db, agent_id: str) -> None:
    """Seed a probe row matching the fixture's probe pillar values."""
    db.add(
        AgentProbe(
            agent_id=agent_id,
            probed_at=FROZEN_TS,
            responded=True,
            http_status=200,
            latency_ms=150,
            status="BOUND",
            presence="online",
            endpoint="https://agent.example/a2a",
            skills_count=2,
            error=None,
        )
    )


def _seed_events_for_alpha(db, agent_id: str) -> None:
    """Seed 5 90-day-window OnchainAgentEvent rows (matches fixture's event_count=5, recency_days=3)."""
    for i in range(5):
        db.add(
            OnchainAgentEvent(
                agent_id=agent_id,
                token_id=101,
                event_type="transfer",
                from_address="0x" + "1" * 40,
                to_address="0x" + f"{i:040x}",
                block_number=1,
                timestamp=FROZEN_TS - _dt.timedelta(days=3),
                tx_hash="0x" + f"{i:064x}",
            )
        )


# Alias kept for backwards-compatibility with the conftest's _now helper used
# in the seed (the seed uses _now() — which calls datetime.now() — but we want
# the FROZEN_TS for byte-identical comparison).
_seed_now = FROZEN_TS


# ---------------------------------------------------------------------------
# T13 — RED: ScoreOut exposes wallet_activity_score.
# ---------------------------------------------------------------------------


async def test_score_endpoint_includes_wallet_activity_score(client, db) -> None:
    """`GET /api/agents/56/101/score` JSON MUST include `wallet_activity_score`.

    AC-7 load-bearing assertion. Field is a float in [0.0, 100.0] with two-decimal
    precision on the happy path.
    """
    aid = await _seed_alpha_agent(
        db, creator_address="0x" + "ab" * 20, owner_address="0x" + "cd" * 20
    )
    _seed_probe_for_alpha(db, aid)
    _seed_events_for_alpha(db, aid)
    await db.commit()

    resp = client.get("/api/agents/56/101/score")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "wallet_activity_score" in body, (
        "ScoreOut must expose the additive `wallet_activity_score` field. "
        f"Got keys: {sorted(body.keys())}"
    )
    assert isinstance(body["wallet_activity_score"], (int, float))
    assert 0.0 <= body["wallet_activity_score"] <= 100.0, (
        f"score out of range: {body['wallet_activity_score']}"
    )


# ---------------------------------------------------------------------------
# T14 — additive breakdown + creator_is_owner keys present.
# ---------------------------------------------------------------------------


async def test_score_endpoint_includes_wallet_activity_breakdown(client, db) -> None:
    """ScoreOut MUST include `wallet_activity_breakdown` with `creator/owner/track_record` keys."""
    aid = await _seed_alpha_agent(
        db, creator_address="0x" + "ee" * 20, owner_address="0x" + "ff" * 20
    )
    _seed_probe_for_alpha(db, aid)
    _seed_events_for_alpha(db, aid)
    await db.commit()

    body = client.get("/api/agents/56/101/score").json()
    assert "wallet_activity_breakdown" in body
    breakdown = body["wallet_activity_breakdown"]
    assert set(breakdown.keys()) == {"creator", "owner", "track_record"}, (
        f"unexpected breakdown keys: {sorted(breakdown.keys())}"
    )
    for k, v in breakdown.items():
        assert isinstance(v, int), f"{k} should be int, got {type(v).__name__}"
        assert 0 <= v <= 100, f"{k} out of range: {v}"


async def test_score_endpoint_includes_creator_is_owner(client, db) -> None:
    """ScoreOut MUST include `creator_is_owner: bool`."""
    aid = await _seed_alpha_agent(
        db, creator_address="0x" + "ee" * 20, owner_address="0x" + "ff" * 20
    )
    _seed_probe_for_alpha(db, aid)
    _seed_events_for_alpha(db, aid)
    await db.commit()

    body = client.get("/api/agents/56/101/score").json()
    assert "creator_is_owner" in body
    assert body["creator_is_owner"] is False


async def test_score_endpoint_creator_is_owner_true_when_mixed_case_same(client, db) -> None:
    """Mixed-case addresses that match LOWER-equality → `creator_is_owner: true`."""
    aid = await _seed_alpha_agent(
        db, creator_address="0xAbC" + "0" * 37, owner_address="0xabc" + "0" * 37
    )
    _seed_probe_for_alpha(db, aid)
    _seed_events_for_alpha(db, aid)
    await db.commit()

    body = client.get("/api/agents/56/101/score").json()
    assert body["creator_is_owner"] is True


# ---------------------------------------------------------------------------
# T15 — RED: existing keys byte-identical to frozen fixture.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fixture() -> dict:
    fp = Path("tests/fixtures/score_response_reference.json")
    with fp.open() as f:
        return json.load(f)


async def test_score_endpoint_existing_keys_byte_identical_to_fixture(
    client, db, fixture: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-7: existing ScoreOut keys MUST match `score_response_reference.json` exactly.

    Frozen `datetime.now` at the fixture reference time so time-derived fields
    (`probed_at`, `age_months`, `recency_days`) match byte-for-byte.
    """
    # Freeze time on both the route handler side (agents.py / pages.py) and the
    # wallet fetcher side (wallet_activity.py). The route uses `datetime.now`
    # indirectly via the helpers; the wallet fetcher uses it directly.
    from app.db.models import agent as agent_mod
    from app.routers import agents as agents_router
    from app.services import agent_score as agent_score_mod
    from app.services import wallet_activity as wallet_activity_mod

    _patch_datetime_now(agents_router, FROZEN_TS)
    _patch_datetime_now(agent_score_mod, FROZEN_TS)
    _patch_datetime_now(wallet_activity_mod, FROZEN_TS)
    _patch_datetime_now(agent_mod, FROZEN_TS)

    aid = await _seed_alpha_agent(
        db,
        creator_address="0x" + "ab" * 20,
        owner_address="0x" + "cd" * 20,
    )
    _seed_probe_for_alpha(db, aid)
    _seed_events_for_alpha(db, aid)
    await db.commit()

    resp = client.get("/api/agents/56/101/score")
    assert resp.status_code == 200, resp.text
    actual = resp.json()

    # Existing keys: the fixture has all of these and the response must match exactly.
    existing_keys = (
        "chain",
        "token",
        "activity_score",
        "compliance_penalty",
        "displayed_activity_score",
        "pillars",
        "breakdown",
    )
    for key in existing_keys:
        assert key in actual, f"existing key missing: {key!r}"
        assert actual[key] == fixture[key], (
            f"key {key!r} drifted from frozen fixture.\n"
            f"  expected: {fixture[key]!r}\n"
            f"  got:      {actual[key]!r}"
        )

    # JSON key order is also part of the contract (Pydantic field-order preserved).
    actual_key_order = [k for k in actual.keys() if k in existing_keys]
    fixture_key_order = [k for k in fixture.keys() if k in existing_keys]
    assert actual_key_order == fixture_key_order, (
        f"key order drifted: actual={actual_key_order}, fixture={fixture_key_order}"
    )


# ---------------------------------------------------------------------------
# Aggregation failure swallows → wallet_activity_score=None, etc.
# ---------------------------------------------------------------------------


async def test_score_endpoint_aggregation_failure_swallowed(client, db) -> None:
    """`fetch_wallet_signals` raising MUST NOT break the response.

    Existing keys remain populated; new keys are None / False.
    """
    aid = await _seed_alpha_agent(
        db, creator_address="0x" + "ab" * 20, owner_address="0x" + "cd" * 20
    )
    _seed_probe_for_alpha(db, aid)
    _seed_events_for_alpha(db, aid)
    await db.commit()

    from app.routers import agents as agents_router
    from app.services import wallet_activity as wallet_activity_mod

    async def _boom(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("simulated DB outage")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(wallet_activity_mod, "fetch_wallet_signals", _boom)
    try:
        resp = client.get("/api/agents/56/101/score")
    finally:
        monkeypatch.undo()

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["wallet_activity_score"] is None
    assert body["wallet_activity_breakdown"] is None
    assert body["creator_is_owner"] is False
    # Existing fields unchanged.
    assert body["chain"] == 56
    assert body["token"] == 101
    assert float(body["activity_score"]) == 80.0
