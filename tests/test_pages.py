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

# Hired-by-you panel: a signed-in user with a paid hire sees the history and
# the "Hire again" CTA; anonymous users see neither.
async def test_agent_detail_shows_hired_panel_and_hire_again(client, db, respx_mock):
    from decimal import Decimal

    from app.config import get_settings
    from app.db.models.hired_agent import HiredAgent, HiredStatus
    from tests.conftest import _sign_in, payai_header

    aid = await _seed_one(db, 1, name="Alpha")
    agent = await db.scalar(select(AgentCache).where(AgentCache.agent_id == aid))
    assert agent is not None
    agent.agent_wallet = "0x" + "77" * 20
    agent.x402_supported = True
    agent.a2a_endpoint = "https://example.com/a2a/card"
    await db.commit()

    address, cookie = _sign_in(client)
    # Create the hire through the real API (the app's connection owns the
    # user row), then flip it to PAID in this session to render the panel.
    from app.services.auth import issue_csrf

    settings = get_settings()
    rail = settings.x402_rail_for(8453)
    respx_mock.get("https://example.com/a2a/card").respond(
        402,
        headers={
            "payment-required": payai_header(
                asset=rail.token_address, network="eip155:8453"
            )
        },
    )
    create = client.post(
        "/api/hires",
        json={"agent_id": aid},
        cookies={"bnb_agent_session": cookie},
        headers={"X-CSRF-Token": issue_csrf(cookie)},
    )
    assert create.status_code == 201, create.text
    row = await db.get(HiredAgent, create.json()["id"])
    assert row is not None
    row.status = HiredStatus.PAID
    row.tx_hash = "0x" + "ab" * 32
    row.amount = Decimal("1.000000000000000000")
    await db.commit()

    client.cookies.set("bnb_agent_session", cookie)
    body = client.get("/agents/56/1").text
    assert "Hired by you" in body
    assert "You hired this agent once" in body
    assert "Hire again for $1.03" in body
    assert "view transaction" in body

async def test_agent_detail_no_hired_panel_for_anonymous(client, db):
    await _seed_one(db, 1, name="Alpha")
    body = client.get("/agents/56/1").text
    assert "Hired by you" not in body
    assert "Hire again" not in body

async def test_agent_detail_lazy_hire_offer_wiring(client, db):
    """R9/D-8: hireable agent renders the stable #hire-cta with the lazy
    hire-offer wiring — inner #hire-offer-slot shows "Checking availability…"
    and the button carries hx-get to the hire-offer endpoint, so the real
    agent price swaps in without replacing the payment.js-bound node."""
    aid = await _seed_one(db, 7, name="LazyProbe")
    agent = await db.scalar(select(AgentCache).where(AgentCache.agent_id == aid))
    assert agent is not None
    agent.agent_wallet = "0x" + "88" * 20
    agent.x402_supported = True
    # Give the agent an A2A endpoint so hire-offer has something to probe.
    agent.a2a_endpoint = "https://agent.example.com/a2a"
    await db.commit()

    body = client.get("/agents/56/7").text
    assert 'id="hire-cta"' in body
    assert 'id="hire-offer-slot"' in body
    assert "Checking availability" in body
    assert 'hx-get="/agents/56/7/hire-offer"' in body
    assert 'hx-target="#hire-offer-slot"' in body
    assert 'hx-trigger="load"' in body
    # The button node must survive (payment.js binds it at DOMContentLoaded) —
    # the swap target is the inner slot, never the button itself.
    assert 'data-agent-id="' in body

# Card + detail show the locally-computed average of mirrored feedbacks.
async def test_agent_score_uses_feedback_average(client, db):
    from app.db.models.agent_feedback import AgentFeedback

    aid = await _seed_one(db, 1, name="Alpha")
    now = _now()
    db.add_all(
        [
            AgentFeedback(
                feedback_id=f"56:1:0x{'a' * 40}:{i}",
                agent_id=aid,
                chain_id=BSC_CHAIN_ID,
                token_id=1,
                user_address="0x" + "a" * 40,
                score=100 if i == 1 else 60,
                comment=f"fb {i}",
                submitted_at=now,
                is_revoked=False,
                created_at=now,
                updated_at=now,
            )
            for i in (1, 2)
        ]
    )
    await db.commit()

    card = client.get("/").text
    assert "80" in card  # avg(100, 60) shown on the card score

    detail = client.get("/agents/56/1").text
    assert "Reviews (2) &mdash; avg score 80" in detail

