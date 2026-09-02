"""FastAPI app factory + lifespan.

Spec: `sdd/marketplace-scaffold/spec/app-bootstrap` (#24) + design id 26
module map. The factory is the single composition root — `uvicorn
app.main:app` and the tests both import `app`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.db.session import AsyncSessionLocal, engine
from app.errors import register_error_handlers
from app.routers import (
    agents,
    auth,
    favorites,
    healthz,
    hires,
    onchain_hires,
    onchain_stats,
    pages,
    payments,
    sync,
)

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """App lifespan: warm the async engine, start indexer, dispose on shutdown."""
    settings = get_settings()
    logger.info("app start: log_level=%s db=%s", settings.log_level, settings.database_url)
    # Touch the engine to fail fast on a bad DATABASE_URL before serving.
    async with AsyncSessionLocal() as session:
        from sqlalchemy import text

        await session.execute(text("SELECT 1"))

    # Start on-chain indexer if Alchemy key is configured
    indexer_task = None
    alchemy_key = getattr(settings, "alchemy_api_key", "")
    print(f"[lifespan] ALCHEMY_API_KEY configured: {'yes' if alchemy_key else 'NO'}", flush=True)
    if alchemy_key:
        from app.services.onchain_indexer import run_indexer_loop

        indexer_task = asyncio.create_task(run_indexer_loop())
        print("[lifespan] On-chain indexer task started", flush=True)
    else:
        print("[lifespan] On-chain indexer DISABLED (no ALCHEMY_API_KEY)", flush=True)

    # Start the A2A probe worker (agent-score P1) — indexer pattern.
    probe_task = None
    print(f"[lifespan] PROBE_ENABLED: {'yes' if settings.probe_enabled else 'NO'}", flush=True)
    if settings.probe_enabled:
        from app.services.probe_worker import run_probe_loop

        probe_task = asyncio.create_task(run_probe_loop())
        print("[lifespan] A2A probe worker task started", flush=True)
    else:
        print("[lifespan] A2A probe worker DISABLED (PROBE_ENABLED=false)", flush=True)

    try:
        yield
    finally:
        if probe_task:
            probe_task.cancel()
        if indexer_task:
            indexer_task.cancel()
        await engine.dispose()


def create_app() -> FastAPI:
    """Build the FastAPI app with all routers, errors, static, and lifespan."""
    app = FastAPI(
        title="bnb_agent marketplace",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )
    # Static assets — base.html pulls /static/css/site.css and /static/js/htmx-2.x.min.js.
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    # Routers.
    app.include_router(healthz.router)
    app.include_router(auth.router)
    app.include_router(agents.router)
    app.include_router(favorites.router)
    app.include_router(hires.router)
    app.include_router(onchain_hires.router)
    app.include_router(onchain_stats.router)
    app.include_router(pages.router)
    app.include_router(payments.router)
    app.include_router(sync.router)
    # Error handlers.
    register_error_handlers(app)
    return app


# uvicorn entrypoint: `uvicorn app.main:app`.
app = create_app()
