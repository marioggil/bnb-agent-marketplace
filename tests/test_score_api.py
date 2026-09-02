"""Score & compare API tests (agent-score A1-A3, D5 lazy path).

Layers: API integration via TestClient (per conftest) against the
aiosqlite harness. Probe rows are seeded directly into `agent_probes`
(they are what the worker would append); track events go into
`onchain_agent_events`; `agent_cache` carries the materialized score.

Expected pillar math (pinned by test_agent_score.py):
- probe: responded=True(100) ×0.5 + latency 150ms→100 ×0.3 + online→100 ×0.1
  + skills 2→20 ×0.1 = 92
- track (age 6mo→50, events 5→50, buyers 5→50, recency 3d→100) =
  (50+50+50+100)/4 = 62.5 → 62 (round-half-even)
- composite = 0.6×92 + 0.4×62 = 80
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.db.models.agent import (
    BSC_CHAIN_ID,
    BSC_IDENTITY_REGISTRY,
    AgentCache,
    build_agent_id,
)
from app.db.models.agent_probe import AgentProbe
from app.db.models.onchain_index import OnchainAgentEvent
from tests.conftest import _now


async def _seed_agent(
    session,
    token_id: int,
    *,
    name: str | None = None,
    activity_score: Decimal | None = None,
    upstream_created_at: datetime | None = None,
) -> str:
    aid = build_agent_id(BSC_CHAIN_ID, BSC_IDENTITY_REGISTRY, token_id)
    session.add(
        AgentCache(
            agent_id=aid,
            chain_id=BSC_CHAIN_ID,
            token_id=token_id,
            registry_address=BSC_IDENTITY_REGISTRY,
            name=name or f"Agent {token_id}",
            activity_score=activity_score,
            upstream_created_at=upstream_created_at,
            services={},
            supported_protocols=[],
            cross_chain_versions=[],
            raw={},
            created_at=_now(),
            updated_at=_now(),
            tags=[],
            categories=[],
        )
    )
    await session.commit()
    return aid


def _seed_probe(
    session,
    agent_id: str,
    *,
    responded: bool = True,
    latency_ms: int = 150,
    status: str = "BOUND",
    presence: str = "online",
    skills_count: int = 2,
) -> None:
    session.add(
        AgentProbe(
            agent_id=agent_id,
            probed_at=_now(),
            responded=responded,
            http_status=200 if responded else 404,
            latency_ms=latency_ms,
            status=status,
            presence=presence,
            endpoint="https://agent.example/a2a",
            skills_count=skills_count,
            error=None,
        )
    )


def _seed_events(session, agent_id: str, n: int, *, latest_days_ago: int = 3) -> None:
    """`n` 90-day-window events with distinct tx_hash and unique buyers."""
    for i in range(n):
        session.add(
            OnchainAgentEvent(
                agent_id=agent_id,
                token_id=0,
                event_type="transfer",
                from_address="0x" + "1" * 40,
                to_address="0x" + f"{i:040x}",
                block_number=1,
                timestamp=_now() - timedelta(days=latest_days_ago),
                tx_hash="0x" + f"{i:064x}",
            )
        )


# ---------------------------------------------------------------------------
# A1 — GET /api/agents/{chain}/{token}/score
# ---------------------------------------------------------------------------


async def test_score_returns_pillars_and_breakdown(client, db):
    """A1 scenario 'Score schema': cached agent → both pillars + breakdown."""
    aid = await _seed_agent(
        db,
        101,
        name="Alpha",
        activity_score=Decimal("80"),
        upstream_created_at=_now() - timedelta(days=183),  # ~6 months → 50 pts
    )
    _seed_probe(db, aid)
    _seed_events(db, aid, 5)
    await db.commit()

    resp = client.get("/api/agents/56/101/score")
    assert resp.status_code == 200, resp.text
    payload = resp.json()

    assert payload["chain"] == 56
    assert payload["token"] == 101
    assert float(payload["activity_score"]) == 80.0

    probe = payload["pillars"]["probe"]
    assert probe["score"] == 92
    assert probe["responded"] is True
    assert probe["latency_ms"] == 150
    assert probe["status"] == "BOUND"
    assert probe["presence"] == "online"
    assert probe["skills_count"] == 2
    assert probe["probed_at"] is not None

    track = payload["pillars"]["track_record"]
    assert track["score"] == 62
    assert track["age_months"] is not None and abs(track["age_months"] - 6.0) < 0.5
    assert track["event_count"] == 5
    assert track["unique_buyers"] == 5
    assert track["recency_days"] == 3

    breakdown = payload["breakdown"]
    assert {d["dimension"] for d in breakdown} == {
        "responded",
        "latency",
        "presence",
        "skills",
        "age",
        "events",
        "buyers",
        "recency",
    }
    assert {d["weight"] for d in breakdown} == {0.5, 0.3, 0.1, 0.25}


async def test_score_unknown_agent_404(client, db):
    """A1: an agent that is not cached → 404."""
    resp = client.get("/api/agents/56/9999/score")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_score_lazy_recomputes_and_materializes_when_null(client, db):
    """D5 lazy path: activity_score IS NULL → recompute + materialize (A1)."""
    aid = await _seed_agent(
        db,
        102,
        name="Beta",
        activity_score=None,
        upstream_created_at=_now() - timedelta(days=183),
    )
    _seed_probe(db, aid)
    _seed_events(db, aid, 5)
    await db.commit()

    resp = client.get("/api/agents/56/102/score")
    assert resp.status_code == 200, resp.text
    assert float(resp.json()["activity_score"]) == 80.0  # 0.6×92 + 0.4×62

    row = await db.scalar(select(AgentCache).where(AgentCache.agent_id == aid))
    assert row is not None
    assert row.activity_score == Decimal("80")


async def test_score_track_only_when_never_probed(client, db):
    """S3/D8: never-probed agent → probe pillar absent, score = track pillar."""
    aid = await _seed_agent(
        db,
        103,
        name="Gamma",
        activity_score=None,
        upstream_created_at=_now() - timedelta(days=183),
    )
    _seed_events(db, aid, 5)
    await db.commit()

    resp = client.get("/api/agents/56/103/score")
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert float(payload["activity_score"]) == 62.0  # track-only per D8
    assert payload["pillars"]["probe"] is None
    assert {d["dimension"] for d in payload["breakdown"]} == {
        "age",
        "events",
        "buyers",
        "recency",
    }


# ---------------------------------------------------------------------------
# A2 — GET /api/agents/compare?ids=chain/token,chain/token
# ---------------------------------------------------------------------------


async def test_compare_two_valid_ids_returns_agents_with_pillars(client, db):
    """A2 scenario 'Compare happy path': two valid ids → both agents + pillars."""
    aid1 = await _seed_agent(
        db,
        201,
        name="Alpha",
        activity_score=Decimal("80"),
        upstream_created_at=_now() - timedelta(days=183),
    )
    _seed_probe(db, aid1)
    _seed_events(db, aid1, 5)
    await _seed_agent(db, 202, name="Beta", activity_score=Decimal("50"))
    await db.commit()

    resp = client.get("/api/agents/compare", params={"ids": "56/201,56/202"})
    assert resp.status_code == 200, resp.text
    agents = resp.json()["agents"]
    by_token = {a["token"]: a for a in agents}
    assert set(by_token) == {201, 202}

    alpha = by_token[201]
    assert alpha["chain"] == 56
    assert alpha["name"] == "Alpha"
    assert float(alpha["activity_score"]) == 80.0
    assert alpha["pillars"]["probe"]["score"] == 92
    assert alpha["pillars"]["track_record"]["event_count"] == 5

    beta = by_token[202]
    assert beta["name"] == "Beta"
    assert float(beta["activity_score"]) == 50.0
    assert beta["pillars"]["probe"] is None


async def test_compare_malformed_ids_422(client, db):
    """A2 scenario 'Compare validates ids': malformed ids → 422, no query."""
    for bad in ["56:0xabc", "56", "56/1,", ",56/1", "abc/1", "56/1/extra", "56//1"]:
        resp = client.get("/api/agents/compare", params={"ids": bad})
        assert resp.status_code == 422, f"ids={bad!r} -> {resp.status_code}"
        assert resp.json()["error"]["code"] == "validation_error", f"ids={bad!r}"


async def test_compare_skips_uncached_ids(client, db):
    """A2: uncached ids are silently skipped (design open question → SKIP)."""
    await _seed_agent(db, 301, name="Alpha", activity_score=Decimal("80"))
    await db.commit()

    resp = client.get("/api/agents/compare", params={"ids": "56/301,56/999"})
    assert resp.status_code == 200, resp.text
    agents = resp.json()["agents"]
    assert [a["token"] for a in agents] == [301]


# ---------------------------------------------------------------------------
# A3 — list sort=activity_score (desc, nulls last)
# ---------------------------------------------------------------------------


async def test_list_sort_activity_score_desc_nulls_last(client, db):
    """A3 scenario 'Sort by activity_score': desc with NULLs landing last."""
    for token, score in [(401, 90), (402, 10), (403, None), (404, 50)]:
        await _seed_agent(db, token, name=f"Agent {token}", activity_score=score)
    await db.commit()

    payload = client.get("/api/agents?sort=activity_score").json()
    assert [it["name"] for it in payload["items"]] == [
        "Agent 401",
        "Agent 404",
        "Agent 402",
        "Agent 403",
    ]
