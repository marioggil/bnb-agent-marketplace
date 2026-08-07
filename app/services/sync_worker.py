"""Idempotent sync worker for AgentCache.

Walks BSC `token_id` and upserts into `agent_cache` via
`ON CONFLICT (agent_id) DO UPDATE`. Checkpoints progress in the singleton
`sync_state` row so a kill mid-walk resumes cleanly (spec #23 S7).

See `sdd/marketplace-scaffold/spec/sync-worker` (#23) for the requirements
and scenarios. Design decisions D3 (generated category + post-pass) and D7
(FIFO cap 1000 for failed_token_ids) are enforced here.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.agent import (
    AgentCache,
    BSC_CHAIN_ID,
    BSC_IDENTITY_REGISTRY,
    build_agent_id,
)
from app.db.models.sync_state import FAILED_TOKEN_IDS_CAP, SyncState
from app.services.categories import canonical_agent_id, compute_category
from app.services.client_8004scan import (
    Client8004Scan,
    UpstreamError,
    UpstreamUnavailable,
)

logger = logging.getLogger(__name__)

#: Default batch size for incremental runs (spec R1).
DEFAULT_INCREMENTAL_BATCH: int = 100
#: Default batch size for full runs. Larger because we expect a longer,
#: lower-priority window (Dokploy cron Sunday 03:00 UTC).
DEFAULT_FULL_BATCH: int = 200
#: How often to log a progress line (records walked).
PROGRESS_EVERY: int = 100


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SyncReport:
    """Outcome of one sync run."""

    walked: int
    upserted: int
    skipped: int
    failed: int
    last_token_id: int
    duration_s: float

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return (
            f"SyncReport(walked={self.walked} upserted={self.upserted} "
            f"skipped={self.skipped} failed={self.failed} "
            f"last_token_id={self.last_token_id} duration_s={self.duration_s:.2f})"
        )


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------


async def _ensure_sync_state(session: AsyncSession) -> SyncState:
    """Return the singleton row, creating it if absent."""
    state = await session.get(SyncState, 1)
    if state is None:
        state = SyncState(id=1, last_token_id=-1)  # type: ignore[call-arg]
        session.add(state)
        await session.flush()
    return state


async def _record_failure(
    session: AsyncSession, state: SyncState, token_id: int
) -> None:
    """Append a token_id to the jsonb failure list, capped at FAILED_TOKEN_IDS_CAP (D7)."""
    state.failed_token_ids = await session.scalar(
        text(
            "SELECT jsonb_build_array(:token_id) || COALESCE(sync_state.failed_token_ids, '[]'::jsonb) "
            "FROM sync_state WHERE id = 1"
        ).bindparams(token_id=token_id)
    ) or [token_id]
    # Trim FIFO in Python — `len(...) > cap` drops the oldest entries.
    if len(state.failed_token_ids) > FAILED_TOKEN_IDS_CAP:
        state.failed_token_ids = state.failed_token_ids[-FAILED_TOKEN_IDS_CAP:]


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------


def _row_from_agent(agent: Any, category_override: str) -> dict[str, Any]:
    """Map an `AgentResponse` to a dict compatible with the agent_cache columns."""
    return {
        "agent_id": agent.agent_id or canonical_agent_id(agent.chain_id or BSC_CHAIN_ID, agent.token_id or 0),
        "chain_id": int(agent.chain_id) if agent.chain_id is not None else BSC_CHAIN_ID,
        "token_id": int(agent.token_id) if agent.token_id is not None else 0,
        "registry_address": BSC_IDENTITY_REGISTRY,
        "owner_address": agent.owner,
        "name": agent.name,
        "description": agent.description,
        "image_url": agent.image_url or agent.image,
        "x402_supported": bool(agent.x402_supported),
        "supported_protocols": list(agent.supported_protocols or []),
        # `category` is GENERATED. The post-pass UPDATE below overwrites it
        # only when the rich mapping differs from the default.
        "average_score": agent.average_score,
        "total_feedbacks": int(agent.total_feedbacks or 0),
        "is_verified": bool(agent.is_verified),
        "cross_chain_versions": list(agent.cross_chain_versions or []),
        "raw": dict(agent.raw or {}),
    }


async def _upsert_agent(
    session: AsyncSession, row: dict[str, Any]
) -> None:
    """Idempotent ON CONFLICT upsert keyed on agent_id."""
    stmt = pg_insert(AgentCache).values(**row)
    excluded = stmt.excluded
    stmt = stmt.on_conflict_do_update(
        index_elements=[AgentCache.agent_id],
        set_={
            "chain_id": excluded.chain_id,
            "token_id": excluded.token_id,
            "registry_address": excluded.registry_address,
            "owner_address": excluded.owner_address,
            "name": excluded.name,
            "description": excluded.description,
            "image_url": excluded.image_url,
            "x402_supported": excluded.x402_supported,
            "supported_protocols": excluded.supported_protocols,
            "average_score": excluded.average_score,
            "total_feedbacks": excluded.total_feedbacks,
            "is_verified": excluded.is_verified,
            "cross_chain_versions": excluded.cross_chain_versions,
            "raw": excluded.raw,
            "updated_at": text("now()"),
        },
    )
    await session.execute(stmt)


async def _maybe_enrich_category(
    session: AsyncSession, agent: Any, agent_id: str
) -> None:
    """If the rich mapping differs from the GENERATED default, UPDATE the row.

    Spec: the worker post-pass refines `category` for rows with oasf skills
    or sub-protocols (design D3).
    """
    rich = compute_category(agent.supported_protocols or [], agent.x402_supported)
    # If the rich mapping matches the GENERATED default, skip the UPDATE.
    if rich in {"rebalancing", "other"} and (
        agent.x402_supported or "oasf" in (agent.supported_protocols or [])
    ):
        return
    if rich in {"rebalancing", "other"}:
        return
    await session.execute(
        text("UPDATE agent_cache SET category = :cat WHERE agent_id = :aid"),
        {"cat": rich, "aid": agent_id},
    )


# ---------------------------------------------------------------------------
# Walk
# ---------------------------------------------------------------------------


async def _walk(
    session: AsyncSession,
    client: Client8004Scan,
    start: int,
    end: int,
) -> tuple[int, int, int, int, int]:
    """Walk token_ids in [start, end). Returns (last_seen, upserted, skipped, failed, last_token_id)."""
    upserted = 0
    skipped = 0
    failed = 0
    last_seen = start - 1

    for token_id in range(start, end):
        last_seen = token_id
        try:
            agent = await client.get_agent(BSC_CHAIN_ID, token_id)
        except UpstreamUnavailable as exc:
            logger.error("Upstream unavailable at token_id=%s: %s", token_id, exc)
            failed += 1
            await _record_failure(session, await _ensure_sync_state(session), token_id)
            continue
        except UpstreamError as exc:
            logger.warning("Upstream error at token_id=%s: %s", token_id, exc)
            failed += 1
            await _record_failure(session, await _ensure_sync_state(session), token_id)
            continue
        if agent is None:
            # 404 — gap, skip but record.
            skipped += 1
            await _record_failure(session, await _ensure_sync_state(session), token_id)
            continue
        # Chain filter (defense in depth — client already filters).
        if agent.chain_id is not None and int(agent.chain_id) != BSC_CHAIN_ID:
            skipped += 1
            continue
        # Force a consistent agent_id even if the upstream supplies one.
        if not agent.agent_id or not str(agent.agent_id).startswith(f"{BSC_CHAIN_ID}:"):
            agent.agent_id = build_agent_id(BSC_CHAIN_ID, BSC_IDENTITY_REGISTRY, token_id)
        elif ":" not in str(agent.agent_id):
            agent.agent_id = f"{BSC_CHAIN_ID}:{BSC_IDENTITY_REGISTRY}:{agent.agent_id}"

        row = _row_from_agent(agent, category_override="")
        await _upsert_agent(session, row)
        await _maybe_enrich_category(session, agent, row["agent_id"])
        upserted += 1

        if token_id % PROGRESS_EVERY == 0:
            await session.commit()
            logger.info("sync: walked=%s upserted=%s skipped=%s failed=%s", token_id, upserted, skipped, failed)

    await session.commit()
    return last_seen, upserted, skipped, failed, end - 1


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


async def sync_incremental(batch: int = DEFAULT_INCREMENTAL_BATCH) -> SyncReport:
    """Resume from `last_token_id + 1` and walk `batch` more records."""
    return await _run_sync(start_offset=1, batch=batch, label="incremental")


async def sync_full(batch: int = DEFAULT_FULL_BATCH) -> SyncReport:
    """Re-walk from token_id 0. Idempotent via ON CONFLICT."""
    return await _run_sync(start_offset=0, batch=batch, label="full")


async def _run_sync(start_offset: int, batch: int, label: str) -> SyncReport:
    from app.db.session import AsyncSessionLocal

    started = time.monotonic()
    logger.info("sync %s: start_offset=%s batch=%s", label, start_offset, batch)

    async with AsyncSessionLocal() as session, Client8004Scan() as client:
        state = await _ensure_sync_state(session)
        if start_offset == 0:
            start = 0
        else:
            start = int(state.last_token_id) + 1
        end = start + batch

        last_seen, upserted, skipped, failed, last_id = await _walk(
            session, client, start, end
        )

        # Update checkpoint + last_sync_at.
        state = await _ensure_sync_state(session)
        state.last_token_id = last_id
        state.last_sync_at = datetime.now(tz=timezone.utc)
        await session.commit()

    duration = time.monotonic() - started
    report = SyncReport(
        walked=max(0, last_seen - start + 1),
        upserted=upserted,
        skipped=skipped,
        failed=failed,
        last_token_id=last_id,
        duration_s=duration,
    )
    logger.info("sync %s: %s", label, report)
    return report


# Re-export for the CLI.
__all__ = [
    "Client8004Scan",
    "SyncReport",
    "sync_full",
    "sync_incremental",
]
