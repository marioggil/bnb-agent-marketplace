"""Ranking endpoint tests — `GET /api/agents/score` → `Page[ScoreOutMinimal]`.

Spec: `openspec/changes/score-integration/spec.md` AC-1 + AC-2 + AC-3.
Design: `openspec/changes/score-integration/design.md` §3 + §4.

Pins the seven-field projection, the DESC NULLS LAST sort, the category
filter, the limit clamp (default 20, max 100), and the Literal validation
on `category` + `sort` (= HTTP 400). All checks via the FastAPI test
client — no direct ORM bypass.

Strict TDD: every assertion below pins behaviour that lands in
`app/routers/agents.py::rank_agents_score` and the new
`app/schemas/score.py::ScoreOutMinimal`. The RED phase observed the
missing schema (T1) and missing route (T3/T5/T6); the GREEN phase landed
the schema + handler. Tests are organised by acceptance criterion.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import bindparam, text
from sqlalchemy.types import DateTime

from app.db.models.agent import (
    BSC_CHAIN_ID,
    BSC_IDENTITY_REGISTRY,
    AgentCache,
    build_agent_id,
)
from tests.conftest import _now


# ---------------------------------------------------------------------------
# Seed helpers (local — kept self-contained per strict TDD isolation rule)
# ---------------------------------------------------------------------------


async def _seed_agent(
    db,
    *,
    token_id: int,
    name: str = "Agent",
    activity_score: Decimal | None = Decimal("0"),
    compliance_penalty: float = 0.0,
    creator_flagged: bool = False,
    owner_flagged: bool = False,
    creator_is_owner: bool = False,
    category: str = "other",
) -> str:
    """Seed one AgentCache row + an optional `agent_compliance_flags` row.

    Mirrors `_compliance_fixtures.seed_compliance_agent` (kept inline so this
    file stays independent of the Phase 1b fixture module). Returns the
    `agent_id` so callers can chain further assertions. Uses raw SQL with
    `bindparam(DateTime(timezone=True))` so tz-aware datetimes route through
    SQLAlchemy's adapter and avoid the `filterwarnings=error` sqlite3
    default-datetime-adapter DeprecationWarning.
    """
    aid = build_agent_id(BSC_CHAIN_ID, BSC_IDENTITY_REGISTRY, token_id)
    chain_id = BSC_CHAIN_ID
    registry = BSC_IDENTITY_REGISTRY
    creator = "0x" + f"{token_id:040x}"
    owner = "0x" + f"{token_id+10000:040x}"
    now = _now()
    # SQLite via aiosqlite does not bind Decimal directly; cast to float at
    # the parameter boundary. The column is Numeric(5, 2) — sqlite stores
    # it as REAL/REAL-affinity and SQLAlchemy coerces the returned value
    # back to a string-shaped Decimal via the type's result processor.
    activity_score_param: float | None = (
        float(activity_score) if activity_score is not None else None
    )
    compliance_penalty_param: float = float(compliance_penalty)

    cache_stmt = text(
        """
        INSERT INTO agent_cache (
            agent_id, chain_id, token_id, registry_address,
            name, creator_address, owner_address,
            activity_score, compliance_penalty,
            supported_protocols, cross_chain_versions, raw,
            tags, categories, category,
            created_at, updated_at
        ) VALUES (
            :aid, :chain_id, :token_id, :registry,
            :name, :creator, :owner,
            :activity_score, :compliance_penalty,
            '[]', '[]', '{}',
            '[]', '[]', :category,
            :now, :now
        )
        ON CONFLICT(agent_id) DO UPDATE SET
            name = excluded.name,
            activity_score = excluded.activity_score,
            compliance_penalty = excluded.compliance_penalty,
            category = excluded.category,
            updated_at = excluded.updated_at
        """
    ).bindparams(bindparam("now", type_=DateTime(timezone=True)))
    await db.execute(
        cache_stmt,
        {
            "aid": aid,
            "chain_id": chain_id,
            "token_id": token_id,
            "registry": registry,
            "name": name,
            "creator": creator,
            "owner": owner,
            "activity_score": activity_score_param,
            "compliance_penalty": compliance_penalty_param,
            "category": category,
            "now": now,
        },
    )

    if creator_flagged or owner_flagged or creator_is_owner:
        flag_stmt = text(
            """
            INSERT INTO agent_compliance_flags (
                agent_id, creator_flagged, creator_flag_sources,
                owner_flagged, owner_flag_sources, creator_is_owner,
                flagged_data_stale, refreshed_at
            ) VALUES (
                :aid, :cf, '[]', :of, '[]', :cio, 0, :now
            )
            ON CONFLICT(agent_id) DO UPDATE SET
                creator_flagged=excluded.creator_flagged,
                owner_flagged=excluded.owner_flagged,
                creator_is_owner=excluded.creator_is_owner,
                refreshed_at=excluded.refreshed_at
            """
        ).bindparams(bindparam("now", type_=DateTime(timezone=True)))
        await db.execute(
            flag_stmt,
            {
                "aid": aid,
                "cf": 1 if creator_flagged else 0,
                "of": 1 if owner_flagged else 0,
                "cio": 1 if creator_is_owner else 0,
                "now": now,
            },
        )

    await db.commit()
    return aid


async def _seed_many(db, n: int, *, category: str = "other", prefix: str = "B") -> list[str]:
    """Seed N agents with `displayed_activity_score = activity_score - 0` (penalty 0).

    Each agent has a distinct activity score in `[0, 100]` so ordering tests
    are deterministic.
    """
    ids: list[str] = []
    for i in range(n):
        ids.append(
            await _seed_agent(
                db,
                token_id=10_000 + i,
                name=f"{prefix}-{i:03d}",
                activity_score=Decimal(str(float(i))),  # 0.0, 1.0, … (n-1).0
                compliance_penalty=0.0,
                category=category,
            )
        )
    return ids


# ---------------------------------------------------------------------------
# T1 — RED: minimal projection (7-field schema).
# ---------------------------------------------------------------------------


async def test_score_endpoint_returns_minimal_projection(client, db) -> None:
    """AC-3 / T1: response items carry exactly the seven `ScoreOutMinimal` fields."""
    await _seed_agent(
        db,
        token_id=1,
        name="Alpha",
        activity_score=Decimal("72.50"),
        compliance_penalty=20.00,
    )
    resp = client.get(
        "/api/agents/score",
        params={"category": "other", "sort": "displayed_activity_score", "limit": 5},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    items = body["items"]
    assert len(items) == 1
    item = items[0]
    assert set(item.keys()) == {
        "chain",
        "token",
        "name",
        "activity_score",
        "compliance_penalty",
        "displayed_activity_score",
        "creator_is_owner",
    }, f"unexpected key set: {sorted(item.keys())}"
    # Wallet signals + heavy fields MUST be absent (AC-3 + R-5).
    for forbidden in (
        "wallet_activity_score",
        "wallet_activity_breakdown",
        "pillars",
        "breakdown",
    ):
        assert forbidden not in item, f"{forbidden!r} leaked into ScoreOutMinimal"


# ---------------------------------------------------------------------------
# T3 — RED: category filter narrows the result set.
# ---------------------------------------------------------------------------


async def test_score_endpoint_filters_by_category(client, db) -> None:
    """AC-1: `category=other` returns only `category=='other'` rows."""
    await _seed_agent(db, token_id=1, name="OtherA", activity_score=Decimal("80"), category="other")
    await _seed_agent(db, token_id=2, name="OtherB", activity_score=Decimal("60"), category="other")
    await _seed_agent(
        db,
        token_id=3,
        name="Rebal",
        activity_score=Decimal("90"),
        category="rebalancing",
    )

    resp = client.get(
        "/api/agents/score",
        params={"category": "other", "sort": "displayed_activity_score"},
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    names = {item["name"] for item in items}
    assert names == {"OtherA", "OtherB"}, f"category filter leaked: {names!r}"


# ---------------------------------------------------------------------------
# T5 — RED: DESC NULLS LAST ordering.
# ---------------------------------------------------------------------------


async def test_score_endpoint_orders_by_displayed_activity_score_desc(client, db) -> None:
    """AC-2: A=90, C=50, B=NULL → order [A, C, B] (NULL sorts last)."""
    await _seed_agent(db, token_id=10, name="A", activity_score=Decimal("90"), category="other")
    await _seed_agent(db, token_id=11, name="B", activity_score=None, category="other")
    await _seed_agent(db, token_id=12, name="C", activity_score=Decimal("50"), category="other")

    resp = client.get(
        "/api/agents/score",
        params={
            "category": "other",
            "sort": "displayed_activity_score",
            "limit": 10,
        },
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    names = [item["name"] for item in items]
    assert names == ["A", "C", "B"], (
        "DESC NULLS LAST violated: expected [A, C, B], got " + repr(names)
    )


# ---------------------------------------------------------------------------
# T6 — RED: limit clamp (default 20, max 100).
# ---------------------------------------------------------------------------


async def test_score_endpoint_default_limit_20(client, db) -> None:
    """AC-1: 25 seeded agents + no `limit` → exactly 20 returned, page_size=20."""
    await _seed_many(db, 25, category="other")
    resp = client.get(
        "/api/agents/score",
        params={"category": "other", "sort": "displayed_activity_score"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["page_size"] == 20
    assert len(body["items"]) == 20
    # Monotonic non-increasing.
    scores = [item["displayed_activity_score"] for item in body["items"]]
    assert scores == sorted(scores, reverse=True), "items not sorted DESC"


async def test_score_endpoint_limit_clamped_to_100(client, db) -> None:
    """AC-1: seed 150 agents, request `limit=200` → 100 returned, status 200 (silent clamp)."""
    await _seed_many(db, 150, category="other")
    resp = client.get(
        "/api/agents/score",
        params={
            "category": "other",
            "sort": "displayed_activity_score",
            "limit": 200,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["page_size"] == 100, f"expected clamp to 100, got {body['page_size']!r}"
    assert len(body["items"]) == 100


async def test_score_endpoint_negative_limit_returns_400(client, db) -> None:
    """AC-1: `limit=-1` → HTTP 400."""
    resp = client.get(
        "/api/agents/score",
        params={
            "category": "other",
            "sort": "displayed_activity_score",
            "limit": -1,
        },
    )
    assert resp.status_code == 400, resp.text


# ---------------------------------------------------------------------------
# T4 — GREEN contract: literal validation (400 on unknown sort / category).
# ---------------------------------------------------------------------------


async def test_score_endpoint_unknown_sort_returns_400(client, db) -> None:
    """AC-1: `sort=wallet_activity_score` is not whitelisted → HTTP 400.

    The spec AC pins the unknown-sort path to 400 (not 422 — `ValidationError`
    returns 422; the new endpoint raises a fresh 400 boundary).
    """
    resp = client.get(
        "/api/agents/score",
        params={"category": "other", "sort": "wallet_activity_score"},
    )
    assert resp.status_code == 400, resp.text
    assert "sort" in resp.text.lower(), (
        f"error body should reference the offending field; got {resp.text!r}"
    )


async def test_score_endpoint_unknown_category_returns_400(client, db) -> None:
    """AC-1: `category=defi` (not in the Literal) → HTTP 400."""
    resp = client.get(
        "/api/agents/score",
        params={"category": "defi", "sort": "displayed_activity_score"},
    )
    assert resp.status_code == 400, resp.text
    assert "category" in resp.text.lower(), (
        f"error body should reference the offending field; got {resp.text!r}"
    )


async def test_score_endpoint_missing_category_returns_all(client, db) -> None:
    """AC-1: no `category` param → every seeded agent is returned (no filter)."""
    await _seed_agent(db, token_id=20, name="Other", activity_score=Decimal("50"), category="other")
    await _seed_agent(
        db,
        token_id=21,
        name="Rebal",
        activity_score=Decimal("70"),
        category="rebalancing",
    )
    resp = client.get(
        "/api/agents/score",
        params={"sort": "displayed_activity_score"},
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    names = {item["name"] for item in items}
    assert names == {"Other", "Rebal"}


# ---------------------------------------------------------------------------
# Page envelope + creator_is_owner LEFT JOIN.
# ---------------------------------------------------------------------------


async def test_score_endpoint_page_envelope_shape(client, db) -> None:
    """AC-1: response carries `total`, `page`, `page_size`, `items`."""
    await _seed_many(db, 5, category="other")
    resp = client.get(
        "/api/agents/score",
        params={"category": "other", "sort": "displayed_activity_score"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body.keys()) >= {"items", "total", "page", "page_size"}
    assert body["total"] == 5
    assert body["page"] == 1
    assert isinstance(body["items"], list)
    assert len(body["items"]) == 5


async def test_score_endpoint_creator_is_owner_uses_left_join(client, db) -> None:
    """`creator_is_owner=True` flag row → response item reflects it via LEFT JOIN."""
    await _seed_agent(
        db,
        token_id=42,
        name="SoloCreator",
        activity_score=Decimal("80"),
        creator_is_owner=True,
    )
    resp = client.get(
        "/api/agents/score",
        params={"category": "other", "sort": "displayed_activity_score"},
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["creator_is_owner"] is True, (
        "LEFT JOIN to agent_compliance_flags creator_is_owner must surface"
    )


async def test_score_endpoint_creator_is_owner_defaults_false_when_flag_row_absent(client, db) -> None:
    """No `agent_compliance_flags` row → `creator_is_owner=False` (LEFT JOIN default)."""
    await _seed_agent(db, token_id=43, name="NoFlag", activity_score=Decimal("80"))
    resp = client.get(
        "/api/agents/score",
        params={"category": "other", "sort": "displayed_activity_score"},
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["creator_is_owner"] is False


# ---------------------------------------------------------------------------
# T2 — GREEN contract: ScoreOutMinimal round-trips JSON independently.
# ---------------------------------------------------------------------------


def test_score_out_minimal_roundtrips_json() -> None:
    """AC-3: ScoreOutMinimal serializes and re-validates without field drift."""
    from app.schemas.score import ScoreOutMinimal

    original = ScoreOutMinimal(
        chain=56,
        token=1,
        name="Alpha",
        activity_score=Decimal("72.50"),
        compliance_penalty=20.0,
        displayed_activity_score=52.5,
        creator_is_owner=True,
    )
    payload = original.model_dump_json()
    reloaded = ScoreOutMinimal.model_validate_json(payload)
    # Decimal→str coercion at the JSON boundary; re-validation keeps the Decimal.
    assert reloaded.chain == 56
    assert reloaded.token == 1
    assert reloaded.name == "Alpha"
    assert reloaded.activity_score == Decimal("72.50")
    assert reloaded.compliance_penalty == 20.0
    assert reloaded.displayed_activity_score == 52.5
    assert reloaded.creator_is_owner is True


# ---------------------------------------------------------------------------
# displayed_activity_score formula sanity.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("activity", "penalty", "expected"),
    [
        ("80.00", "20.00", 60.0),  # 80 - 20 = 60
        ("20.00", "30.00", 0.0),  # clip to zero
        ("85.00", "0.00", 85.0),  # clean agent
        ("100.00", "50.00", 50.0),  # max penalty
    ],
)
async def test_score_endpoint_displayed_formula(client, db, activity, penalty, expected) -> None:
    """`displayed_activity_score = max(0, activity_score - compliance_penalty)`."""
    await _seed_agent(
        db,
        token_id=70,
        name="Math",
        activity_score=Decimal(activity),
        compliance_penalty=float(penalty),
    )
    resp = client.get(
        "/api/agents/score",
        params={"category": "other", "sort": "displayed_activity_score"},
    )
    assert resp.status_code == 200, resp.text
    item = resp.json()["items"][0]
    assert item["displayed_activity_score"] == expected, (
        f"displayed formula failed: activity={activity}, penalty={penalty}, "
        f"expected {expected}, got {item['displayed_activity_score']!r}"
    )
