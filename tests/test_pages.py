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


# sdd/doc-refresh DSG-2 — the hero renders all 10 category cards, each an
# /?category= link with icon + name + tagline + example.
async def test_home_renders_ten_category_cards(client, db):
    await _seed_one(db, 1, name="Alpha")
    body = client.get("/").text
    assert body.count('class="category-card"') == 10
    slugs = [
        "rebalancing",
        "grid_trading",
        "yield_optimisation",
        "health_factor_monitoring",
        "dev_automation",
        "creative_design",
        "marketing_content",
        "data_analytics",
        "security_compliance",
        "admin_ops",
    ]
    for slug in slugs:
        assert f'href="/?category={slug}"' in body
    # Spot-check taglines/examples from category-study.md §5.
    assert "Turns an API into a workflow" in body
    assert "Finds the hole before the hacker" in body
    assert "Keeps the books in order" in body


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
                    {
                        "trait_type": "Domain proof",
                        "value": "https://example.com/.well-known/agent-registration.json",
                    },
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
    assert "Make a payment" in body
    assert "Check balance" in body
    assert "npm" in body
    assert "npx -y @example/mcp-server@latest" in body


async def test_agent_detail_oasf_and_social_renders(client, db):
    """Agents with OASF skills and social links show them."""
    await _seed_one(db, 1, name="OASF Agent")
    async with db.begin():
        from app.db.models.agent import AgentCache

        row = await db.scalar(select(AgentCache).where(AgentCache.token_id == 1))
        row.raw_metadata = {
            "offchain_content": {
                "name": "Test Agent",
                "active": True,
                "services": [
                    {
                        "name": "OASF",
                        "skills": ["reasoning/planning", "orchestration/delegation"],
                        "domains": ["blockchain/defi", "finance/investment"],
                        "version": "1.0.0",
                        "endpoint": "https://example.com/runtime",
                    },
                    {"name": "web", "endpoint": "https://example.com"},
                    {"name": "twitter", "endpoint": "https://x.com/example"},
                    {"name": "telegram", "endpoint": "https://t.me/example"},
                    {"name": "email", "endpoint": "contact@example.com"},
                ],
                "provider": {"organization": "Example Corp", "url": "https://example.com"},
                "capabilities": {"streaming": True, "pushNotifications": False},
                "documentationUrl": "https://docs.example.com",
                "protocolVersion": "1.0.0",
            },
        }
    body = client.get("/agents/56/1").text
    assert "OASF Runtime Skills" in body
    assert "reasoning" in body
    assert "planning" in body
    assert "orchestration" in body
    assert "delegation" in body
    assert "blockchain" in body
    assert "defi" in body
    assert "Links &amp; Provider" in body
    assert "Example Corp" in body
    assert "Twitter" in body
    assert "Telegram" in body
    assert "contact@example.com" in body
    assert "streaming" in body


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


