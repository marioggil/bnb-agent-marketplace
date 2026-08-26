"""Page render tests: full HTML vs HTMX partial, image fallback, auth.

Spec: `sdd/marketplace-scaffold-tests/spec` pages-tests R1, R2, R5, R6.
"""
from __future__ import annotations

from tests.conftest import _now

from app.db.models.agent import (
    AgentCache, BSC_CHAIN_ID, BSC_IDENTITY_REGISTRY, build_agent_id,
)


async def _seed_one(session, token_id: int = 1, name: str = "Alpha",
                    image_url: str | None = None,
                    owner_address: str | None = None) -> str:
    aid = build_agent_id(56, BSC_IDENTITY_REGISTRY, token_id)
    session.add(AgentCache(
        agent_id=aid, chain_id=BSC_CHAIN_ID, token_id=token_id,
        registry_address=BSC_IDENTITY_REGISTRY, name=name, image_url=image_url,
        owner_address=owner_address,
        supported_protocols=[], cross_chain_versions=[], raw={}, created_at=_now(), updated_at=_now(),
        tags=[], categories=[],
    ))
    await session.commit()
    return aid


# R1 — full HTML render.
async def test_home_full_html_renders_cards(client, db):
    aid = await _seed_one(db, 1, name="Alpha")
    body = client.get("/").text
    assert "<html" in body and "Alpha" in body


# Design alignment (DESIGN.md D6/D8): the full home renders the hero partial
# and the detail page renders the hire panel + trust signals. This pins the
# template graph so a missing partial (TemplateNotFound) fails here, not in
# production; the owner filter and the "Hired by" context are also covered.
async def test_home_renders_hero_and_owner_filter(client, db):
    await _seed_one(db, 1, name="Alpha", owner_address="0x" + "ab" * 20)
    body = client.get("/").text
    assert "Automate your investments with AI agents" in body
    assert 'href="/?owner=' in body


async def test_agent_detail_renders_hire_panel(client, db):
    aid = await _seed_one(db, 1, name="Alpha")
    body = client.get(f"/agents/56/1").text
    assert 'id="hire-cta"' in body
    assert 'id="hire-status"' in body


# R2 — HTMX swap returns partial only.
async def test_home_htmx_returns_partial(client, db):
    await _seed_one(db, 1, name="Alpha")
    body = client.get("/", headers={"HX-Request": "true"}).text
    assert "<html" not in body and "Alpha" in body


# R5 — image fallback renders /static/img/placeholder.svg.
async def test_image_fallback_to_placeholder(client, db):
    await _seed_one(db, 1, name="NoImage", image_url=None)
    body = client.get("/", headers={"HX-Request": "true"}).text
    assert "/static/img/placeholder.svg" in body and "NoImage" in body


# R6 — /favorites anon: 302 for direct nav; 200 + HX-Redirect for HTMX.
async def test_favorites_anon_redirects_to_auth(client):
    response = client.get("/favorites", follow_redirects=False)
    assert response.status_code == 302 and response.headers["location"] == "/auth"


async def test_favorites_anon_htmx_redirect(client):
    response = client.get("/favorites", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert response.headers.get("HX-Redirect") == "/auth"


# /auth page renders.
async def test_auth_page_renders(client):
    body = client.get("/auth").text
    assert "<html" in body and "Sign in" in body
