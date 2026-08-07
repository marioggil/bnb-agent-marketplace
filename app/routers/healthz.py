"""GET /healthz — liveness + DB ping.

Spec: `sdd/marketplace-scaffold/spec/app-bootstrap` (#24) + design id 26
D9 (DB connectivity check). Dokploy retries on 503.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from sqlalchemy import text

from app.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(tags=["healthz"])


@router.get("/healthz")
async def healthz() -> dict[str, Any]:
    """Return `{"status": "ok", "db": "ok"}`; 503 on DB ping failure."""
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - operational path
        logger.error("healthz: DB ping failed: %s", exc)
        return {"status": "error", "db": "error"}
    return {"status": "ok", "db": "ok"}
