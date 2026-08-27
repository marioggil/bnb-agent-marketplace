"""Page render tests: full HTML vs HTMX partial, image fallback, auth.

Spec: `sdd/marketplace-scaffold-tests/spec` pages-tests R1, R2, R5, R6.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models.agent import (
    BSC_CHAIN_ID,
    BSC_IDENTITY_REGISTRY,
    AgentCache,
    build_agent_id,
)
from tests.conftest import _now


async def _seed_one(
    session,
    token_id: int = 1,
    name: str = "Alpha",
    image_url: str | None = None,
    owner_address: str | None = None,
) -> str:
    aid = build_agent_id(56, BSC_IDENTITY_REGISTRY, token_id)
    session.add(
        AgentCache(
            agent_id=aid,
            chain_id=BSC_CHAIN_ID,
            token_id=token_id,
            registry_address=BSC_IDENTITY_REGISTRY,
            name=name,
            image_url=image_url,
            owner_address=owner_address,
            supported_protocols=[],
            cross_chain_versions=[],
            raw={},
            created_at=_now(),
            updated_at=_now(),
            tags=[],
            categories=[],
        )
    )
    await session.commit()
    return aid


# R1 — full HTML render.
async def test_home_full_html_renders_cards(client, db):
    await _seed_one(db, 1, name="Alpha")
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
    await _seed_one(db, 1, name="Alpha")
    body = client.get("/agents/56/1").text
    assert 'id="hire-cta"' in body
    assert 'id="hire-status"' in body


# R2 — HTMX swap returns partial only.
async def test_home_htmx_returns_partial(client, db):
    await _seed_one(db, 1, name="Alpha")
    body = client.get("/", headers={"HX-Request": "true"}).text
    assert "<html" not in body and "Alpha" in body


# Filters (category study §8): hireable, platform, health + sort keys.
async def test_filter_hireable(client, db):
    await _seed_one(db, 1, name="Alpha", owner_address="0x" + "ab" * 20)
    await _seed_one(db, 2, name="Beta", owner_address="0x" + "ab" * 20)
    async with db.begin():
        from app.db.models.agent import AgentCache

        for row in (await db.execute(select(AgentCache))).scalars():
            row.x402_supported = row.token_id == 1
    body = client.get("/?hireable=true").text
    assert "Alpha" in body and "Beta" not in body
    body = client.get("/?hireable=false").text
    assert "Beta" in body and "Alpha" not in body


async def test_sort_metadata_completeness(client, db):
    await _seed_one(db, 1, name="Alpha", owner_address="0x" + "ab" * 20)
    await _seed_one(db, 2, name="Beta", owner_address="0x" + "ab" * 20)
    async with db.begin():
        from app.db.models.agent import AgentCache

        for row in (await db.execute(select(AgentCache))).scalars():
            row.metadata_completeness_score = 10 if row.token_id == 1 else 90
    body = client.get("/?sort=metadata_completeness").text
    assert body.index("Beta") < body.index("Alpha")


async def test_filter_health(client, db):
    await _seed_one(db, 1, name="Alpha", owner_address="0x" + "ab" * 20)
    await _seed_one(db, 2, name="Beta", owner_address="0x" + "ab" * 20)
    async with db.begin():
        from app.db.models.agent import AgentCache

        for row in (await db.execute(select(AgentCache))).scalars():
            row.health_status = {"overall_status": "degraded"} if row.token_id == 1 else None
    body = client.get("/?health=degraded").text
    assert "Alpha" in body and "Beta" not in body


async def test_filter_platform_termix(client, db):
    await _seed_one(db, 1, name="Alpha", owner_address="0x" + "ab" * 20)
    await _seed_one(db, 2, name="Beta", owner_address="0x" + "ab" * 20)
    async with db.begin():
        from app.db.models.agent import AgentCache

        for row in (await db.execute(select(AgentCache))).scalars():
            row.raw_metadata = (
                {
                    "offchain_content": {
                        "termix": {"profile": {"category": "Code & Smart Contracts"}}
                    }
                }
                if row.token_id == 1
                else {}
            )
    body = client.get("/?platform=termix").text
    assert "Alpha" in body and "Beta" not in body


async def test_agent_detail_evoevo_card_renders(client, db, monkeypatch):
    """EvoEvo agents get their live card fetched and rendered."""
    from unittest.mock import AsyncMock, patch

    evo_hex = "0x" + "EvoEvo".encode().hex()
    await _seed_one(db, 1, name="EvoBot")
    async with db.begin():
        from app.db.models.agent import AgentCache

        row = await db.scalar(select(AgentCache).where(AgentCache.token_id == 1))
        row.raw_metadata = {
            "onchain": [{"key": "platform", "value": evo_hex}],
        }

    mock_card = {
        "name": "EvoBot",
        "description": "An EvoEvo agent",
        "active": True,
        "x402Support": False,
        "services": [{"name": "web", "endpoint": "https://evoevo.ai/agent/detail?id=1"}],
        "registrations": [{"agentId": 99, "agentRegistry": "eip155:56:0xabc"}],
    }
    with patch(
        "app.services.client_evoevo.fetch_evoevo_card",
        new_callable=AsyncMock,
        return_value=mock_card,
    ):
        body = client.get("/agents/56/1").text
    assert "EvoEvo live data" in body
    assert "EvoBot" in body
    assert "eip155:56:0xabc" in body


async def test_agent_detail_eip8004_registration_renders(client, db):
    """Agents with offchain_content show EIP-8004 registration data."""
    await _seed_one(db, 1, name="BrainAgent")
    async with db.begin():
        from app.db.models.agent import AgentCache

        row = await db.scalar(select(AgentCache).where(AgentCache.token_id == 1))
        row.raw_metadata = {
            "offchain_content": {
                "name": "Brain on BNB",
                "active": True,
                "x402Support": True,
                "services": [
                    {
                        "name": "rebalance_plan",
                        "endpoint": "https://agent.example.com/a2a",
                        "description": "Portfolio rebalance service",
                        "needs": {"holdings": "array of tokens"},
                    }
                ],
                "attributes": [
                    {"trait_type": "Category", "value": "rebalancing"},
                    {"trait_type": "Domain proof", "value": "https://example.com/.well-known/agent-registration.json"},
                ],
            },
        }
    body = client.get("/agents/56/1").text
    assert "EIP-8004 registration data" in body
    assert "Brain on BNB" in body
    assert "rebalance_plan" in body
    assert "Portfolio rebalance service" in body
    assert "rebalancing" in body
    assert "Domain proof" in body


async def test_agent_detail_mcp_info_renders(client, db, monkeypatch):
    """Agents with MCP services get their MCP info fetched and rendered."""
    from unittest.mock import AsyncMock, patch

    await _seed_one(db, 1, name="MCPAgent")
    async with db.begin():
        from app.db.models.agent import AgentCache

        row = await db.scalar(select(AgentCache).where(AgentCache.token_id == 1))
        row.services = {
            "mcp": {
                "endpoint": "https://example.com/mcp/info",
                "tools": [],
                "prompts": [],
                "resources": [],
            }
        }

    mock_mcp = {
        "name": "@example/mcp-server",
        "version": "1.0.0",
        "transport": "stdio",
        "tools": [
            {"name": "pay_tool", "description": "Make a payment"},
            {"name": "balance_tool", "description": "Check balance"},
        ],
        "registry": {"npm": "https://npmjs.com/package/example-mcp"},
        "install": {"npx": "npx -y @example/mcp-server@latest"},
        "docs": "https://example.com/docs",
        "dashboard": "https://example.com/dashboard",
    }
    with patch(
        "app.services.client_mcp.fetch_mcp_info",
        new_callable=AsyncMock,
        return_value=mock_mcp,
    ):
        body = client.get("/agents/56/1").text
    assert "MCP Server Info" in body
    assert "@example/mcp-server" in body
    assert "1.0.0" in body
    assert "2 available" in body
    assert "pay_tool" in body
    assert "balance_tool" in body
    assert "npm" in body
    assert "npx -y @example/mcp-server@latest" in body


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
