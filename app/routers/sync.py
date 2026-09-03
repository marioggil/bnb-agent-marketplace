"""API-key-protected remote sync endpoints.

POST /api/sync        — start an incremental or full sync run in the
                        background (202) or 409 if one is already running.
GET  /api/sync/status — checkpoint + running state from the singleton
                        `sync_state` row.
POST /api/sync/flagged — mirror the OFAC sanctioned-address lists into
                        `flagged_addresses` (T2, DESIGN.md); runs inline
                        and returns the per-source report, 409 while one
                        is in progress.

All endpoints require the `X-API-Key` header to match `SYNC_API_KEY`
(compared with `hmac.compare_digest`). With `SYNC_API_KEY` unset the
endpoints answer 503 — the API is opt-in, so a missing key fails loudly
instead of being silently open.

The run is dispatched with `asyncio.create_task` and the task reference is
kept module-level so the GC does not drop it mid-run. A module-level lock
(plain `threading.Lock` — loop-agnostic, no `asyncio.Lock` cross-loop
binding issues) gives fast 409s on concurrent triggers; the authoritative
"running" flag is the task's own `done()` state.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import threading
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models.sync_state import SyncState
from app.db.session import get_db
from app.errors import Conflict
from app.services.flagged_sync import refresh_flagged_addresses
from app.services.sync_worker import SyncReport, sync_full, sync_incremental

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sync", tags=["sync"])

#: Guards task creation; the authoritative running flag is `_sync_task.done()`.
_sync_lock = threading.Lock()
#: Reference to the in-flight sync task (module-level so the GC keeps it alive).
_sync_task: asyncio.Task[SyncReport] | None = None


class _SyncRequest(BaseModel):
    mode: Literal["incremental", "full"] = "incremental"


def _consume_task_result(task: asyncio.Task[SyncReport]) -> None:
    """Done-callback: log background run failures so they are not silent."""
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        logger.error("sync run failed: %s", exc)


def require_sync_key(request: Request) -> None:
    """Reject the request unless `X-API-Key` matches `SYNC_API_KEY`.

    503 when the key is not configured at all (endpoints are opt-in, so
    unconfigured must fail loudly, never pass). 401 on missing/mismatched
    key, always comparing (even an empty value) to avoid timing leaks.
    """
    configured = get_settings().sync_api_key
    if not configured:
        raise HTTPException(
            status_code=503,
            detail="sync API not configured (SYNC_API_KEY is empty)",
        )
    provided = request.headers.get("X-API-Key")
    if provided is None:
        raise HTTPException(status_code=401, detail="missing X-API-Key header")
    if not hmac.compare_digest(provided.encode(), configured.encode()):
        raise HTTPException(status_code=401, detail="invalid X-API-Key")


@router.post("", dependencies=[Depends(require_sync_key)], status_code=202)
async def start_sync(payload: _SyncRequest | None = None) -> dict[str, object]:
    """Start a background sync run. Body: `{"mode": "incremental"|"full"}`
    (optional, defaults to `incremental`). 409 while one is already running."""
    global _sync_task

    mode = payload.mode if payload is not None else "incremental"
    if not _sync_lock.acquire(blocking=False):
        raise Conflict("a sync run is already in progress")
    try:
        if _sync_task is not None and not _sync_task.done():
            raise Conflict("a sync run is already in progress")
        if mode == "incremental":
            coro = sync_incremental()
        else:
            coro = sync_full()
        _sync_task = asyncio.create_task(coro)
        _sync_task.add_done_callback(_consume_task_result)
    finally:
        _sync_lock.release()
    return {"status": "started", "mode": mode}


@router.get("/status", dependencies=[Depends(require_sync_key)])
async def sync_status(db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    """Return the sync checkpoint: running flag + `sync_state` singleton."""
    running = _sync_task is not None and not _sync_task.done()
    state = await db.get(SyncState, 1)
    if state is None:
        return {
            "running": running,
            "last_token_id": None,
            "last_sync_at": None,
            "failed_count": 0,
        }
    return {
        "running": running,
        "last_token_id": state.last_token_id,
        "last_sync_at": state.last_sync_at.isoformat() if state.last_sync_at else None,
        "failed_count": len(state.failed_token_ids or []),
    }


#: Guards concurrent `/api/sync/flagged` runs. Mirrors `_sync_lock` but as
#: its own lock so flagged syncs and agent syncs never block each other.
_flag_sync_lock = threading.Lock()


@router.post("/flagged", dependencies=[Depends(require_sync_key)])
async def sync_flagged() -> dict[str, Any]:
    """Mirror the OFAC sanctioned-address lists into `flagged_addresses`.

    Fetches each list in `FLAGGED_SOURCES` (public GitHub raw, no auth),
    normalizes the addresses to lowercase and REPLACES that source's rows —
    idempotent, and addresses removed upstream disappear here too. Runs
    inline and returns the per-source report; a concurrent trigger gets 409
    while one is in progress (same guard shape as POST /api/sync).
    """
    if not _flag_sync_lock.acquire(blocking=False):
        raise Conflict("a flagged-address sync is already in progress")
    try:
        report = await refresh_flagged_addresses()
    finally:
        _flag_sync_lock.release()
    return report.to_dict()


@router.post("/agent/{chain_id}/{token_id}", dependencies=[Depends(require_sync_key)])
async def sync_one_agent(
    chain_id: int,
    token_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Fetch + upsert a single agent from the upstream (BSC only).

    Lets an operator add a specific agent that the incremental sync has not
    reached yet (coverage is a small fraction of the BSC registry). Runs
    inline and mirrors the per-agent path of the two-phase sync: get_agent
    -> row map -> ON CONFLICT upsert -> category enrichment. 404 when the
    upstream has no such agent; 422 when the agent is not on BSC.
    """
    from app.db.models.agent import BSC_CHAIN_ID, BSC_IDENTITY_REGISTRY, AgentCache, build_agent_id
    from app.services.client_8004scan import (
        Client8004Scan,
        UpstreamError,
        UpstreamRateLimit,
        UpstreamUnavailable,
    )
    from app.services.sync_worker import _maybe_enrich_category, _row_from_agent, _upsert_agent

    if chain_id != BSC_CHAIN_ID:
        raise HTTPException(
            status_code=422,
            detail=f"only BSC chain {BSC_CHAIN_ID} is supported",
        )
    try:
        async with Client8004Scan() as client:
            agent = await client.get_agent(chain_id, token_id)
    except UpstreamRateLimit as exc:
        raise HTTPException(status_code=429, detail=f"upstream rate limited: {exc}") from exc
    except (UpstreamUnavailable, UpstreamError) as exc:
        raise HTTPException(status_code=502, detail=f"upstream error: {exc}") from exc
    if agent is None:
        raise HTTPException(status_code=404, detail=f"agent {chain_id}:{token_id} not found upstream")
    if agent.chain_id is not None and int(agent.chain_id) != BSC_CHAIN_ID:
        raise HTTPException(
            status_code=422,
            detail=f"agent {token_id} is on chain {agent.chain_id}, not BSC",
        )

    # Force a consistent agent_id even if the upstream supplies one.
    if not agent.agent_id or not str(agent.agent_id).startswith(f"{BSC_CHAIN_ID}:"):
        agent.agent_id = build_agent_id(BSC_CHAIN_ID, BSC_IDENTITY_REGISTRY, token_id)
    elif ":" not in str(agent.agent_id):
        agent.agent_id = f"{BSC_CHAIN_ID}:{BSC_IDENTITY_REGISTRY}:{agent.agent_id}"

    row = _row_from_agent(agent, category_override="")
    await _upsert_agent(db, row)
    await _maybe_enrich_category(db, agent, row["agent_id"])
    await db.commit()

    from sqlalchemy import select

    # AgentCache's PK is `id`, not `agent_id` — select by the canonical id.
    cached = await db.scalar(
        select(AgentCache).where(AgentCache.agent_id == row["agent_id"])
    )
    return {
        "agent_id": row["agent_id"],
        "name": row["name"],
        "token_id": row["token_id"],
        "chain_id": row["chain_id"],
        "x402_supported": row["x402_supported"],
        "category": cached.category if cached is not None else None,
        "wallet": row["agent_wallet"],
    }
