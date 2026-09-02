"""HTMX + Jinja2 page routes (home, agent detail, favorites, auth).

Spec: `sdd/marketplace-scaffold/spec/web-pages` (#22).

Pattern: each route returns either `pages/*.html` (full page) or
`partials/*.html` (HTMX swap fragment) based on the `HX-Request: true`
header. The `TemplateResponse` is reused across both paths.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context

from app.config import get_settings
from app.db.models.agent import AgentCache
from app.db.models.favorite import Favorite
from app.db.session import AsyncSessionLocal
from app.errors import AuthRequired, NotFound
from app.services.auth import (
    SESSION_COOKIE_NAME,
    _read_session,
    get_current_user,
    issue_csrf,
)
from app.services.categories import CATEGORIES

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


#: Display names for the filter/card category values (DESIGN.md terminology;
#: taxonomy accepted from docs/category-study.md §5, sdd/doc-refresh TAX-5).
_CATEGORY_LABELS: dict[str, str] = {
    "rebalancing": "Rebalancing",
    "grid_trading": "Grid Trading",
    "yield_optimisation": "Yield Optimization",
    "health_factor_monitoring": "Health Factor Monitoring",
    "dev_automation": "Dev & Automation",
    "creative_design": "Creative & Design",
    "marketing_content": "Marketing & Content",
    "data_analytics": "Data & Analytics",
    "security_compliance": "Security & Compliance",
    "admin_ops": "Admin & Ops",
    "other": "Other",
}


def _category_label(value: str | None) -> str:
    """Jinja filter — display name for a category value (unknown → title case)."""
    if not value:
        return "Other"
    return _CATEGORY_LABELS.get(value, value.replace("_", " ").title())


templates.env.filters["category_label"] = _category_label

# Single source of truth for the filter options (design D6): the select in
# filter_form.html iterates this instead of hardcoding slugs.
templates.env.globals["category_options"] = CATEGORIES


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


@pass_context
def _current_user_address(context: dict[str, Any]) -> str | None:
    """Truncated session address for the navbar (0x1234…abcd) or None.

    Reads the signed session cookie only — no DB query (DESIGN.md header:
    logged-in state replaces "Sign in" with the truncated address).
    """
    request = context.get("request")
    cookie = request.cookies.get(SESSION_COOKIE_NAME) if request is not None else None
    address = _read_session(cookie)
    if address is None:
        return None
    return f"{address[:6]}…{address[-4:]}"


templates.env.globals["current_user_address"] = _current_user_address


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _list_agents_page(
    page: int,
    page_size: int,
    q: str | None = None,
    category: str | None = None,
    x402: bool | None = None,
    sort: str = "average_score",
    owner: str | None = None,
    hireable: bool | None = None,
    health: str | None = None,
    platform: str | None = None,
) -> tuple[list[AgentCache], int]:
    """Run the same query as `routers.agents.list_agents` for template rendering."""
    from sqlalchemy import ColumnElement, func, or_, select

    async with AsyncSessionLocal() as session:
        dialect = session.bind.dialect.name if session.bind is not None else "postgresql"

        _sort_map: dict[str, ColumnElement[Any]] = {
            "average_score": AgentCache.average_score.desc().nullslast(),
            "total_feedbacks": AgentCache.total_feedbacks.desc(),
            "created_at": AgentCache.created_at.desc(),
            "name": AgentCache.name.asc().nullslast(),
            "metadata_completeness": AgentCache.metadata_completeness_score.desc().nullslast(),
            "health_score": AgentCache.health_score.desc().nullslast(),
            "activity_score": AgentCache.activity_score.desc().nullslast(),  # A3
        }
        sort_key = _sort_map.get(sort, AgentCache.average_score.desc().nullslast())

        base = select(AgentCache)
        if q:
            like = f"%{q}%"
            base = base.where(or_(AgentCache.name.ilike(like), AgentCache.description.ilike(like)))
        if category:
            base = base.where(AgentCache.category == category)
        if owner:
            base = base.where(AgentCache.owner_address == owner)
        if x402 is not None:
            base = base.where(AgentCache.x402_supported.is_(x402))
        if hireable is not None:
            # The product's hire signal is the x402 payment flag (category study §8).
            base = base.where(AgentCache.x402_supported.is_(hireable))
        if health:
            # overall_status lives inside the health_status JSONB map.
            status = _json_text(AgentCache.health_status, ["overall_status"], dialect)
            if health == "not_measured":
                base = base.where(status.is_(None))
            else:
                base = base.where(status == health)
        if platform:
            base = base.where(_platform_expression(platform, dialect))

        total_q = select(func.count()).select_from(base.subquery())
        list_q = base.order_by(sort_key).offset((page - 1) * page_size).limit(page_size)

        total = int(await session.scalar(total_q) or 0)
        rows = (await session.scalars(list_q)).all()
    return list(rows), total


async def _hires_count(agent_ids: list[str]) -> dict[str, int]:
    """'Hired by N' counts — distinct addresses per agent with a paid hire.

    One query for the whole page (T1 trust signal, DESIGN.md); keys are
    canonical `agent_id`s so templates do `hires.get(agent.agent_id)`.
    """
    if not agent_ids:
        return {}
    from sqlalchemy import func, select

    from app.db.models.hired_agent import HiredAgent, HiredStatus

    q = (
        select(HiredAgent.agent_id, func.count(func.distinct(HiredAgent.address)))
        .where(
            HiredAgent.agent_id.in_(agent_ids),
            HiredAgent.status == HiredStatus.PAID,
        )
        .group_by(HiredAgent.agent_id)
    )
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(q)).all()
    return {agent_id: int(count) for agent_id, count in rows}


# ---------------------------------------------------------------------------
# Agent profile (detail page technical sheet) — category study §8
# ---------------------------------------------------------------------------


#: Hex-encoded `platform` value stored by the upstream for EvoEvo agents.
_EVOEVO_PLATFORM_HEX: str = "0x" + "EvoEvo".encode().hex()


def _json_text(column: Any, keys: list[str], dialect: str) -> Any:
    """Text-valued JSON path expression, portable across dialects.

    Postgres JSONB needs `->>` (astext) to yield text; the sqlite test
    harness uses `json_extract`, which returns text directly.
    """
    from sqlalchemy import func

    if dialect == "postgresql":
        expr = column
        for key in keys:
            expr = expr[key]
        return expr.astext
    return func.json_extract(column, "$." + ".".join(keys))


def _platform_expression(platform: str, dialect: str = "postgresql") -> Any:
    """SQL expression for the origin-platform filter (category study §8).

    EvoEvo agents carry `onchain[].key == "platform"` with the hex-encoded
    value; Termix agents carry `offchain_content.termix`; everything else
    is `other`. Postgres uses a raw EXISTS subquery for maximum compat;
    the sqlite fallback (test harness only) matches the hex substring.
    """
    from sqlalchemy import false, func, or_, text

    # termix uses IS NOT NULL which can yield NULL for agents without
    # offchain_content at all; coerce to false so ~or_(evo, termix) works.
    termix = func.coalesce(
        _json_text(AgentCache.raw_metadata, ["offchain_content", "termix"], dialect).is_not(None),
        false(),
    )
    if dialect == "postgresql":
        # EXISTS (SELECT 1 FROM jsonb_array_elements(raw_metadata->'onchain')
        #   AS oe WHERE oe->>'key'='platform' AND oe->>'value'=:hex)
        # Uses ->> (works on all Postgres 9.4+) instead of jsonb_path_query_first
        # which may not be available on older versions.
        evo = text(
            "EXISTS (SELECT 1 FROM jsonb_array_elements(agent_cache.raw_metadata"
            "->'onchain') AS oe WHERE oe->>'key' = 'platform'"
            " AND oe->>'value' = :hex)"
        ).bindparams(hex=_EVOEVO_PLATFORM_HEX)
    else:
        # sqlite test harness: no jsonb_array_elements; approximate match.
        evo = _json_text(AgentCache.raw_metadata, ["onchain"], dialect).like(
            f"%{_EVOEVO_PLATFORM_HEX}%"
        )
    return {"evoevo": evo, "termix": termix, "other": ~or_(evo, termix)}.get(platform, or_(False))


def _hex_to_text(value: str | None) -> str | None:
    """Decode an 0x hex string stored by the upstream (onchain key/value)."""
    if not value or not isinstance(value, str):
        return None
    try:
        if value.startswith("0x"):
            return bytes.fromhex(value[2:]).decode("utf-8", "replace")
        return value
    except (ValueError, TypeError):
        return None


def _onchain_value(agent: Any, key: str) -> str | None:
    """First `value` for `key` inside raw_metadata.onchain entries."""
    rm = agent.raw_metadata or {}
    for entry in rm.get("onchain") or []:
        if isinstance(entry, dict) and entry.get("key") == key:
            return entry.get("value")
    return None


def _offchain(agent: Any) -> dict[str, Any]:
    """The off-chain agent definition from raw_metadata, or {}."""
    rm = agent.raw_metadata or {}
    if not isinstance(rm, dict):
        return {}
    oc = rm.get("offchain_content")
    return oc if isinstance(oc, dict) else {}


def _build_agent_profile(agent: Any) -> dict[str, Any]:
    """Flatten the technical sheet fields (category study §8) for the
    detail template. All values are source-reported; the template renders
    them as-is with '—' fallbacks, never a judgment."""
    off = _offchain(agent) or {}
    termix = off.get("termix") or {}
    services = agent.services or {}

    platform = _hex_to_text(_onchain_value(agent, "platform"))
    if not platform and off.get("termix"):
        platform = "Termix"

    a2a = services.get("a2a") or {}
    if not a2a:
        for svc in off.get("services") or []:
            if isinstance(svc, dict) and svc.get("name") == "A2A":
                a2a = svc
                break

    health = agent.health_status
    if isinstance(health, str):
        health = {"overall_status": health}

    breakdown = (agent.scores or {}).get("breakdown") or {}
    dimensions = []
    for name, dim in (breakdown.get("dimensions") or {}).items():
        if isinstance(dim, dict) and dim.get("score") is not None:
            dimensions.append(
                {"name": name, "score": dim.get("score"), "weight": dim.get("weight")}
            )

    parse = agent.parse_status or {}
    health_services = []
    if isinstance(health, dict):
        for name, svc in (health.get("services") or {}).items():
            if isinstance(svc, dict):
                health_services.append(
                    {
                        "name": name,
                        "status": svc.get("status"),
                        "latency_ms": svc.get("latency_ms"),
                        "domain_verified": svc.get("domain_verified"),
                    }
                )

    # Extract OASF data from offchain services
    oASF_skills: list[str] = []
    oasf_domains: list[str] = []
    for svc in off.get("services") or []:
        if isinstance(svc, dict) and svc.get("name") == "OASF":
            oASF_skills = svc.get("skills") or []
            oasf_domains = svc.get("domains") or []
            break

    # Extract social links from offchain services
    social_links = {}
    for svc in off.get("services") or []:
        if isinstance(svc, dict):
            svc_name = (svc.get("name") or "").lower()
            svc_endpoint = svc.get("endpoint")
            if svc_name in ("twitter", "telegram", "email", "web") and svc_endpoint:
                social_links[svc_name] = svc_endpoint

    return {
        "platform": platform,
        "termix_category": (termix.get("profile") or {}).get("category"),
        "tags": off.get("tags") or agent.tags or [],
        "a2a_endpoint": a2a.get("endpoint"),
        "a2a_version": a2a.get("version"),
        "a2a_skills": a2a.get("skills") or [],
        "built_with": _hex_to_text(_onchain_value(agent, "built_with")),
        "hireable": bool(agent.x402_supported and agent.agent_wallet),
        "wallet": agent.agent_wallet,
        "health_score": agent.health_score,
        "health_status": (health or {}).get("overall_status") if isinstance(health, dict) else None,
        "health_services": health_services,
        "metadata_completeness": agent.metadata_completeness_score,
        "score_dimensions": dimensions,
        "parse_status": parse.get("status") if isinstance(parse, dict) else None,
        "parse_errors": len(parse.get("errors") or []) if isinstance(parse, dict) else 0,
        "parse_warnings": len(parse.get("warnings") or []) if isinstance(parse, dict) else 0,
        # EIP-8004 registration data from offchain_content
        "offchain_name": off.get("name"),
        "offchain_description": off.get("description"),
        "offchain_active": off.get("active"),
        "offchain_x402": off.get("x402Support"),
        "offchain_services": off.get("services") or [],
        "offchain_attributes": off.get("attributes") or [],
        "offchain_image": off.get("image"),
        "domain_proof": next(
            (
                a["value"]
                for a in (off.get("attributes") or [])
                if isinstance(a, dict) and a.get("trait_type") == "Domain proof"
            ),
            None,
        ),
        # OASF runtime data
        "oasf_skills": oASF_skills,
        "oasf_domains": oasf_domains,
        # Social links
        "social_links": social_links,
        # Provider info
        "provider": off.get("provider") or {},
        # Capabilities
        "capabilities": off.get("capabilities") or {},
        # Documentation
        "documentation_url": off.get("documentationUrl"),
        # Protocol info
        "protocol_version": off.get("protocolVersion"),
    }


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
    x402: str | None = None,
    sort: str = "average_score",
    owner: str | None = None,
    hireable: str | None = None,
    health: str | None = None,
    platform: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=24, ge=1, le=100),
) -> HTMLResponse:
    """Home: full page on first paint, partials on HTMX load-more."""
    x402_bool: bool | None = None
    if x402 == "true":
        x402_bool = True
    elif x402 == "false":
        x402_bool = False
    hireable_bool: bool | None = None
    if hireable == "true":
        hireable_bool = True
    elif hireable == "false":
        hireable_bool = False
    items, total = await _list_agents_page(
        page,
        page_size,
        q=q,
        category=category,
        x402=x402_bool,
        sort=sort,
        owner=owner,
        hireable=hireable_bool,
        health=health,
        platform=platform,
    )
    total_pages = (total + page_size - 1) // page_size if total else 0
    hire_price_usd = get_settings().x402_default_price_usd
    hires = await _hires_count([a.agent_id for a in items])
    is_htmx = request.headers.get("HX-Request", "").lower() == "true"
    if is_htmx:
        # HTMX "load more" appends new cards without a wrapper (spec R4, S3).
        return _render(
            request,
            "partials/agent_card_htmx.html",
            {
                "items": items,
                "page": page,
                "page_size": page_size,
                "sort": sort,
                "q": q,
                "category": category,
                "x402": x402,
                "owner": owner,
                "hireable": hireable,
                "health": health,
                "platform": platform,
                "has_more": page < total_pages,
                "hires": hires,
                "hire_price_usd": hire_price_usd,
            },
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
            "owner": owner,
            "hireable": hireable,
            "health": health,
            "platform": platform,
            "has_more": page < total_pages,
            "hires": hires,
            "hire_price_usd": hire_price_usd,
        },
    )


@router.get("/agents/{chain_id}/{token_id}")
async def agent_detail(request: Request, chain_id: int, token_id: int) -> Response:
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

    profile = _build_agent_profile(row)

    # Fetch supplementary platform card data (Termix or EvoEvo).
    termix_card: dict[str, Any] | None = None
    evoevo_card: dict[str, Any] | None = None
    mcp_info: dict[str, Any] | None = None

    platform_name = profile.get("platform")
    if platform_name == "Termix":
        from app.services.client_termix import fetch_termix_card

        termix_card = await fetch_termix_card(token_id)
    elif platform_name == "EvoEvo":
        from app.services.client_evoevo import fetch_evoevo_card

        evoevo_card = await fetch_evoevo_card(token_id)

    # Fetch MCP server info for agents with MCP services.
    mcp_endpoint = (row.services or {}).get("mcp", {}).get("endpoint")
    if mcp_endpoint:
        from app.services.client_mcp import fetch_mcp_info

        mcp_info = await fetch_mcp_info(mcp_endpoint)

    hires = await _hires_count([row.agent_id])

    # On-chain metrics from indexed DB
    onchain_stats = {"transfers": 0, "volume": "0", "events": 0}
    try:
        from app.db.models.onchain_index import OnchainAgentEvent, OnchainTransfer

        async with AsyncSessionLocal() as ocs:
            # $U transfers received by this agent
            from sqlalchemy import func

            t_result = await ocs.execute(
                select(
                    func.count().label("total"),
                    func.coalesce(func.sum(OnchainTransfer.value), 0).label("volume"),
                ).where(
                    OnchainTransfer.linked_agent_id == row.agent_id,
                    OnchainTransfer.transfer_type == "erc20_u",
                )
            )
            t_row = t_result.one()
            onchain_stats["transfers"] = t_row.total or 0
            onchain_stats["volume"] = str(t_row.volume or 0)

            # Agent NFT events (mints + transfers)
            e_result = await ocs.execute(
                select(func.count()).where(
                    OnchainAgentEvent.agent_id == row.agent_id,
                )
            )
            onchain_stats["events"] = e_result.scalar() or 0
    except Exception:
        logger.warning("Failed to fetch onchain stats for %s", row.agent_id, exc_info=True)

    return _render(
        request,
        "pages/agent_detail.html",
        {
            "agent": row,
            "pay_to": row.agent_wallet,
            "hire_price_usd": get_settings().x402_default_price_usd,
            "hires": hires,
            "profile": profile,
            "termix_card": termix_card,
            "evoevo_card": evoevo_card,
            "mcp_info": mcp_info,
            "onchain_stats": onchain_stats,
        },
    )


@router.get("/favorites", response_class=HTMLResponse)
async def favorites(request: Request) -> HTMLResponse:
    """Auth-gated favorites page. Redirects to /auth for anonymous callers (spec S6)."""

    from app.services.auth import SESSION_COOKIE_NAME, _read_session

    # `get_current_user` raises AuthRequired for anonymous callers; the
    # favorites page wants a clean redirect instead of a 401, so gate on the
    # session cookie directly and only resolve the user when present.
    has_session = _read_session(request.cookies.get(SESSION_COOKIE_NAME)) is not None
    if not has_session:
        if request.headers.get("HX-Request", "").lower() == "true":
            return HTMLResponse(status_code=200, headers={"HX-Redirect": "/auth"})
        return RedirectResponse(url="/auth", status_code=302)  # type: ignore[return-value]
    try:
        user = await get_current_user(request)
    except AuthRequired:
        return RedirectResponse(url="/auth", status_code=302)  # type: ignore[return-value]
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        rows = (
            await session.scalars(select(Favorite).where(Favorite.address == user.address))
        ).all()
    return _render(request, "pages/favorites.html", {"favorites": rows, "user": user})


@router.get("/auth", response_class=HTMLResponse)
async def auth_page(request: Request) -> HTMLResponse:
    """Sign-in affordance (spec #20 R1 + spec #22 R2)."""
    return _render(request, "pages/auth.html", {})


__all__ = ["router", "templates"]
