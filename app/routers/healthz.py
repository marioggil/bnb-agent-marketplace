"""GET /healthz — liveness + DB ping.

Spec: `sdd/marketplace-scaffold/spec/app-bootstrap` (#24) + design id 26
D9 (DB connectivity check). Returns 200 on success and **503 on DB ping
failure** so the container's HEALTHCHECK (`curl -fsS`) goes red and
Dokploy / Docker can take action (restart, remove from rotation).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(tags=["healthz"])


@router.get("/healthz")
async def healthz() -> Any:
    """Return `{"status": "ok", "db": "ok"}`; **503** on DB ping failure."""
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - operational path
        logger.error("healthz: DB ping failed: %s", exc)
        # 503 is what `curl -fsS` in the container's HEALTHCHECK treats as
        # unhealthy. Returning 200 here would keep the container marked
        # healthy even with the DB down, and Dokploy would never restart it.
        return JSONResponse(
            status_code=503,
            content={"status": "error", "db": "error"},
        )
    return {"status": "ok", "db": "ok"}
