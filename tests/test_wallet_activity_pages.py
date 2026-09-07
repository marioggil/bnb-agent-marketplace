"""Page-render tests for the wallet-activity chip on the agent detail page.

Spec: `openspec/changes/wallet-activity/spec.md` AC-8 + R-5 (listing-page exclusion).
Design: `openspec/changes/wallet-activity/design.md` §7 (UI chip) + §5.3 (N+1).

Pins:
  T17 — detail page renders `Wallet activity:` chip + breakdown labels.
  T19 — `creator == owner` label when addresses match (mixed-case).
  T20 — chip renders in `agent_detail.html` with the locked format.
  T21 — listing pages (`/`, `/agents`, `/agents/{chain}`) do NOT render the chip
        AND issue ZERO `OnchainTransfer` queries (N+1 invariant).
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal

import pytest
from sqlalchemy import event

from app.db.models.agent import (
    BSC_CHAIN_ID,
    BSC_IDENTITY_REGISTRY,
    AgentCache,
    build_agent_id,
)
from tests.conftest import _now


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_one_with_addresses(
    db,
    *,
    token_id: int,
    name: str = "Alpha",
    creator_address: str | None = None,
    owner_address: str | None = None,
    activity_score: Decimal | None = Decimal("80"),
) -> str:
    """Seed one AgentCache row at chain 56, the given token, with addresses."""
    aid = build_agent_id(BSC_CHAIN_ID, BSC_IDENTITY_REGISTRY, token_id)
    db.add(
        AgentCache(
            agent_id=aid,
            chain_id=BSC_CHAIN_ID,
            token_id=token_id,
            registry_address=BSC_IDENTITY_REGISTRY,
            name=name,
            creator_address=creator_address,
            owner_address=owner_address,
            activity_score=activity_score,
            supported_protocols=[],
            cross_chain_versions=[],
            raw={},
            created_at=_now(),
            updated_at=_now(),
            tags=[],
            categories=[],
        )
    )
    await db.commit()
    return aid


# ---------------------------------------------------------------------------
# T17 — RED: chip renders with breakdown.
# ---------------------------------------------------------------------------


async def test_agent_detail_shows_wallet_activity_chip(client, db) -> None:
    """Detail page renders the `Wallet activity:` chip and breakdown labels."""
    aid = await _seed_one_with_addresses(
        db,
        token_id=1,
        creator_address="0x" + "aa" * 20,
        owner_address="0x" + "bb" * 20,
    )
    body = client.get("/agents/56/1").text
    assert "Wallet activity:" in body, (
        "chip missing — T20 not yet rendering `Wallet activity:` in template"
    )
    # Per design §7 the chip renders breakdown labels `creator`, `owner`, `track`.
    for label in ("creator", "owner", "track"):
        assert label in body, f"breakdown label {label!r} missing from chip"


# ---------------------------------------------------------------------------
# T19 — RED: `creator == owner` label.
# ---------------------------------------------------------------------------


async def test_agent_detail_chip_shows_creator_is_owner_label(client, db) -> None:
    """`creator_is_owner=True` (mixed-case LOWER-equal) → `creator == owner` label."""
    aid = await _seed_one_with_addresses(
        db,
        token_id=2,
        creator_address="0xAbC" + "0" * 37,
        owner_address="0xabc" + "0" * 37,
    )
    body = client.get("/agents/56/2").text
    assert "creator == owner" in body, (
        "`creator == owner` label missing when addresses match"
    )


# ---------------------------------------------------------------------------
# T21 — NEGATIVE: listing pages do NOT render the chip.
# ---------------------------------------------------------------------------


async def test_home_page_does_not_render_wallet_activity_chip(client, db) -> None:
    """`GET /` MUST NOT contain `Wallet activity:` substring."""
    await _seed_one_with_addresses(db, token_id=10, name="Alpha")
    await _seed_one_with_addresses(db, token_id=11, name="Beta")
    body = client.get("/").text
    assert "Wallet activity:" not in body, (
        "home page must NOT render the wallet-activity chip"
    )


async def test_agents_index_does_not_render_wallet_activity_chip(client, db) -> None:
    """`GET /agents` MUST NOT contain `Wallet activity:` substring."""
    await _seed_one_with_addresses(db, token_id=20, name="Alpha")
    await _seed_one_with_addresses(db, token_id=21, name="Beta")
    body = client.get("/agents").text
    assert "Wallet activity:" not in body, (
        "/agents index page must NOT render the wallet-activity chip"
    )


async def test_agents_chain_index_does_not_render_wallet_activity_chip(client, db) -> None:
    """`GET /agents/{chain}` MUST NOT contain `Wallet activity:` substring."""
    await _seed_one_with_addresses(db, token_id=30, name="Alpha")
    body = client.get("/agents/56").text
    assert "Wallet activity:" not in body, (
        "/agents/{chain} page must NOT render the wallet-activity chip"
    )


async def test_listing_pages_issue_zero_wallet_queries(client, db) -> None:
    """Listing endpoints MUST NOT issue `OnchainTransfer` queries.

    N+1 invariant: a chip on the listing page would push per-page query count
    to 48 indexed seeks (24 agents × 2 wallets). The negative SQLAlchemy listener
    test pins the invariant.
    """
    # Seed several agents so the listing has work to do.
    for tid in range(40, 50):
        await _seed_one_with_addresses(
            db,
            token_id=tid,
            name=f"Agent {tid}",
            creator_address="0x" + f"{tid:040x}",
            owner_address="0x" + f"{tid+100:040x}",
        )

    # The TestClient thread uses a different engine binding than the test's
    # `db` session. Attach the listener to the production engine bound at
    # app.routers.pages (the page route's `get_db` resolves through the test
    # sessionmaker; we listen on the test sessionmaker.sync_engine).
    from app.db import session as session_module
    from app.db.session import AsyncSessionLocal

    counter = {"count": 0}

    def _listener(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
        if "onchain_transfers" in statement.lower():
            counter["count"] += 1

    engine = AsyncSessionLocal.kw["bind"] if hasattr(AsyncSessionLocal, "kw") else None
    # AsyncSessionLocal is an async_sessionmaker; reach its bound engine via session_module.engine
    engine = session_module.engine.sync_engine
    event.listen(engine, "before_cursor_execute", _listener)
    try:
        # Hit the listing endpoints.
        client.get("/")
        client.get("/agents")
        client.get("/agents/56")
    finally:
        event.remove(engine, "before_cursor_execute", _listener)

    assert counter["count"] == 0, (
        f"listing pages must not query OnchainTransfer; got {counter['count']} queries"
    )


# ---------------------------------------------------------------------------
# T18 — graceful degradation when aggregation fails.
# ---------------------------------------------------------------------------


async def test_agent_detail_renders_n_a_on_aggregation_failure(client, db, monkeypatch) -> None:
    """When `fetch_wallet_signals` raises, chip falls back to `Wallet activity: n/a/100`."""
    from app.services import wallet_activity as wallet_activity_mod

    async def _boom(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("simulated DB outage")

    aid = await _seed_one_with_addresses(
        db,
        token_id=50,
        creator_address="0x" + "aa" * 20,
        owner_address="0x" + "bb" * 20,
    )
    monkeypatch.setattr(wallet_activity_mod, "fetch_wallet_signals", _boom)

    body = client.get("/agents/56/50").text
    assert "Wallet activity:" in body, (
        "chip should still render (with fallback) on aggregation failure"
    )
    # Either `n/a/100` or `—/100` is acceptable per spec R-2 fallback.
    assert ("n/a/100" in body) or ("—/100" in body), (
        "chip should render `Wallet activity: n/a/100` (or `—/100`) on failure"
    )
