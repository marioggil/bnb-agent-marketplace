"""A2A probe worker — probes Termix cards and materializes health + score.

Runs as a lifespan task (indexer pattern, P1): one cycle per
`probe_interval_min`, selecting up to `probe_chunk_size` probeable agents
(`services->'a2a'->>'endpoint'` present — D1 eligibility flag only) ordered
by `endpoint_last_checked_at NULLS FIRST`, probing the pinned Termix card
URL `client_termix._BASE_URL/{token_id}/card` (never `services.a2a.endpoint`
— SSRF boundary, D1), appending one `agent_probes` row per probe (P5), and
materializing the health columns + composite activity score (P6, S2, D5).

Spec: `sdd/agent-score/spec` agent-probes P1-P6.
Design: D1 (probe source), D3 (health_status MERGE), D9 (history), D10
(429 throttle — lives in client_termix), D11 (timeouts).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models.agent import AgentCache
from app.db.models.agent_probe import AgentProbe
from app.db.session import get_sessionmaker
from app.services import agent_score
from app.services.client_termix import probe_termix_card

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Selection (P2, P4, D1)
# ---------------------------------------------------------------------------


def _a2a_endpoint_expr(column: Any, dialect: str) -> Any:
    """Text-valued JSON path for `services->'a2a'->>'endpoint'` (D1).

    Postgres JSONB needs `->>` (astext); the sqlite test harness uses
    `json_extract`, which returns text directly (mirrors pages.py:238).
    """
    from sqlalchemy import func

    if dialect == "postgresql":
        return column["a2a"]["endpoint"].astext
    return func.json_extract(column, "$.a2a.endpoint")


async def _select_probeable(session: AsyncSession, limit: int) -> list[tuple[str, int]]:
    """Select up to *limit* probeable agents for one cycle (P2, P4, D1).

    Probeable = `services->'a2a'->>'endpoint'` is present. The JSONB value
    is ONLY an eligibility flag — the probe URL itself is always the pinned
    Termix card (SSRF boundary). Ordering: `endpoint_last_checked_at NULLS
    FIRST` so never-probed agents are served before already-checked ones.
    """
    dialect = session.get_bind().dialect.name
    endpoint = _a2a_endpoint_expr(AgentCache.services, dialect)
    rows = await session.execute(
        select(AgentCache.agent_id, AgentCache.token_id)
        .where(endpoint.is_not(None))
        .order_by(AgentCache.endpoint_last_checked_at.nulls_first(), AgentCache.agent_id)
        .limit(limit)
    )
    return [(agent_id, token_id) for agent_id, token_id in rows.all()]


def _declared_a2a_endpoint(services: dict[str, Any] | None) -> str | None:
    """The agent's declared A2A endpoint, recorded for telemetry only (P5).

    Never fetched — the probe URL is always the pinned Termix card (D1).
    """
    if not services:
        return None
    a2a = services.get("a2a")
    if not isinstance(a2a, dict):
        return None
    endpoint = a2a.get("endpoint")
    return endpoint if isinstance(endpoint, str) else None


# ---------------------------------------------------------------------------
# Probe + materialization (P5, P6, D3)
# ---------------------------------------------------------------------------


def _merge_health_status(
    current: dict[str, Any] | None,
    probe: dict[str, Any],
    probed_at: datetime,
) -> dict[str, Any]:
    """MERGE the probe into `health_status` (D3).

    Keeps everything upstream wrote (e.g. the `services` render), refreshes
    `overall_status` (healthy/unhealthy), and adds the `probe` dict. Only
    called for probed agents — never-probed agents keep `health_status`
    NULL.
    """
    merged = dict(current or {})
    merged["overall_status"] = "healthy" if probe["responded"] else "unhealthy"
    merged["probe"] = {
        "responded": probe["responded"],
        "http_status": probe["http_status"],
        "latency_ms": probe["latency_ms"],
        "probed_at": probed_at.isoformat(),
    }
    return merged


async def _probe_agent(
    session: AsyncSession, agent_id: str, token_id: int, probed_at: datetime
) -> None:
    """Probe one agent: append an `agent_probes` row + materialize (P5, P6).

    The composite score (S2/D5 worker path) is computed from the fresh probe
    pillar and the 90-day track record and written to `activity_score`.
    """
    agent = await session.scalar(select(AgentCache).where(AgentCache.agent_id == agent_id))
    if agent is None:
        # The agent vanished between selection and probing; skip it.
        logger.warning("probe: agent %s vanished before probing", agent_id)
        return

    probe = await probe_termix_card(token_id)
    session.add(
        AgentProbe(
            agent_id=agent_id,
            probed_at=probed_at,
            responded=probe["responded"],
            http_status=probe["http_status"],
            latency_ms=probe["latency_ms"],
            status=probe["status"],
            presence=probe["presence"],
            endpoint=_declared_a2a_endpoint(agent.services),
            skills_count=probe["skills_count"],
            error=probe["error"],
        )
    )

    probe_pillar = agent_score.compute_probe_pillar(
        probe["responded"], probe["latency_ms"], probe["presence"], probe["skills_count"]
    )
    assert probe_pillar is not None  # every probe has a bool `responded` → pillar
    await session.execute(
        update(AgentCache)
        .where(AgentCache.agent_id == agent_id)
        .values(
            health_status=_merge_health_status(agent.health_status, probe, probed_at),
            health_score=Decimal(probe_pillar),
            health_checked_at=probed_at,
            endpoint_last_checked_at=probed_at,
        )
    )

    track = await agent_score.fetch_track_record(session, agent_id)
    track_pillar = agent_score.compute_track_record_pillar(
        track.age_months, track.event_count, track.unique_buyers, track.recency_days
    )
    await agent_score.materialize_score(
        session, agent_id, agent_score.composite_score(probe_pillar, track_pillar)
    )


async def _probe_cycle(limit: int | None = None) -> int:
    """Run one probe cycle: select, probe, append, materialize (P2-P6).

    Returns the number of agents probed. Opens its own session (indexer
    pattern) and commits once per cycle. One `probed_at` stamp per cycle —
    two cycles therefore produce rows with distinct `probed_at` (P5
    scenario "History accumulates").
    """
    chunk = limit if limit is not None else get_settings().probe_chunk_size
    session_factory = get_sessionmaker()
    probed_at = datetime.now(timezone.utc)
    async with session_factory() as session:
        rows = await _select_probeable(session, chunk)
        for agent_id, token_id in rows:
            await _probe_agent(session, agent_id, token_id, probed_at)
        await session.commit()
        return len(rows)


async def run_probe_loop() -> None:
    """Probe worker loop (P1, indexer pattern): one cycle per interval."""
    settings = get_settings()
    interval_s = settings.probe_interval_min * 60
    logger.info(
        "probe worker started: chunk=%s interval=%ss", settings.probe_chunk_size, interval_s
    )
    while True:
        try:
            probed = await _probe_cycle()
            logger.info("probe cycle done: %s agents probed", probed)
        except Exception:
            logger.exception("probe cycle failed")
        await asyncio.sleep(interval_s)


__all__ = ["run_probe_loop"]
