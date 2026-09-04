"""Agent-feedbacks tests: sync service (pagination, idempotency, score
normalization), the /agents/{chain}/{token}/feedbacks endpoint (HTMX
partial + JSON), and the collapsible reviews panel on the detail page.

The upstream /api/v1/feedbacks endpoint (plain v1, NOT /api/v1/public) is
mocked with respx. Harness rule: after TestClient activity the `db` fixture
is NOT read — persistence is verified through public endpoints instead
(ResourceWarning contamination; same pattern as test_flagged.py).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from app.db.models.agent_feedback import AgentFeedback
from app.services.feedback_sync import FEEDBACKS_API, sync_agent_feedbacks

BASE = "https://8004scan.io/api/v1/public"
AGENT_ID = "56:0x8004A169FB4a3325136EB29fA0ceB6D2e539a432:137"


def _feedback_payload(feedback_id: str, *, comment: str = "Nice", **overrides) -> dict:
    """One upstream feedback payload (documented item shape)."""
    payload = {
        "agent_id": "986bff00-0000-0000-0000-000000000000",
        "score": None,
        "value": "100",
        "value_decimals": "0",
        "comment": comment,
        "id": "6adc10a0-0000-0000-0000-000000000000",
        "chain_id": 56,
        "is_testnet": False,
        "feedback_id": feedback_id,
        "transaction_hash": "0x" + "e8" * 32,
        "block_number": 80259171,
        "user_id": "ace19d6f-0000-0000-0000-000000000000",
        "user_address": "0x" + "69" * 20,
        "agent": {"token_id": "137", "chain_id": 56},
        "user": {"address": "0x" + "69" * 20, "ens": None, "username": None},
        "tag1": "giant",
        "tag2": "great",
        "feedback_index": 1,
        "feedback_hash": "0x" + "65" * 32,
        "is_revoked": False,
        "revoked_at": None,
        "submitted_at": "2026-02-09T18:37:23Z",
        "created_at": "2026-02-09T19:31:25.325875Z",
        "updated_at": "2026-02-09T19:31:25.325875Z",
    }
    payload.update(overrides)
    return payload


async def _seed_agent(db, token_id: int = 137, name: str | None = None) -> str:
    from app.db.models.agent import (
        BSC_CHAIN_ID,
        BSC_IDENTITY_REGISTRY,
        AgentCache,
        build_agent_id,
    )
    from tests.conftest import _now

    aid = build_agent_id(56, BSC_IDENTITY_REGISTRY, token_id)
    db.add(
        AgentCache(
            agent_id=aid,
            chain_id=BSC_CHAIN_ID,
            token_id=token_id,
            registry_address=BSC_IDENTITY_REGISTRY,
            name=name or f"Agent {token_id}",
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


async def _seed_feedback(
    db,
    feedback_id: str,
    agent_id: str = AGENT_ID,
    *,
    comment: str = "Nice",
    score: int | None = None,
    submitted_at: datetime | None = None,
    user_address: str | None = "0x" + "69" * 20,
    tag1: str | None = None,
    tag2: str | None = None,
    is_revoked: bool = False,
    tx_hash: str | None = None,
) -> None:
    from tests.conftest import _now

    db.add(
        AgentFeedback(
            feedback_id=feedback_id,
            agent_id=agent_id,
            chain_id=56,
            token_id=137,
            user_address=user_address,
            score=score,
            comment=comment,
            tag1=tag1,
            tag2=tag2,
            tx_hash=tx_hash,
            submitted_at=submitted_at or _now(),
            is_revoked=is_revoked,
            created_at=_now(),
            updated_at=_now(),
        )
    )
    await db.commit()


# ---------------------------------------------------------------------------
# sync_agent_feedbacks — pagination, idempotency, normalization
# ---------------------------------------------------------------------------


async def test_sync_fetches_pages_and_upserts(db, respx_mock):
    await _seed_agent(db, 137)
    route = respx_mock.get(FEEDBACKS_API)
    # total=150 (> one 100-item page) forces a second fetch at offset=100;
    # after it, offset=200 >= total → stop.
    route.side_effect = [
        httpx.Response(
            200,
            json={
                "items": [
                    _feedback_payload("56:137:0xaa:1", comment="first page"),
                    _feedback_payload("56:137:0xaa:2", comment="second on page 1"),
                ],
                "total": 150,
                "limit": 100,
                "offset": 0,
            },
        ),
        httpx.Response(
            200,
            json={
                "items": [_feedback_payload("56:137:0xaa:3", comment="page two")],
                "total": 150,
                "limit": 100,
                "offset": 100,
            },
        ),
    ]

    upserted = await sync_agent_feedbacks(db, AGENT_ID, 56, 137)

    assert upserted == 3
    # Pagination walked offsets 0 then 100, stopping at offset >= total.
    offsets = [c.request.url.params["offset"] for c in route.calls]
    assert offsets == ["0", "100"]
    from sqlalchemy import select

    rows = (await db.scalars(select(AgentFeedback))).all()
    assert len(rows) == 3
    assert {r.feedback_id for r in rows} == {
        "56:137:0xaa:1",
        "56:137:0xaa:2",
        "56:137:0xaa:3",
    }
    assert rows[0].agent_id == AGENT_ID and rows[0].chain_id == 56 and rows[0].token_id == 137


async def test_sync_idempotent_rerun_updates_not_duplicates(db, respx_mock):
    await _seed_agent(db, 137)
    payload = _feedback_payload("56:137:0xaa:1", comment="v1")
    route = respx_mock.get(FEEDBACKS_API)
    route.side_effect = [
        httpx.Response(200, json={"items": [payload], "total": 1, "limit": 100, "offset": 0}),
    ]
    assert await sync_agent_feedbacks(db, AGENT_ID, 56, 137) == 1

    # Same feedback_id, updated comment — second run must UPDATE, not insert.
    route.side_effect = [
        httpx.Response(
            200,
            json={
                "items": [_feedback_payload("56:137:0xaa:1", comment="v2")],
                "total": 1,
                "limit": 100,
                "offset": 0,
            },
        ),
    ]
    assert await sync_agent_feedbacks(db, AGENT_ID, 56, 137) == 1

    from sqlalchemy import func, select

    assert await db.scalar(select(func.count()).select_from(AgentFeedback)) == 1
    row = await db.scalar(select(AgentFeedback).where(AgentFeedback.feedback_id == "56:137:0xaa:1"))
    assert row is not None and row.comment == "v2"


async def test_sync_normalizes_score_and_submitted_at(db, respx_mock):
    await _seed_agent(db, 137)
    respx_mock.get(FEEDBACKS_API).respond(
        200,
        json={
            "items": [
                # value_decimals == 0 → integer score.
                _feedback_payload(
                    "56:137:0xaa:1",
                    value="100",
                    value_decimals="0",
                    submitted_at="2026-02-09T18:37:23Z",
                ),
                # value_decimals != 0 → score must be None (lossy int cast).
                _feedback_payload(
                    "56:137:0xaa:2",
                    value="99",
                    value_decimals="2",
                    submitted_at="2026-02-08T10:00:00Z",
                ),
                # Missing value → score None.
                _feedback_payload("56:137:0xaa:3", value=None, value_decimals=None),
            ],
            "total": 3,
            "limit": 100,
            "offset": 0,
        },
    )

    await sync_agent_feedbacks(db, AGENT_ID, 56, 137)

    from sqlalchemy import select

    rows = {r.feedback_id: r for r in (await db.scalars(select(AgentFeedback))).all()}
    assert rows["56:137:0xaa:1"].score == 100
    assert rows["56:137:0xaa:2"].score is None
    assert rows["56:137:0xaa:3"].score is None
    # ISO "Z" timestamps parse as timezone-aware UTC datetimes.
    assert rows["56:137:0xaa:1"].submitted_at == datetime(
        2026, 2, 9, 18, 37, 23, tzinfo=timezone.utc
    )
    assert rows["56:137:0xaa:1"].user_address == "0x" + "69" * 20
    assert rows["56:137:0xaa:1"].tx_hash == "0x" + "e8" * 32
    assert rows["56:137:0xaa:1"].tag1 == "giant" and rows["56:137:0xaa:1"].tag2 == "great"


async def test_sync_404_returns_zero(db, respx_mock):
    await _seed_agent(db, 137)
    respx_mock.get(FEEDBACKS_API).respond(404)
    assert await sync_agent_feedbacks(db, AGENT_ID, 56, 137) == 0


# ---------------------------------------------------------------------------
# Worker wiring — a feedback sync failure must never fail the agent sync
# ---------------------------------------------------------------------------


async def test_worker_feedback_failure_keeps_agent(db, respx_mock, monkeypatch):
    from app.services.client_8004scan import Client8004Scan
    from app.services.sync_worker import _enrich_and_upsert

    monkeypatch.setattr("app.services.feedback_sync._RETRY_BACKOFF_S", 0)

    # Upstream detail reports 3 feedbacks → the worker must attempt the sync.
    respx_mock.get(f"{BASE}/agents/56/137").respond(
        200,
        json={
            "agent_id": AGENT_ID,
            "chain_id": 56,
            "token_id": 137,
            "registry": "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432",
            "name": "With Reviews",
            "x402_supported": False,
            "supported_protocols": [],
            "total_feedbacks": 3,
        },
    )
    # The feedbacks endpoint keeps failing past the retry budget.
    fb_route = respx_mock.get(FEEDBACKS_API)
    fb_route.side_effect = [httpx.Response(500), httpx.Response(500), httpx.Response(500)]

    async with Client8004Scan() as client:
        upserted, failed, _last, _walked = await _enrich_and_upsert(db, client, [137], 0)

    assert upserted == 1 and failed == 0  # the agent survived
    assert len(fb_route.calls) == 3  # all retries were spent on feedbacks
    from sqlalchemy import func, select

    from app.db.models.agent import AgentCache

    row = await db.scalar(select(AgentCache).where(AgentCache.agent_id == AGENT_ID))
    assert row is not None and row.name == "With Reviews"
    assert await db.scalar(select(func.count()).select_from(AgentFeedback)) == 0


# ---------------------------------------------------------------------------
# GET /agents/{chain_id}/{token_id}/feedbacks — HTMX partial + JSON
# ---------------------------------------------------------------------------


async def _seed_agent_with_reviews(db, count: int, agent_id: str = AGENT_ID) -> None:
    await _seed_agent(db, 137)
    base = datetime(2026, 2, 6, tzinfo=timezone.utc)
    for i in range(count):
        await _seed_feedback(
            db,
            f"56:137:0xaa:{i + 1}",
            agent_id=agent_id,
            comment=f"review #{i + 1}",
            score=100 - i,
            submitted_at=base - timedelta(days=i),
            user_address="0x" + f"{69 + i:02x}" * 20,
        )


async def test_feedbacks_endpoint_json_shape(client, db):
    await _seed_agent_with_reviews(db, 6)

    body = client.get("/agents/56/137/feedbacks").json()

    assert body["total"] == 6 and body["offset"] == 0 and body["has_more"] is True
    assert len(body["items"]) == 5  # default limit
    # Newest first: the most recent review leads the page.
    assert body["items"][0]["comment"] == "review #1"
    assert body["items"][0]["score"] == 100
    assert body["items"][0]["feedback_id"] == "56:137:0xaa:1"
    assert body["items"][0]["chain_id"] == 56 and body["items"][0]["token_id"] == 137
    assert body["items"][0]["user_address"] == "0x" + "45" * 20


async def test_feedbacks_endpoint_pagination_and_validation(client, db):
    await _seed_agent_with_reviews(db, 6)

    last = client.get("/agents/56/137/feedbacks?offset=5").json()
    assert len(last["items"]) == 1 and last["has_more"] is False

    empty = client.get("/agents/56/137/feedbacks?offset=10").json()
    assert empty["items"] == [] and empty["has_more"] is False and empty["total"] == 6

    assert client.get("/agents/56/137/feedbacks?limit=21").status_code == 422
    assert client.get("/agents/56/137/feedbacks?limit=0").status_code == 422


async def test_feedbacks_endpoint_htmx_partial(client, db):
    await _seed_agent_with_reviews(db, 6)

    partial = client.get("/agents/56/137/feedbacks", headers={"HX-Request": "true"}).text

    assert "review #1" in partial  # first item comment
    assert "review #6" not in partial  # beyond the first page
    assert "Load more" in partial  # has_more → pagination button
    assert f"offset={5}" in partial  # next page offset
    assert "reviews-list" in partial
    assert "view tx" not in partial  # no tx_hash seeded → no link


async def test_feedbacks_endpoint_htmx_no_more_and_tx_link(client, db):
    await _seed_agent(db, 137)
    await _seed_feedback(
        db,
        "56:137:0xaa:1",
        comment="lonely review",
        tx_hash="0x" + "e8" * 32,
        is_revoked=True,
    )

    partial = client.get("/agents/56/137/feedbacks?offset=0", headers={"HX-Request": "true"}).text

    assert "lonely review" in partial
    assert "Load more" not in partial
    assert "view tx" in partial and "https://testnet.bscscan.com/tx/" in partial
    assert "revoked" in partial


async def test_feedbacks_endpoint_empty_state(client, db):
    await _seed_agent(db, 137)
    partial = client.get("/agents/56/137/feedbacks", headers={"HX-Request": "true"}).text
    assert "No reviews yet." in partial


# ---------------------------------------------------------------------------
# Detail page — collapsible reviews panel, hidden by default
# ---------------------------------------------------------------------------


async def test_detail_reviews_panel_hidden_by_default(client, db):
    await _seed_agent_with_reviews(db, 2)

    body = client.get("/agents/56/137").text

    assert '<details class="reviews-panel">' in body
    assert "Reviews (2)" in body
    assert 'hx-get="/agents/56/137/feedbacks?offset=0"' in body
    assert 'id="reviews-body"' in body
    # Hidden by default: no review comment leaks into the initial page.
    assert "review #1" not in body and "review #2" not in body


async def test_detail_reviews_zero_feedbacks(client, db):
    await _seed_agent(db, 137)
    body = client.get("/agents/56/137").text
    assert "Reviews (0)" in body
    assert '<details class="reviews-panel">' in body
