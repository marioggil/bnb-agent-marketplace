"""Favorites API tests: happy, idempotent, unauth, list, delete.

Spec: `sdd/marketplace-scaffold-tests/spec` favorites-hires-tests R1-R4.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.db.models.agent import (
    BSC_CHAIN_ID,
    BSC_IDENTITY_REGISTRY,
    AgentCache,
    build_agent_id,
)
from app.db.models.favorite import Favorite
from app.services.auth import issue_csrf
from tests.conftest import _now, _sign_in


async def _seed(session, token_id: int) -> str:
    aid = build_agent_id(56, BSC_IDENTITY_REGISTRY, token_id)
    session.add(
        AgentCache(
            agent_id=aid,
            chain_id=BSC_CHAIN_ID,
            token_id=token_id,
            registry_address=BSC_IDENTITY_REGISTRY,
            name=f"A{token_id}",
            supported_protocols=[],
            cross_chain_versions=[],
            raw={},
            created_at=_now(),
            updated_at=_now(),
        )
    )
    await session.commit()
    return aid


def _ch(cookie: str) -> tuple[dict, dict]:
    return {"bnb_agent_session": cookie}, {"X-CSRF-Token": issue_csrf(cookie)}


# R1 — happy POST returns 201.
async def test_favorite_happy_path(client, db):
    address, cookie = _sign_in(client)
    aid = await _seed(db, 1)
    cookies, headers = _ch(cookie)
    r = client.post("/api/favorites", json={"agent_id": aid}, cookies=cookies, headers=headers)
    assert r.status_code == 201
    assert r.json()["address"].lower() == address.lower() and r.json()["agent_id"] == aid


# R2 — unauth + HTMX → HX-Redirect: /auth.
async def test_favorite_unauth_htmx_redirect(client):
    r = client.post(
        "/api/favorites",
        json={"agent_id": "56:0x8004A169FB4a3325136EB29fA0ceB6D2e539a432:1"},
        headers={"HX-Request": "true"},
    )
    assert r.headers.get("HX-Redirect") == "/auth"


# R2a — idempotent insert: re-POST returns 201, exactly one row.
# Counts via async `db` after TestClient writes; sqlite cannot coordinate
# the two connections, so it needs real Postgres.
@pytest.mark.postgres
async def test_favorite_idempotent_post(client, db):
    address, cookie = _sign_in(client)
    aid = await _seed(db, 1)
    cookies, headers = _ch(cookie)
    for _ in range(2):
        assert (
            client.post(
                "/api/favorites", json={"agent_id": aid}, cookies=cookies, headers=headers
            ).status_code
            == 201
        )
    count = await db.scalar(
        select(func.count())
        .select_from(Favorite)
        .where(Favorite.address == address, Favorite.agent_id == aid)
    )
    assert count == 1


@pytest.mark.postgres
async def test_favorite_idempotent_postgres(client, db):
    """Same scenario against `pg_insert.on_conflict_do_nothing`."""
    address, cookie = _sign_in(client)
    aid = await _seed(db, 1)
    cookies, headers = _ch(cookie)
    for _ in range(2):
        assert (
            client.post(
                "/api/favorites", json={"agent_id": aid}, cookies=cookies, headers=headers
            ).status_code
            == 201
        )
    count = await db.scalar(
        select(func.count())
        .select_from(Favorite)
        .where(Favorite.address == address, Favorite.agent_id == aid)
    )
    assert count == 1


# R3 — DELETE returns 204 and removes the row; not-owned → 404.
async def test_favorite_delete_and_not_owned(client, db):
    _other, _ = _sign_in(client)
    aid = await _seed(db, 1)
    address, cookie = _sign_in(client)
    cookies, headers = _ch(cookie)
    assert (
        client.post(
            "/api/favorites", json={"agent_id": aid}, cookies=cookies, headers=headers
        ).status_code
        == 201
    )
    assert (
        client.delete(f"/api/favorites/{aid}", cookies=cookies, headers=headers).status_code == 204
    )
    assert (
        await db.scalar(
            select(Favorite).where(Favorite.address == address, Favorite.agent_id == aid)
        )
        is None
    )
    # not-owned: a different user trying to delete a row they don't own.
    _me, my_cookie = _sign_in(client)
    cookies, headers = _ch(my_cookie)
    assert (
        client.delete(f"/api/favorites/{aid}", cookies=cookies, headers=headers).status_code == 404
    )


# R4 — GET /api/favorites returns only the caller's rows.
async def test_favorite_list_own(client, db):
    _, a_cookie = _sign_in(client)
    _, b_cookie = _sign_in(client)
    a_id = await _seed(db, 1)
    b_id = await _seed(db, 2)
    a_c, a_h = _ch(a_cookie)
    b_c, b_h = _ch(b_cookie)
    client.post("/api/favorites", json={"agent_id": a_id}, cookies=a_c, headers=a_h)
    client.post("/api/favorites", json={"agent_id": b_id}, cookies=b_c, headers=b_h)
    a_items = {f["agent_id"] for f in client.get("/api/favorites", cookies=a_c).json()}
    b_items = {f["agent_id"] for f in client.get("/api/favorites", cookies=b_c).json()}
    assert a_items == {a_id} and b_items == {b_id}
