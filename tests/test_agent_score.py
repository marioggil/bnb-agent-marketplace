"""Agent activity scoring tests (agent-score S1-S4, D6-D8).

Pure-function tests are DB-free; the sqlite-harness tests for
`fetch_track_record` / `materialize_score` live at the bottom of this file.

Spec: `sdd/agent-score/spec` agent-activity-scoring.
Design: D6 (probe pillar 0.5/0.3/0.1/0.1), D7 (track neutral baselines),
D8 (composite renormalization).
"""

from __future__ import annotations

from app.services.agent_score import (
    build_breakdown,
    composite_score,
    compute_probe_pillar,
    compute_track_record_pillar,
    latency_band_points,
)

# ---------------------------------------------------------------------------
# S4 — latency band mapping
# ---------------------------------------------------------------------------


def test_latency_band_points_boundaries():
    assert latency_band_points(300) == 100
    assert latency_band_points(1000) == 75
    assert latency_band_points(3000) == 50
    assert latency_band_points(10000) == 25
    assert latency_band_points(10001) == 0


def test_latency_band_points_edges_and_none():
    assert latency_band_points(0) == 100
    assert latency_band_points(299) == 100
    assert latency_band_points(301) == 75
    assert latency_band_points(50000) == 0
    assert latency_band_points(None) == 0


# ---------------------------------------------------------------------------
# D6 — probe pillar: 0.5 responded + 0.3 latency + 0.1 presence + 0.1 skills
# ---------------------------------------------------------------------------


def test_probe_pillar_all_max():
    assert compute_probe_pillar(True, 300, "online", 10) == 100


def test_probe_pillar_weights_responded():
    # responded=False drops 0.5*100 = 50 points; everything else stays max.
    assert compute_probe_pillar(False, 300, "online", 10) == 50


def test_probe_pillar_weights_latency_and_presence():
    # latency 300→100 pts, 2000→50 pts (Δ50 × 0.3 = 15); presence offline → 50.
    assert compute_probe_pillar(True, 300, "offline", 0) == 85
    assert compute_probe_pillar(True, 2000, "offline", 0) == 70


def test_probe_pillar_never_probed_is_none():
    assert compute_probe_pillar(None, None, None, None) is None


# ---------------------------------------------------------------------------
# D7 — track pillar neutral baselines
# ---------------------------------------------------------------------------


def test_track_pillar_zero_events_neutral_50():
    assert compute_track_record_pillar(6, 0, 0, None) == 50


def test_track_pillar_all_absent_neutral_50():
    assert compute_track_record_pillar(None, None, None, None) == 50


def test_track_pillar_present_dimensions_renormalize():
    # age 6mo→50, events 10→100, buyers 10→100, recency 30d→100 → mean 87.5.
    assert compute_track_record_pillar(6, 10, 10, 30) == 88


# ---------------------------------------------------------------------------
# S3/D8 — composite renormalization
# ---------------------------------------------------------------------------


def test_composite_both_pillars():
    # 0.60×80 + 0.40×60 = 72.0 (spec scenario "Composite formula").
    assert composite_score(80, 60) == 72


def test_composite_probe_only():
    assert composite_score(80, None) == 80


def test_composite_track_only():
    assert composite_score(None, 60) == 60


def test_composite_neither_neutral_50():
    assert composite_score(None, None) == 50


# ---------------------------------------------------------------------------
# A1/D6/D7 — breakdown weights
# ---------------------------------------------------------------------------


def test_breakdown_probe_weights():
    bd = build_breakdown(responded=True, latency_ms=300, presence="online", skills_count=10)
    dims = {d["dimension"]: d for d in bd}
    assert dims["responded"]["weight"] == 0.5
    assert dims["latency"]["weight"] == 0.3
    assert dims["presence"]["weight"] == 0.1
    assert dims["skills"]["weight"] == 0.1
    assert dims["skills"]["score"] == 100  # min(10,10)*10


def test_breakdown_track_age_six_months_is_50():
    bd = build_breakdown(age_months=6, event_count=10, unique_buyers=10, recency_days=30)
    dims = {d["dimension"]: d for d in bd}
    assert dims["age"]["score"] == 50  # 6mo → neutral 50 (D7)
    assert dims["age"]["weight"] == 0.25
    assert dims["events"]["score"] == 100


def test_breakdown_absent_probe_has_no_probe_dims():
    bd = build_breakdown(event_count=5, unique_buyers=5)
    dims = {d["dimension"] for d in bd}
    assert "responded" not in dims
    assert "latency" not in dims


# ---------------------------------------------------------------------------
# S1/S2 — fetch_track_record + materialize_score (sqlite harness)
# ---------------------------------------------------------------------------


