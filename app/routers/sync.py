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
