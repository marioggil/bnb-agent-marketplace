"""HTMX + Jinja2 page routes (home, agent detail, favorites, auth).

Spec: `sdd/marketplace-scaffold/spec/web-pages` (#22).

Pattern: each route returns either `pages/*.html` (full page) or
`partials/*.html` (HTMX swap fragment) based on the `HX-Request: true`
header. The `TemplateResponse` is reused across both paths.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context

from app.config import get_settings
from app.db.models.agent import AgentCache
from app.db.models.favorite import Favorite
from app.db.models.user import User
from app.db.session import AsyncSessionLocal
from app.errors import AuthRequired, NotFound
from app.services.auth import SESSION_COOKIE_NAME, get_current_user, issue_csrf

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _img_fallback(url: str | None) -> str:
    """Jinja filter — return the placeholder when url is None/empty."""
    return url or "/static/img/placeholder.svg"


def _pagination_window(page: int, total_pages: int, window: int = 2) -> list[int | None]:
    """Return a list of page numbers with None sentinels for gaps.

    `None` entries render as ellipsis in the template.
    """
    if total_pages <= 1:
        return []
    start = max(1, page - window)
    end = min(total_pages, page + window)
    pages: list[int | None] = []
    if start > 1:
        pages.append(1)
        if start > 2:
            pages.append(None)
    pages.extend(range(start, end + 1))
    if end < total_pages:
        if end < total_pages - 1:
            pages.append(None)
        pages.append(total_pages)
    return pages


templates.env.filters["img_fallback"] = _img_fallback
templates.env.globals["pagination_window"] = _pagination_window


# CSRF token is derived from the session cookie. Templates call `{{ csrf_token() }}`
# (see `favorites_card.html`, `agent_detail.html`); we register a contextfunction
# so Jinja passes the render context — which FastAPI populates with `request` —
# letting us read the cookie and return the per-session token. Fix for sdd-verify C1.
@pass_context
def _csrf_token_from_context(context: dict[str, Any]) -> str:
    request = context.get("request")
    cookie = request.cookies.get(SESSION_COOKIE_NAME) if request is not None else None
    return issue_csrf(cookie)


templates.env.globals["csrf_token"] = _csrf_token_from_context


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _list_agents_page(
    page: int, page_size: int, q: str | None = None, category: str | None = None,
    x402: bool | None = None, sort: str = "average_score",
) -> tuple[list[AgentCache], int]:
    """Run the same query as `routers.agents.list_agents` for template rendering."""
    from sqlalchemy import func, or_, select

    sort_key = {
        "average_score": AgentCache.average_score.desc().nullslast(),
        "total_feedbacks": AgentCache.total_feedbacks.desc(),
        "created_at": AgentCache.created_at.desc(),
        "name": AgentCache.name.asc().nullslast(),
    }.get(sort, AgentCache.average_score.desc().nullslast())

    base = select(AgentCache)
    if q:
        like = f"%{q}%"
        base = base.where(or_(AgentCache.name.ilike(like), AgentCache.description.ilike(like)))
    if category:
        base = base.where(AgentCache.category == category)
    if x402 is not None:
        base = base.where(AgentCache.x402_supported.is_(x402))

    total_q = select(func.count()).select_from(base.subquery())
    list_q = base.order_by(sort_key).offset((page - 1) * page_size).limit(page_size)

    async with AsyncSessionLocal() as session:
        total = int(await session.scalar(total_q) or 0)
        rows = (await session.scalars(list_q)).all()
    return list(rows), total


def _render(request: Request, template: str, context: dict[str, Any]) -> HTMLResponse:
    """Wrap `TemplateResponse` so the request stays in `context`."""
    context.setdefault("request", request)
    return templates.TemplateResponse(request, template, context)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


router = APIRouter(tags=["pages"])


@router.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    q: str | None = None,
    category: str | None = None,
    x402: bool | None = None,
    sort: str = "average_score",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=24, ge=1, le=100),
) -> HTMLResponse:
    """Home: full page on first paint, partials on HTMX load-more."""
    items, total = await _list_agents_page(
        page, page_size, q=q, category=category, x402=x402, sort=sort
    )
    total_pages = (total + page_size - 1) // page_size if total else 0
    is_htmx = request.headers.get("HX-Request", "").lower() == "true"
    if is_htmx:
        # HTMX "load more" appends new cards without a wrapper (spec R4, S3).
        return _render(
            request,
            "partials/agent_card_htmx.html",
            {"items": items, "page": page, "page_size": page_size, "sort": sort,
             "q": q, "category": category, "x402": x402, "has_more": page < total_pages},
        )
    return _render(
        request,
        "pages/home.html",
        {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
            "sort": sort,
            "q": q,
            "category": category,
            "x402": x402,
            "has_more": page < total_pages,
        },
    )


@router.get("/agents/{chain_id}/{token_id}", response_class=HTMLResponse)
async def agent_detail(
    request: Request, chain_id: int, token_id: int
) -> HTMLResponse:
    """Single-agent detail page (spec #22 + web-pages-x402 W1).

    FU-2: the hire CTA needs the agent's `pay_to` (payment wallet) and the
    flat price (X402_DEFAULT_PRICE_USD). `csrf_token()` is already a global.
    """
    async with AsyncSessionLocal() as session:
        from sqlalchemy import select

        row = await session.scalar(
            select(AgentCache).where(
                AgentCache.chain_id == chain_id, AgentCache.token_id == token_id
            )
        )
    if row is None:
        raise NotFound(f"agent {chain_id}:{token_id} not cached")
    return _render(
        request,
        "pages/agent_detail.html",
        {
            "agent": row,
            "pay_to": row.agent_wallet,
            "hire_price_usd": get_settings().x402_default_price_usd,
        },
    )


@router.get("/favorites", response_class=HTMLResponse)
async def favorites(request: Request) -> HTMLResponse:
    """Auth-gated favorites page. Redirects to /auth for anonymous callers (spec S6)."""
    from fastapi.responses import RedirectResponse

    from app.errors import AuthRequired
    from app.services.auth import SESSION_COOKIE_NAME, _read_session

    # `get_current_user` raises AuthRequired for anonymous callers; the
    # favorites page wants a clean redirect instead of a 401, so gate on the
    # session cookie directly and only resolve the user when present.
    has_session = _read_session(request.cookies.get(SESSION_COOKIE_NAME)) is not None
    if not has_session:
        if request.headers.get("HX-Request", "").lower() == "true":
            return HTMLResponse(status_code=200, headers={"HX-Redirect": "/auth"})
        return RedirectResponse(url="/auth", status_code=302)
    try:
        user = await get_current_user(request)
    except AuthRequired:
        return RedirectResponse(url="/auth", status_code=302)
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        rows = (
            await session.scalars(
                select(Favorite).where(Favorite.address == user.address)
            )
        ).all()
    return _render(request, "pages/favorites.html", {"favorites": rows, "user": user})


@router.get("/auth", response_class=HTMLResponse)
async def auth_page(request: Request) -> HTMLResponse:
    """Sign-in affordance (spec #20 R1 + spec #22 R2)."""
    return _render(request, "pages/auth.html", {})


__all__ = ["router", "templates"]
