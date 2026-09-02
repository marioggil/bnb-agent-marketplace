"""A2A probe worker tests (agent-score P1-P6, D1-D3/D10/D11).

Layers:
- Unit (respx-mocked HTTP, no DB): `probe_termix_card` status awareness
  (P3, D2, D11) and the 429 throttle (P4, D10).
- Integration (aiosqlite harness + respx): `_select_probeable` eligibility +
  chunking (P2, P4, D1), `_probe_cycle` history + materialization (P5, P6,
  D3), composite via agent_score (S2, D5).
- Lifespan (TestClient): P1 task creation + cancellation.

Spec: `sdd/agent-score/spec` agent-probes.
Design: D1 (pinned probe URL, JSONB eligibility only), D2 (status-aware,
no raise_for_status collapse), D3 (health_status MERGE), D10 (429 throttle),
D11 (timeouts).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import httpx
from fastapi.testclient import TestClient

TERMIX_CARD = "https://platform-backend.prod.termix.live/api/v1/a2a/agents/{}/card"


# ---------------------------------------------------------------------------
# P3/D2/D11 — probe_termix_card status awareness
# ---------------------------------------------------------------------------


async def test_probe_termix_card_200_responded(respx_mock):
    from app.services.client_termix import probe_termix_card

    respx_mock.get(TERMIX_CARD.format(601)).respond(
        200,
        json={"status": "BOUND", "presence": "online", "skills": ["a", "b"]},
    )
    result = await probe_termix_card(601)
    assert result["responded"] is True
    assert result["http_status"] == 200
    assert result["status"] == "BOUND"
    assert result["presence"] == "online"
    assert result["skills_count"] == 2
    assert result["error"] is None
    assert isinstance(result["latency_ms"], int) and result["latency_ms"] >= 0


async def test_probe_termix_card_401_is_responded_no_collapse(respx_mock):
    # P3 scenario "401 is responded": 401 must NOT be collapsed into a failure.
    from app.services.client_termix import probe_termix_card

    # 401 bodies are usually non-JSON; the probe still counts as responded.
    respx_mock.get(TERMIX_CARD.format(602)).respond(401, text="Unauthorized")
    result = await probe_termix_card(602)
    assert result["responded"] is True
    assert result["http_status"] == 401
    assert result["error"] is None
    assert result["latency_ms"] >= 0


async def test_probe_termix_card_404_not_responded(respx_mock):
    from app.services.client_termix import probe_termix_card

    respx_mock.get(TERMIX_CARD.format(603)).respond(404, text="not found")
    result = await probe_termix_card(603)
    assert result["responded"] is False
    assert result["http_status"] == 404
    assert result["error"] == "http_404"
    assert result["latency_ms"] >= 0


async def test_probe_termix_card_5xx_not_responded(respx_mock):
    from app.services.client_termix import probe_termix_card

    respx_mock.get(TERMIX_CARD.format(604)).respond(503, text="boom")
    result = await probe_termix_card(604)
    assert result["responded"] is False
    assert result["http_status"] == 503
    assert result["error"] == "http_503"


async def test_probe_termix_card_2xx_parse_error_not_responded(respx_mock):
    # P3: a parse error on a 2xx body counts as not responded, classified.
    from app.services.client_termix import probe_termix_card

    respx_mock.get(TERMIX_CARD.format(608)).respond(200, text="<html>not json</html>")
    result = await probe_termix_card(608)
    assert result["responded"] is False
    assert result["http_status"] == 200
    assert result["error"] == "parse_error"


async def test_probe_termix_card_connect_error_not_responded(respx_mock, monkeypatch):
    from app.services import client_termix
    from app.services.client_termix import probe_termix_card

    # The D10 fallback wait (60s) is unit-tested separately; patch it so the
    # retried ConnectError never actually sleeps.
    monkeypatch.setattr(client_termix, "_probe_wait", lambda _state: 0.0)
    respx_mock.get(TERMIX_CARD.format(605)).mock(side_effect=httpx.ConnectError("refused"))
    result = await probe_termix_card(605)
    assert result["responded"] is False
    assert result["http_status"] is None
    assert result["error"] == "connect_error"
    assert result["latency_ms"] >= 0


# ---------------------------------------------------------------------------
# P4/D10 — 429 throttle: 2 attempts max, Retry-After honored, else wait 60s
# ---------------------------------------------------------------------------


async def test_probe_429_retries_then_success(respx_mock):
    from app.services.client_termix import probe_termix_card

    route = respx_mock.get(TERMIX_CARD.format(606)).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}, text="slow down"),
            httpx.Response(200, json={"status": "BOUND", "presence": "online"}),
        ]
    )
    result = await probe_termix_card(606)
    assert route.call_count == 2  # the 429 triggered exactly one retry
    assert result["responded"] is True
    assert result["http_status"] == 200


async def test_probe_429_two_attempts_max_then_not_responded(respx_mock):
    from app.services.client_termix import probe_termix_card

    route = respx_mock.get(TERMIX_CARD.format(607)).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}, text="slow down"),
            httpx.Response(429, headers={"Retry-After": "0"}, text="still slow"),
        ]
    )
    result = await probe_termix_card(607)
    assert route.call_count == 2  # stop_after_attempt(2) caps a 429 storm
    assert result["responded"] is False
    assert result["http_status"] == 429
    assert result["error"] == "http_429"


class _FakeOutcome:
    def __init__(self, exc: BaseException | None) -> None:
        self._exc = exc

    def exception(self) -> BaseException | None:
        return self._exc


class _FakeRetryState:
    def __init__(self, exc: BaseException | None) -> None:
        self.outcome = _FakeOutcome(exc)


def _http_status_error(status: int, headers: dict[str, str] | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", TERMIX_CARD.format(1))
    response = httpx.Response(status, headers=headers or {}, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


def test_probe_wait_honors_retry_after_else_60s():
    from app.services.client_termix import _probe_wait

    # 429 with Retry-After → honored verbatim.
    assert _probe_wait(_FakeRetryState(_http_status_error(429, {"Retry-After": "7"}))) == 7.0
    # 429 without the header → fixed 60s (P4 "back off 60s").
    assert _probe_wait(_FakeRetryState(_http_status_error(429))) == 60.0
    # Non-429 retryable error (e.g. 500) → fixed 60s.
    assert _probe_wait(_FakeRetryState(_http_status_error(500))) == 60.0
    # Network error (no response to read a header from) → fixed 60s.
    assert _probe_wait(_FakeRetryState(httpx.ConnectError("refused"))) == 60.0


# ---------------------------------------------------------------------------
# P2/P4/D1 — _select_probeable: JSONB eligibility flag + 50-agent chunk
# ---------------------------------------------------------------------------


async def _seed_agent(
    db, token_id: int, *, services=None, endpoint_last_checked_at=None, health_status=None
) -> str:
    from datetime import datetime, timezone

    from app.db.models.agent import (
        BSC_CHAIN_ID,
        BSC_IDENTITY_REGISTRY,
        AgentCache,
        build_agent_id,
    )

    aid = build_agent_id(BSC_CHAIN_ID, BSC_IDENTITY_REGISTRY, token_id)
    db.add(
        AgentCache(
            agent_id=aid,
            chain_id=BSC_CHAIN_ID,
            token_id=token_id,
            registry_address=BSC_IDENTITY_REGISTRY,
            name=f"Agent {token_id}",
            services=services if services is not None else {},
            endpoint_last_checked_at=endpoint_last_checked_at,
            health_status=health_status,
            supported_protocols=[],
            cross_chain_versions=[],
            raw={},
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            tags=[],
            categories=[],
        )
    )
    await db.commit()
    return aid


async def test_select_probeable_filters_a2a_endpoint_presence(db):
    from app.services.probe_worker import _select_probeable

    aid_1 = await _seed_agent(db, 1, services={"a2a": {"endpoint": "https://a1.example"}})
    await _seed_agent(db, 2, services={"a2a": {"version": "0.1.0"}})  # no endpoint key
    await _seed_agent(db, 3, services={})  # no a2a surface at all
    await _seed_agent(db, 4, services={"mcp": {"endpoint": "https://m4.example"}})

    rows = await _select_probeable(db, 10)
    assert [(agent_id, token_id) for agent_id, token_id in rows] == [(aid_1, 1)]


async def test_select_probeable_chunks_at_50_unchecked_first(db):
    from datetime import datetime, timedelta, timezone

    from app.services.probe_worker import _select_probeable

    # 120 probeable agents; the last 20 were already checked this cycle.
    for t in range(1, 101):
        await _seed_agent(db, t, services={"a2a": {"endpoint": f"https://a{t}.example"}})
    checked = datetime.now(timezone.utc) - timedelta(minutes=30)
    for t in range(101, 121):
        await _seed_agent(
            db,
            t,
            services={"a2a": {"endpoint": f"https://a{t}.example"}},
            endpoint_last_checked_at=checked,
        )

    rows = await _select_probeable(db, 50)
    # P2 scenario "Chunking bounds a cycle": 120 agents → exactly 50 selected.
    assert len(rows) == 50
    # endpoint_last_checked_at NULLS FIRST → never-checked agents win the chunk.
    assert all(token_id <= 100 for _, token_id in rows)


# ---------------------------------------------------------------------------
# P5/P6/D3/S2 — _probe_cycle: history rows + health materialization + score
# ---------------------------------------------------------------------------


async def test_probe_cycle_appends_history_and_materializes(db, respx_mock):
    from sqlalchemy import select

    from app.db.models.agent import AgentCache
    from app.db.models.agent_probe import AgentProbe
    from app.services.probe_worker import _probe_cycle

    # Seed an upstream-shaped health_status that the MERGE must preserve.
    aid = await _seed_agent(
        db,
        701,
        services={"a2a": {"endpoint": "https://a701.example"}},
        health_status={
            "services": {"a2a": {"endpoint": "https://a701.example"}},
            "overall_status": "unknown",
        },
    )
    respx_mock.get(TERMIX_CARD.format(701)).respond(
        200,
        json={"status": "BOUND", "presence": "online", "skills": ["x", "y"]},
    )

    probed = await _probe_cycle(limit=50)
    assert probed == 1

    # P5 — one agent_probes row appended per probe.
    rows = (await db.execute(select(AgentProbe).where(AgentProbe.agent_id == aid))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.responded is True
    assert row.http_status == 200
    assert row.status == "BOUND"
    assert row.presence == "online"
    assert row.skills_count == 2
    assert row.endpoint == "https://a701.example"
    assert row.error is None
    assert row.latency_ms >= 0

    # P6/D3 — health_status MERGE keeps `services`, refreshes overall_status,
    # adds the probe dict; health_score/health_checked_at set.
    agent = await db.scalar(select(AgentCache).where(AgentCache.agent_id == aid))
    assert agent is not None
    hs = agent.health_status
    assert hs is not None
    assert hs["services"]["a2a"]["endpoint"] == "https://a701.example"
    assert hs["overall_status"] == "healthy"
    assert hs["probe"]["responded"] is True
    assert hs["probe"]["http_status"] == 200
    assert "probed_at" in hs["probe"]
    # Pillar: responded 100, latency≤300→100, online→100, skills 2→20 →
    # 0.5*100+0.3*100+0.1*100+0.1*20 = 92. No events → track 50.
    # Composite = 0.6*92 + 0.4*50 = 75.2 → 75.
    assert agent.health_score == Decimal("92")
    assert agent.health_checked_at is not None
    assert agent.endpoint_last_checked_at is not None
    assert agent.activity_score == Decimal("75")


async def test_probe_cycle_two_cycles_two_distinct_rows(db, respx_mock):
    # P5 scenario "History accumulates": 2 cycles → 2 rows, distinct probed_at.
    from sqlalchemy import select

    from app.db.models.agent_probe import AgentProbe
    from app.services.probe_worker import _probe_cycle

    aid = await _seed_agent(db, 702, services={"a2a": {"endpoint": "https://a702.example"}})
    respx_mock.get(TERMIX_CARD.format(702)).respond(
        200, json={"status": "BOUND", "presence": "online", "skills": []}
    )

    await _probe_cycle(limit=50)
    await _probe_cycle(limit=50)

    rows = (await db.execute(select(AgentProbe).where(AgentProbe.agent_id == aid))).scalars().all()
    assert len(rows) == 2
    assert len({r.probed_at for r in rows}) == 2  # distinct cycle stamps


async def test_probe_cycle_404_materializes_unhealthy(db, respx_mock):
    from sqlalchemy import select

    from app.db.models.agent import AgentCache
    from app.db.models.agent_probe import AgentProbe
    from app.services.probe_worker import _probe_cycle

    aid = await _seed_agent(
        db,
        703,
        services={"a2a": {"endpoint": "https://a703.example"}},
    )
    respx_mock.get(TERMIX_CARD.format(703)).respond(404, text="gone")

    await _probe_cycle(limit=50)

    row = (await db.execute(select(AgentProbe).where(AgentProbe.agent_id == aid))).scalar_one()
    assert row.responded is False
    assert row.http_status == 404
    assert row.error == "http_404"
    agent = await db.scalar(select(AgentCache).where(AgentCache.agent_id == aid))
    assert agent is not None and agent.health_status is not None
    assert agent.health_status["overall_status"] == "unhealthy"
    assert agent.health_status["probe"]["responded"] is False
    assert agent.health_checked_at is not None


async def test_probe_cycle_never_probed_keeps_health_null(db, respx_mock):
    # P6/D3: an agent without a2a endpoint is never selected → health stays NULL.
    from sqlalchemy import select

    from app.db.models.agent import AgentCache
    from app.db.models.agent_probe import AgentProbe
    from app.services.probe_worker import _probe_cycle

    aid = await _seed_agent(db, 704, services={})
    respx_mock.get(TERMIX_CARD.format(704)).respond(200, json={})  # guard: must never fire

    await _probe_cycle(limit=50)

    agent = await db.scalar(select(AgentCache).where(AgentCache.agent_id == aid))
    assert agent is not None
    assert agent.health_status is None
    assert agent.health_score is None
    assert agent.health_checked_at is None
    assert agent.endpoint_last_checked_at is None
    probes = (
        (await db.execute(select(AgentProbe).where(AgentProbe.agent_id == aid))).scalars().all()
    )
    assert probes == []


async def test_probe_cycle_chunks_120_agents_to_50(db, respx_mock):
    # P2 scenario "Chunking bounds a cycle": 120 probeable → exactly 50 probed.
    from sqlalchemy import select

    from app.db.models.agent_probe import AgentProbe
    from app.services.probe_worker import _probe_cycle

    for t in range(801, 921):
        await _seed_agent(db, t, services={"a2a": {"endpoint": f"https://a{t}.example"}})
    for t in range(801, 921):
        respx_mock.get(TERMIX_CARD.format(t)).respond(
            200, json={"status": "BOUND", "presence": "online"}
        )

    probed = await _probe_cycle(limit=50)
    assert probed == 50

    rows = (await db.execute(select(AgentProbe))).scalars().all()
    assert len(rows) == 50
    assert len({r.agent_id for r in rows}) == 50  # 50 distinct agents, no double-probe


# ---------------------------------------------------------------------------
