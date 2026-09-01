"""Pagination tests: page numbers, deep link, HTMX load more.

Spec: `sdd/marketplace-scaffold-tests/spec` pages-tests R3, R4.
"""

from __future__ import annotations

from app.db.models.agent import (
    BSC_CHAIN_ID,
    BSC_IDENTITY_REGISTRY,
    AgentCache,
    build_agent_id,
)
from tests.conftest import _now


async def _seed_n(session, n: int) -> None:
    for i in range(n):
        session.add(
            AgentCache(
                agent_id=build_agent_id(56, BSC_IDENTITY_REGISTRY, i + 1),
                chain_id=BSC_CHAIN_ID,
                token_id=i + 1,
                registry_address=BSC_IDENTITY_REGISTRY,
                name=f"A{i}",
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


# R4 — deep link: /?page=N returns the Nth page.
async def test_deep_link_to_page_n(client, db):
    await _seed_n(db, 5)
    body = client.get("/?sort=name&page=2&page_size=2").text
    assert "A2" in body and "A3" in body
    assert 'class="active"' in body


async def test_deep_link_to_last_page(client, db):
    await _seed_n(db, 5)
    body = client.get("/?sort=name&page=3&page_size=2").text
    assert "A4" in body and "A3" not in body


# R3 — load more button has hx-* attrs; HTMX request returns partial.
async def test_load_more_button_emits_hx_attrs(client, db):
    await _seed_n(db, 5)
    body = client.get("/?sort=name&page=1&page_size=2").text
    assert 'hx-get="/' in body
    assert 'hx-target=".grid"' in body
    assert 'hx-swap="beforeend"' in body


async def test_load_more_returns_partial(client, db):
    await _seed_n(db, 5)
    body = client.get("/?sort=name&page=2&page_size=2", headers={"HX-Request": "true"}).text
    assert "<html" not in body
    assert "A2" in body or "A3" in body
