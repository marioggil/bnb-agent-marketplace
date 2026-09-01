"""Read path tests: GET /api/agents — search, filter, sort, paginate.

Spec: `sdd/marketplace-scaffold-tests/spec` agents-tests R1-R4.
"""

from __future__ import annotations

from app.db.models.agent import (
    BSC_IDENTITY_REGISTRY,
    AgentCache,
    build_agent_id,
)
from tests.conftest import _now


async def _seed_five(session) -> None:
    """Five BSC agents with distinct average_score for sort assertions."""
    rows = [
        {
            "agent_id": build_agent_id(56, BSC_IDENTITY_REGISTRY, t),
            "chain_id": 56,
            "token_id": t,
            "registry_address": BSC_IDENTITY_REGISTRY,
            "name": n,
            "average_score": s,
            "x402_supported": x,
            "category": c,
        }
        for t, n, s, x, c in [
            (1, "A", 90.0, True, "rebalancing"),
            (2, "B", 70.0, False, "other"),
            (3, "C", 80.0, False, "other"),
            (4, "D", 50.0, True, "rebalancing"),
            (5, "E", None, False, "other"),
        ]
    ]
    for r in rows:
        session.add(
            AgentCache(
                supported_protocols=[],
                cross_chain_versions=[],
                raw={},
                created_at=_now(),
                updated_at=_now(),
                **r,
            )
        )
    await session.commit()


# R1 — list + sort + paginate.
async def test_list_sorted_by_score_desc(client, db):
    await _seed_five(db)
    payload = client.get("/api/agents?sort=average_score&page=1&page_size=24").json()
    assert payload["total"] == 5
    # Highest score first, the NULL score lands last via `nullslast()`.
    assert [it["name"] for it in payload["items"]] == ["A", "C", "B", "D", "E"]


async def test_pagination(client, db):
    await _seed_five(db)
    p1 = client.get("/api/agents?sort=average_score&page=1&page_size=2").json()
    p3 = client.get("/api/agents?sort=average_score&page=3&page_size=2").json()
    assert p1["total"] == 5 and len(p1["items"]) == 2
    assert p3["total"] == 5 and len(p3["items"]) == 1


# R2 — page beyond end returns [] with the same total.
async def test_page_beyond_end(client, db):
    await _seed_five(db)
    payload = client.get("/api/agents?sort=average_score&page=99&page_size=24").json()
    assert payload["items"] == [] and payload["total"] == 5


# R3 — x402 + category filter.
async def test_filter_x402_and_category(client, db):
    await _seed_five(db)
    payload = client.get("/api/agents?sort=average_score&x402=true&category=rebalancing").json()
    assert {it["name"] for it in payload["items"]} == {"A", "D"}


# R4 — empty fuzzy search returns 200 + total=0.
async def test_empty_fuzzy_search(client, db):
    await _seed_five(db)
    payload = client.get("/api/agents?q=zzznomatch").json()
    assert payload["items"] == [] and payload["total"] == 0


# Defensive: 422 on bad page_size.
async def test_bad_page_size_is_422(client, db):
    await _seed_five(db)
    assert client.get("/api/agents?page_size=0").status_code == 422


# sdd/doc-refresh TAX-4 — the category filter accepts the 11 taxonomy slugs.
async def test_filter_category_dev_automation(client, db):
    await _seed_five(db)
    db.add(
        AgentCache(
            agent_id=build_agent_id(56, BSC_IDENTITY_REGISTRY, 6),
            chain_id=56,
            token_id=6,
            registry_address=BSC_IDENTITY_REGISTRY,
            name="F",
            average_score=60.0,
            category="dev_automation",
            supported_protocols=[],
            cross_chain_versions=[],
            raw={},
            created_at=_now(),
            updated_at=_now(),
        )
    )
    await db.commit()
    payload = client.get("/api/agents?category=dev_automation").json()
    assert payload["total"] == 1
    assert [it["name"] for it in payload["items"]] == ["F"]


# TAX-4 — an unknown slug is rejected by the Literal validation (422).
async def test_invalid_category_is_422(client, db):
    await _seed_five(db)
    assert client.get("/api/agents?category=bogus").status_code == 422
