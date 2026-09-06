"""Admin endpoints for the OFAC compliance pipeline (Phase 1a-ii).

Spec: `openspec/changes/compliance-flags/spec.md` AC-10 + design §3.2. Two
endpoints, both behind the same `X-API-Key` auth as `POST /api/sync/flagged`
(reused via `require_sync_key` from `app.routers.sync`):

- `POST /api/admin/compliance/refresh` → `run_compliance_refresh()` JSON.
- `GET  /api/admin/compliance/status`  → `{last_refreshed_at, flagged_data_stale, row_count}`.

The router is mounted in `app/main.py::create_app()` with the prefix
`/api/admin/compliance` (the FastAPI `APIRouter(prefix=...)` below). The
mount happens immediately after the `sync.router` mount — see design §3.3.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.db.session import AsyncSessionLocal
from app.routers.sync import require_sync_key
from app.services import compliance_refresh

router = APIRouter(prefix="/api/admin/compliance", tags=["admin-compliance"])


@router.post("/refresh", dependencies=[Depends(require_sync_key)])
async def refresh_compliance() -> dict[str, object]:
    """Run the chained mirror + agent-flag refresh and return the report.

    Opens its own session (per design §3.2) — the endpoint is the single
    operator trigger; the orchestrator handles phase 1 (mirror) and phase 2
    (agent flags) back-to-back. Phase-1 failures short-circuit (no swallowed
    exception) and the original error propagates as 500.
    """
    async with AsyncSessionLocal() as session:
        report = await compliance_refresh.run_compliance_refresh(session)
    return report.as_dict()


@router.get("/status", dependencies=[Depends(require_sync_key)])
async def compliance_status() -> dict[str, object]:
    """Return mirror freshness + row count for the operator UI.

    Body shape (spec AC-10):
        {
          "last_refreshed_at": "2026-01-15T10:30:00+00:00" | null,
          "flagged_data_stale": bool,
          "row_count": int
        }
    """
    async with AsyncSessionLocal() as session:
        last_refreshed_at, row_count = await compliance_refresh.status_summary(session)
        stale = await compliance_refresh.flagged_data_stale(session)
    return {
        "last_refreshed_at": last_refreshed_at.isoformat() if last_refreshed_at else None,
        "flagged_data_stale": stale,
        "row_count": row_count,
    }


__all__ = ["router"]