# Rank, cross-chain presence and endpoint verification render from cached data.
async def test_agent_detail_rank_crosschain_endpoint(client, db):
    from app.db.models.agent import AgentCache

    aid = await _seed_one(db, 1, name="Alpha")
    row = await db.scalar(select(AgentCache).where(AgentCache.agent_id == aid))
    assert row is not None
    row.rank = 42
    row.network_rank = 42
    row.cross_chain_links = [
        {"chain_id": 42161, "token_id": 999},
        {"chain_id": 137, "token_id": 888},
        {"chain_id": 999999, "token_id": 777},
    ]
    row.is_endpoint_verified = False
    row.endpoint_verification_error = "domain mismatch"
    row.endpoint_last_checked_at = _now()
    await db.commit()

    body = client.get("/agents/56/1").text
    assert "#42" in body
    assert "Cross-chain versions (3)" in body
    assert "Arbitrum" in body and "token 999" in body
    assert "https://8004scan.io/agents/arbitrum/999" in body
    assert "Polygon" in body and "token 888" in body
    # Unknown chain id: no link, plain label.
    assert "chain 999999" in body and "token 777" in body
    assert "Endpoint verification" in body
    assert "not verified" in body and "domain mismatch" in body

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

# ---------------------------------------------------------------------------
# Phase 1b — OFAC compliance Hire-CTA gate (T5 + T7).
# ---------------------------------------------------------------------------

async def test_agent_detail_hire_cta_disabled_when_both_compliance_flags_set(
    client, db
):
    """Both `creator_flagged` and `owner_flagged` MUST disable `#hire-cta`.

    Spec: `openspec/changes/compliance-flags/spec.md` R5 + AC-5.
    Pin the three render properties together:
      1. `id="hire-cta"` and `disabled` on the same `<button>` element;
      2. `aria-disabled="true"` on the same element;
      3. the OFAC-blocking banner copy is present in the body.

    RED evidence (T5): the route handler does not yet read
    `agent_compliance_flags` for the agent, so the template context
    has no `creator_flagged` / `owner_flagged` keys and the button
    renders without `disabled`.
    """
    # Seed agent + wallet so the CTA would otherwise render enabled.
    aid = await _seed_one(db, 1, name="Alpha", owner_address="0x" + "77" * 20)
    async with db.begin():
        row = await db.scalar(select(AgentCache).where(AgentCache.agent_id == aid))
        assert row is not None
        row.agent_wallet = "0x" + "88" * 20
        row.x402_supported = True
    # Pre-seed both compliance flags.
    from tests._compliance_fixtures import seed_compliance_agent

    await seed_compliance_agent(
        db,
        agent_id=aid,
        creator_address="0x" + "aa" * 20,
        owner_address="0x" + "bb" * 20,
        creator_flagged=True,
        owner_flagged=True,
        compliance_penalty=50.00,
        activity_score=72.50,
    )

    body = client.get("/agents/56/1").text

    # Pin the block banner copy (one shared substring, easy to grep).
    assert (
        "Hiring is disabled while OFAC compliance is unresolved for this agent"
        in body
    ), "OFAC block banner copy missing on dual-flag agent"

    # Locally isolate the #hire-cta element so we can assert the gate attrs
    # both appear on the same button (not just somewhere in the page).
    cta_idx = body.find('id="hire-cta"')
    assert cta_idx != -1, "Hire CTA element missing from rendered HTML"
    # The opening <button ... id="hire-cta" ...> tag ends at the first '>'
    # after `id="hire-cta"`.
    cta_tag_end = body.find(">", cta_idx)
    assert cta_tag_end != -1
    cta_open_tag = body[cta_idx:cta_tag_end]
    assert "disabled" in cta_open_tag, (
        f"#hire-cta must render `disabled` when both compliance flags are set. "
        f"Got opening tag: {cta_open_tag!r}"
    )
    assert 'aria-disabled="true"' in cta_open_tag, (
        f"#hire-cta must render `aria-disabled=\"true\"` when both compliance "
        f"flags are set. Got opening tag: {cta_open_tag!r}"
    )