async def _seed_agent(db, token_id: int, *, upstream_created_at=None, activity_score=None) -> str:
    from app.db.models.agent import (
        BSC_CHAIN_ID,
        BSC_IDENTITY_REGISTRY,
        AgentCache,
        build_agent_id,
    )
    from tests.conftest import _now

    aid = build_agent_id(BSC_CHAIN_ID, BSC_IDENTITY_REGISTRY, token_id)
    db.add(
        AgentCache(
            agent_id=aid,
            chain_id=BSC_CHAIN_ID,
            token_id=token_id,
            registry_address=BSC_IDENTITY_REGISTRY,
            name=f"Agent {token_id}",
            upstream_created_at=upstream_created_at,
            activity_score=activity_score,
            supported_protocols=[],
            cross_chain_versions=[],
            raw={},
            created_at=_now(),
            updated_at=_now(),
            tags=[],
            categories=[],
        )
    )
    await db.commit()
    return aid


async def _seed_event(db, aid: str, token_id: int, tx: str, frm: str, to: str, ts) -> None:
    from app.db.models.onchain_index import OnchainAgentEvent

    db.add(
        OnchainAgentEvent(
            agent_id=aid,
            token_id=token_id,
            event_type="transfer",
            from_address=frm,
            to_address=to,
            block_number=1,
            timestamp=ts,
            tx_hash=tx,
        )
    )


# S1 — COUNT(DISTINCT tx_hash) dedupes duplicate txs; >90d events excluded.
async def test_fetch_track_record_dedupe_and_90d_window(db):
    from datetime import timedelta

    from app.services.agent_score import fetch_track_record
    from tests.conftest import _now

    aid = await _seed_agent(db, 501, upstream_created_at=_now() - timedelta(days=180))
    within = _now() - timedelta(days=10)
    old = _now() - timedelta(days=120)
    # Two rows share tx 0xaaa (dup → 1 distinct); 0xbbb is outside the window.
    await _seed_event(db, aid, 501, "0xaaa", "0xA1", "0xB1", within)
    await _seed_event(db, aid, 501, "0xaaa", "0xA2", "0xB1", within)
    await _seed_event(db, aid, 501, "0xbbb", "0xA3", "0xB3", old)
    await db.commit()

    rec = await fetch_track_record(db, aid)
    assert rec.event_count == 1  # duplicate tx_hash counted once
    assert rec.unique_buyers == 1  # only the 0xaaa row within the window


# S1 — unique_buyers FILTERs out rows whose `from` is the zero-address.
async def test_fetch_track_record_buyers_exclude_zero_address(db):
    from datetime import timedelta

    from app.services.agent_score import ZERO_ADDRESS, fetch_track_record
    from tests.conftest import _now

    aid = await _seed_agent(db, 502)
    within = _now() - timedelta(days=2)
    await _seed_event(db, aid, 502, "0x1", ZERO_ADDRESS, "0xB1", within)
    await _seed_event(db, aid, 502, "0x2", "0xA1", "0xB2", within)
    await _seed_event(db, aid, 502, "0x3", "0xA1", "0xB3", within)
    await db.commit()

    rec = await fetch_track_record(db, aid)
    assert rec.event_count == 3  # all three distinct txs count as events
    assert rec.unique_buyers == 2  # 0xB1 excluded: its `from` is zero-address


# S1 — recency_days from MAX(timestamp); age_months from upstream_created_at.
async def test_fetch_track_record_recency_and_age(db):
    from datetime import timedelta

    from app.services.agent_score import fetch_track_record
    from tests.conftest import _now

    aid = await _seed_agent(db, 503, upstream_created_at=_now() - timedelta(days=183))
    await _seed_event(db, aid, 503, "0x1", "0xA1", "0xB1", _now() - timedelta(days=30))
    await _seed_event(db, aid, 503, "0x2", "0xA1", "0xB2", _now())
    await db.commit()

    rec = await fetch_track_record(db, aid)
    assert rec.event_count == 2
    assert rec.recency_days == 0  # latest event is `now`
    assert rec.age_months is not None and 5.5 <= rec.age_months <= 6.5  # ~6 months


# S1 — no events in the window → neutral aggregates; age still from the cache.
async def test_fetch_track_record_no_events(db):
    from datetime import timedelta

    from app.services.agent_score import fetch_track_record
    from tests.conftest import _now

    aid = await _seed_agent(db, 504, upstream_created_at=_now() - timedelta(days=10))
    await db.commit()

    rec = await fetch_track_record(db, aid)
    assert rec.event_count == 0
    assert rec.unique_buyers == 0
    assert rec.recency_days is None
    assert rec.age_months is not None and 0 <= rec.age_months < 1


# S2 — materialize_score writes activity_score into agent_cache.
async def test_materialize_score_writes_activity_score(db):
    from decimal import Decimal

    from sqlalchemy import select

    from app.db.models.agent import AgentCache
    from app.services.agent_score import materialize_score

    aid = await _seed_agent(db, 505)
    await materialize_score(db, aid, 72)
    await db.commit()

    row = await db.scalar(select(AgentCache).where(AgentCache.agent_id == aid))
    assert row is not None
    assert row.activity_score == Decimal("72")
