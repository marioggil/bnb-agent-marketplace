"""FastAPI app factory + lifespan.

Spec: `sdd/marketplace-scaffold/spec/app-bootstrap` (#24) + design id 26
module map. The factory is the single composition root — `uvicorn
app.main:app` and the tests both import `app`.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.db.session import AsyncSessionLocal, engine
from app.errors import register_error_handlers
from app.routers import agents, auth, favorites, healthz, hires, pages, payments, sync

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """App lifespan: warm the async engine, dispose on shutdown.

    The factory does NOT run migrations on startup — that's
    `migrations/env.py` plus `alembic upgrade head`, which the compose
    `app` service runs in its command before uvicorn. Keeping the
    factory free of migration side effects means the tests don't need
    a Postgres instance just to build the app object.
    """
    settings = get_settings()
    logger.info("app start: log_level=%s db=%s", settings.log_level, settings.database_url)
    # Touch the engine to fail fast on a bad DATABASE_URL before serving.
    async with AsyncSessionLocal() as session:
        from sqlalchemy import text

        await session.execute(text("SELECT 1"))
    try:
        yield
    finally:
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
    app.include_router(pages.router)
    app.include_router(payments.router)
    app.include_router(sync.router)
    # Error handlers.
    register_error_handlers(app)
    return app


# uvicorn entrypoint: `uvicorn app.main:app`.
app = create_app()