async def test_agent_detail_hire_cta_enabled_when_only_one_flag_set(client, db):
    """Single-flag agent renders warning copy only; `#hire-cta` stays enabled.

    Spec: `openspec/changes/compliance-flags/spec.md` R5 (single-flag
    scenario). The CTA MUST NOT carry `disabled` or `aria-disabled="true"`,
    and the OFAC-blocking banner copy MUST NOT appear.
    """
    aid = await _seed_one(db, 1, name="Alpha", owner_address="0x" + "77" * 20)
    async with db.begin():
        row = await db.scalar(select(AgentCache).where(AgentCache.agent_id == aid))
        assert row is not None
        row.agent_wallet = "0x" + "88" * 20
        row.x402_supported = True
    from tests._compliance_fixtures import seed_compliance_agent

    # Creator flagged, owner NOT flagged — single flag.
    await seed_compliance_agent(
        db,
        agent_id=aid,
        creator_address="0x" + "aa" * 20,
        owner_address="0x" + "bb" * 20,
        creator_flagged=True,
        owner_flagged=False,
        compliance_penalty=30.00,
        activity_score=72.50,
    )

    body = client.get("/agents/56/1").text

    # Warning copy IS rendered.
    assert "OFAC warning: creator address flagged" in body, (
        "Single-flag agent must show the creator OFAC warning copy"
    )

    # Block-banner copy is NOT rendered (only appears on dual-flag).
    assert (
        "Hiring is disabled while OFAC compliance is unresolved for this agent"
        not in body
    )

    # The CTA must NOT carry `disabled` on its opening tag — the gate is
    # only active when both flags are set.
    cta_idx = body.find('id="hire-cta"')
    assert cta_idx != -1
    cta_tag_end = body.find(">", cta_idx)
    cta_open_tag = body[cta_idx:cta_tag_end]
    assert "disabled" not in cta_open_tag, (
        f"#hire-cta must NOT render `disabled` when only one flag is set. "
        f"Got opening tag: {cta_open_tag!r}"
    )
    assert 'aria-disabled="true"' not in cta_open_tag, (
        f"#hire-cta must NOT render `aria-disabled=\"true\"` when only one "
        f"flag is set. Got opening tag: {cta_open_tag!r}"
    )

# ---------------------------------------------------------------------------
# ERC-8183 escrow (buyer-side) — secondary hire button on the agent page
# ---------------------------------------------------------------------------

from app.config import _settings_cache  # noqa: E402


async def _seed_agent_with_wallet(
    session,
    token_id: int,
    *,
    wallet: str | None = "0x" + "ab" * 20,
    name: str = "WalletAgent",
) -> str:
    aid = build_agent_id(56, BSC_IDENTITY_REGISTRY, token_id)
    session.add(
        AgentCache(
            agent_id=aid,
            chain_id=BSC_CHAIN_ID,
            token_id=token_id,
            registry_address=BSC_IDENTITY_REGISTRY,
            name=name,
            agent_wallet=wallet,
            description="A test agent with a wallet",
            tags=["test", "erc8183"],
            supported_protocols=[],
            cross_chain_versions=[],
            raw={},
            created_at=_now(),
            updated_at=_now(),
        )
    )
    await session.commit()
    return aid

async def test_agent_page_renders_escrow_button_when_wallet_present(client, db):
    """When the agent has agent_wallet and ERC8183_ENABLED=true, the page
    renders the secondary "Hire via Escrow" button + the modal scaffold."""
    await _seed_agent_with_wallet(db, 100, wallet="0x" + "ab" * 20)
    body = client.get("/agents/56/100").text
    assert 'id="hire-escrow-cta"' in body
    assert "Hire via Escrow" in body
    # Modal scaffold present
    assert 'id="hire-escrow-modal"' in body
    # Data attributes for the JS handler
    assert 'data-commerce-address="0xEa4DAa3100A767e86FDed867729ae7446476EBA6"' in body
    assert 'data-u-token-address="0xcE24439F2D9C6a2289F741120FE202248B666666"' in body
    assert "data-chain-id=\"56\"" in body
    assert "data-default-budget-wei=\"100000000000000000\"" in body

async def test_agent_page_hides_escrow_button_when_no_wallet(client, db):
    await _seed_agent_with_wallet(db, 101, wallet=None)
    body = client.get("/agents/56/101").text
    assert 'id="hire-escrow-cta"' not in body
    assert 'id="hire-escrow-modal"' not in body