# sdd/doc-refresh TAX-5 — the category filter select iterates the taxonomy
# (category_options global) and renders a display label per slug.
async def test_home_filter_offers_eleven_category_options(client, db):
    import re

    await _seed_one(db, 1, name="Alpha")
    body = client.get("/").text
    select = re.search(r'<select name="category".*?</select>', body, re.S)
    assert select is not None
    options = select.group(0)
    labels = {
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
    assert options.count("<option") == 12  # "All" + 11 slugs
    for slug, label in labels.items():
        assert f'value="{slug}"' in options
        assert label.replace("&", "&amp;") in options


# ---------------------------------------------------------------------------
# agent-score U1/U2 — card badge + detail breakdown + probe live section
# ---------------------------------------------------------------------------


async def test_agent_card_shows_activity_badge(client, db):
    """U1: the card renders the local activity score in average_score style."""
    from decimal import Decimal

    await _seed_one(db, 1, name="Alpha")
    async with db.begin():
        from app.db.models.agent import AgentCache

        row = await db.scalar(select(AgentCache).where(AgentCache.token_id == 1))
        row.activity_score = Decimal("87.5")
    body = client.get("/").text
    assert "87.5" in body
    assert "activity-score" in body


async def test_agent_card_no_activity_badge_when_null(client, db):
    """U1: agents without a materialized score render no activity badge."""
    await _seed_one(db, 1, name="Alpha")
    body = client.get("/").text
    assert "activity-score" not in body


async def test_agent_detail_renders_activity_breakdown_and_probe(client, db):
    """U2: detail renders local breakdown (score_dimensions markup) + probe."""
    from datetime import timedelta
    from decimal import Decimal

    from app.db.models.agent import AgentCache
    from app.db.models.agent_probe import AgentProbe
    from app.db.models.onchain_index import OnchainAgentEvent

    aid = await _seed_one(db, 1, name="Alpha")
    async with db.begin():
        row = await db.scalar(select(AgentCache).where(AgentCache.token_id == 1))
        row.activity_score = Decimal("87.5")
        row.upstream_created_at = _now() - timedelta(days=183)  # ~6 months → 50
        db.add(
            AgentProbe(
                agent_id=aid,
                probed_at=_now(),
                responded=True,
                http_status=200,
                latency_ms=150,
                status="BOUND",
                presence="online",
                endpoint="https://agent.example/a2a",
                skills_count=2,
                error=None,
            )
        )
        for i in range(5):
            db.add(
                OnchainAgentEvent(
                    agent_id=aid,
                    token_id=1,
                    event_type="transfer",
                    from_address="0x" + "1" * 40,
                    to_address="0x" + f"{i:040x}",
                    block_number=1,
                    timestamp=_now() - timedelta(days=3),
                    tx_hash="0x" + f"{i:064x}",
                )
            )
    body = client.get("/agents/56/1").text
    section = body.split('id="activity-score"', 1)[1].split("</section>", 1)[0]
    # local score + breakdown dimensions (D7 names, not upstream score_dimensions)
    assert "87.5" in section
    assert "events" in section and "recency" in section
    # probe live section (D3 probe snapshot)
    assert "BOUND" in section and "online" in section
    assert "Last probed" in section


# ---------------------------------------------------------------------------
# agent-score U3 — compare partial + page + filter_form options (D3)
# ---------------------------------------------------------------------------


async def test_compare_htmx_partial_returns_fragment(client, db):
    """U3: an HTMX request for /agents/compare returns the fragment only."""
    from decimal import Decimal

    from app.db.models.agent import AgentCache
    from app.db.models.agent_probe import AgentProbe

    aid1 = await _seed_one(db, 1, name="Alpha")
    await _seed_one(db, 2, name="Beta")
    async with db.begin():
        for token, score in [(1, Decimal("90")), (2, Decimal("70"))]:
            row = await db.scalar(select(AgentCache).where(AgentCache.token_id == token))
            row.activity_score = score
        db.add(
            AgentProbe(
                agent_id=aid1,
                probed_at=_now(),
                responded=True,
                http_status=200,
                latency_ms=120,
                status="BOUND",
                presence="online",
                endpoint="https://agent.example/a2a",
                skills_count=2,
                error=None,
            )
        )
    body = client.get(
        "/agents/compare", params={"ids": "56/1,56/2"}, headers={"HX-Request": "true"}
    ).text
    assert "<html" not in body
    table = body.split('id="compare-table"', 1)[1].split("</table>", 1)[0]
    assert "Alpha" in table and "Beta" in table
    assert "90" in table and "70" in table


async def test_compare_full_page(client, db):
    """U3: a plain request renders the compare page wrapper with the table."""
    await _seed_one(db, 1, name="Alpha")
    await _seed_one(db, 2, name="Beta")
    body = client.get("/agents/compare", params={"ids": "56/1,56/2"}).text
    assert "<html" in body
    assert "Compare agents" in body
    assert "Alpha" in body and "Beta" in body


async def test_compare_empty_state(client, db):
    """U3: no ids → the partial renders the empty state."""
    body = client.get("/agents/compare", headers={"HX-Request": "true"}).text
    assert "No agents to compare" in body


async def test_filter_offers_activity_sort_and_healthy_health(client, db):
    """D3: 'Activity' sort option + 'healthy' health option in filter_form."""
    await _seed_one(db, 1, name="Alpha")
    body = client.get("/").text
    assert 'value="activity_score"' in body
    assert "Activity" in body
    assert 'value="healthy"' in body