async def test_agent_page_hides_escrow_button_when_disabled(client, db, monkeypatch):
    """ERC8183_ENABLED=false -> the partial no-ops; no button rendered."""
    monkeypatch.setenv("ERC8183_ENABLED", "false")
    _settings_cache.cache_clear()
    try:
        await _seed_agent_with_wallet(db, 102, wallet="0x" + "ab" * 20)
        body = client.get("/agents/56/102").text
        assert 'id="hire-escrow-cta"' not in body
        assert 'id="hire-escrow-modal"' not in body
    finally:
        _settings_cache.cache_clear()

# ---------------------------------------------------------------------------
# Home card anatomy (simplify): no "Hire" button + no "Hired by N" in the card.
# Hire happens on the detail page (DESIGN.md card anatomy fix v2).
# ---------------------------------------------------------------------------

async def _seed_card_agent(
    session,
    token_id: int,
    *,
    name: str = "CardAgent",
    owner: str = "0x" + "11" * 20,
    wallet: str | None = "0x" + "ab" * 20,
) -> str:
    aid = build_agent_id(56, BSC_IDENTITY_REGISTRY, token_id)
    session.add(
        AgentCache(
            agent_id=aid,
            chain_id=BSC_CHAIN_ID,
            token_id=token_id,
            registry_address=BSC_IDENTITY_REGISTRY,
            name=name,
            description="A test agent for card anatomy",
            owner_address=owner,
            agent_wallet=wallet,
            # No owner_username so the truncated wallet form is rendered.
            category="finance_payments",
            average_score=82.5,
            total_feedbacks=12,
            star_count=4,
            activity_score=70.0,
            supported_protocols=[],
            cross_chain_versions=[],
            raw={},
            created_at=_now(),
            updated_at=_now(),
        )
    )
    await session.commit()
    return aid

async def test_home_card_has_no_hire_button(client, db):
    """The home card must NOT contain a Hire CTA — hiring happens on the
    detail page. Keeps the listing scannable."""
    await _seed_card_agent(db, 200)
    body = client.get("/").text
    assert "CardAgent" in body
    # The detail-page #hire-cta lives in agent_detail.html, not the card.
    assert 'id="hire-cta"' not in body
    assert ">Hire</a>" not in body
    assert ">Not hireable</button>" not in body

async def test_home_card_has_no_hired_by_counter(client, db):
    """The 'Hired by N' usage counter is a detail-page signal; the card
    should not duplicate it."""
    await _seed_card_agent(db, 201)
    body = client.get("/").text
    assert "CardAgent" in body
    # The card used to render 'Hired by {{ _hire_count }}' when hires > 0.
    # With no hires, it never showed — but also must not show even if hires > 0.
    assert "Hired by" not in body

async def test_home_card_keeps_owner_wallet_truncated(client, db):
    """The owner wallet stays visible (truncated) so the user can identify
    the publisher at a glance."""
    # Use a distinctive owner address so the truncated form is unique on the page.
    owner = "0x" + "cafe" + "00" * 18 + "beef"  # 0xcafe000...000beef
    await _seed_card_agent(db, 202, owner=owner)
    body = client.get("/").text
    # Card renders the owner as 'by 0xcafe…beef' (first 4 + last 4 chars).
    assert "by 0xcafe" + chr(0x2026) + "beef" in body

async def test_home_card_keeps_score_and_activity(client, db):
    """Score and activity score stay on the card — they're trust signals."""
    await _seed_card_agent(db, 203)
    body = client.get("/").text
    assert "score" in body
    assert "activity" in body


# ---------------------------------------------------------------------------
# Total score wiring (anatomy v2): the displayed score = sum of 6 components
# + 10 bonus if hires > 10 + 10 bonus if reviews > 10.
# ---------------------------------------------------------------------------


async def test_detail_page_total_score_is_sum_of_components(client, db):
    """The rendered total score equals the sum of available components
    plus binary bonuses for hires/reviews > 10. No cap."""
    import re
    from datetime import datetime, timezone
    from decimal import Decimal

    from app.db.models.agent import (
        BSC_CHAIN_ID,
        BSC_IDENTITY_REGISTRY,
        AgentCache,
        build_agent_id,
    )
    from app.db.models.user import User

    aid = build_agent_id(56, BSC_IDENTITY_REGISTRY, 313)
    owner = "0x" + "11" * 20
    db.add(User(address=owner, created_at=datetime.now(timezone.utc)))
    db.add(AgentCache(
        agent_id=aid,
        chain_id=BSC_CHAIN_ID, token_id=313,
        registry_address=BSC_IDENTITY_REGISTRY,
        name="ScoreAgent",
        description="x",
        agent_wallet="0x" + "ab" * 20,
        owner_address=owner,
        health_score=80,
        is_endpoint_verified=True,
        metadata_completeness_score=90,
        activity_score=70,
        total_feedbacks=15,
        average_score=Decimal("85"),
        supported_protocols=[], cross_chain_versions=[], raw={},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    ))
    await db.commit()
    body = client.get("/agents/56/313").text
    m = re.search(r'metrics-score-value[^>]*>([^<]+)<', body)
    assert m is not None, f"score span not found in: {body[:500]}"
    rendered_score = m.group(1).strip()
    assert rendered_score == "400", f"expected 400, got {rendered_score!r}"


# ---------------------------------------------------------------------------
# Off-chain service endpoint resolution (agent-detail render).
# {agentId} in Termix URLs must resolve: /a2a -> token_id, /services ->
# the internal Termix card id fetched live from fetch_termix_card.
# ---------------------------------------------------------------------------

_A2A_TPL = "https://platform-backend.prod.termix.live/api/v1/a2a/agents/{agentId}/card"
_SVC_TPL = "https://platform-backend.prod.termix.live/api/v1/agents/{agentId}/services"


async def _seed_termix_agent(session, token_id: int, *, name: str = "TermixAgent") -> str:
    aid = build_agent_id(56, BSC_IDENTITY_REGISTRY, token_id)
    session.add(AgentCache(
        agent_id=aid,
        chain_id=BSC_CHAIN_ID, token_id=token_id,
        registry_address=BSC_IDENTITY_REGISTRY,
        name=name,
        description="Termix-registered agent",
        agent_wallet="0x" + "ab" * 20,
        owner_address="0x" + "11" * 20,
        supported_protocols=[],
        cross_chain_versions=[],
        raw_metadata={
            "offchain_content": {
                "name": name,
                "active": True,
                "termix": {
                    "namespace": "aacp-platform",
                    "ownerAccountId": "cmtnOWNER",
                    "originalName": name.replace(".agent", ""),
                },
                "services": [
                    {"name": "A2A", "version": "0.3.0", "endpoint": _A2A_TPL},
                    {
                        "name": "Termix Platform",
                        "version": "aacp-platform-v1",
                        "endpoint": _SVC_TPL,
                    },
                ],
            }
        },
        created_at=_now(),
        updated_at=_now(),
    ))
    await session.commit()
    return aid


async def test_agent_detail_resolves_agent_id_in_offchain_services(client, db, monkeypatch):
    """When the live Termix card is fetched, both service endpoints resolve:
    /a2a uses token_id; /services uses the internal card id. No literal
    {agentId} remains in the rendered service endpoints."""
    token_id = 401
    await _seed_termix_agent(db, token_id)
    await db.commit()

    # Stub the live Termix card fetch — returns the internal id.
    async def _fake_card(_token_id: int):
        return {"id": "cmtnINTERNALID", "agentTokenId": str(token_id), "status": "UNBOUND"}

    monkeypatch.setattr("app.services.client_termix.fetch_termix_card", _fake_card)
    body = client.get(f"/agents/56/{token_id}").text

    # No literal placeholder survives in the services block.
    assert "{agentId}" not in body, "literal {agentId} must be resolved"

    # A2A resolves to token_id.
    assert f"/a2a/agents/{token_id}/card" in body
    # Termix Platform /services resolves to the internal card id.
    assert "/agents/cmtnINTERNALID/services" in body
    # token_id must NOT leak into the /services URL.
    assert f"/agents/{token_id}/services" not in body


async def test_agent_detail_offchain_services_without_termix_card(
    client, db, monkeypatch,
):
    """If the live card fetch fails (None), the A2A endpoint still resolves
    via token_id, but the /services endpoint renders with a note instead of
    a broken {agentId} link."""
    token_id = 402
    await _seed_termix_agent(db, token_id)
    await db.commit()

    async def _fake_card_none(_token_id: int):
        return None

    monkeypatch.setattr("app.services.client_termix.fetch_termix_card", _fake_card_none)
    body = client.get(f"/agents/56/{token_id}").text

    # A2A still resolves (only needs token_id).
    assert f"/a2a/agents/{token_id}/card" in body
    # The /services endpoint must NOT render a literal {agentId} URL as-is.
    assert _SVC_TPL not in body
    # And it must not fabricate a token_id URL (that would 404).
    assert f"/agents/{token_id}/services" not in body
