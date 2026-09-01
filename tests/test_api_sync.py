"""Sync API endpoint tests: key auth, mode dispatch, lock, status.

The worker functions are monkeypatched — no upstream network calls and no
real sync DB writes. `SyncState` reads are exercised against the shared
aiosqlite test engine via the `db` fixture.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app.config import _settings_cache
from app.db.models.sync_state import SyncState
from app.routers import sync as sync_router

API_HEADERS = {"X-API-Key": "test-sync-api-key"}


@pytest.fixture
def sync_key(monkeypatch):
    """Set SYNC_API_KEY before the app fixture re-instantiates Settings."""
    monkeypatch.setenv("SYNC_API_KEY", "test-sync-api-key")
    return "test-sync-api-key"


# --- auth ------------------------------------------------------------------


def test_post_without_key_returns_401(sync_key, client):
    response = client.post("/api/sync", json={"mode": "incremental"})
    assert response.status_code == 401


def test_post_with_wrong_key_returns_401(sync_key, client):
    response = client.post(
        "/api/sync", headers={"X-API-Key": "wrong-key"}, json={"mode": "incremental"}
    )
    assert response.status_code == 401


def test_status_requires_key(sync_key, client):
    response = client.get("/api/sync/status")
    assert response.status_code == 401


def test_unconfigured_key_returns_503(client, monkeypatch):
    monkeypatch.delenv("SYNC_API_KEY", raising=False)
    _settings_cache.cache_clear()
    response = client.post("/api/sync", json={"mode": "incremental"})
    assert response.status_code == 503


# --- POST /api/sync --------------------------------------------------------


def test_post_incremental_returns_202(sync_key, client, monkeypatch):
    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(sync_router, "sync_incremental", noop)
    response = client.post("/api/sync", headers=API_HEADERS, json={"mode": "incremental"})
    assert response.status_code == 202
    assert response.json() == {"status": "started", "mode": "incremental"}


def test_post_defaults_to_incremental(sync_key, client, monkeypatch):
    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(sync_router, "sync_incremental", noop)
    response = client.post("/api/sync", headers=API_HEADERS)
    assert response.status_code == 202
    assert response.json() == {"status": "started", "mode": "incremental"}


def test_post_full_returns_202(sync_key, client, monkeypatch):
    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(sync_router, "sync_full", noop)
    response = client.post("/api/sync", headers=API_HEADERS, json={"mode": "full"})
    assert response.status_code == 202
    assert response.json() == {"status": "started", "mode": "full"}


def test_post_invalid_mode_returns_422(sync_key, client):
    response = client.post("/api/sync", headers=API_HEADERS, json={"mode": "bogus"})
    assert response.status_code == 422


def test_second_post_while_running_returns_409(sync_key, client, monkeypatch):
    async def stuck(*args, **kwargs):
        await asyncio.sleep(2)

    monkeypatch.setattr(sync_router, "sync_incremental", stuck)
    first = client.post("/api/sync", headers=API_HEADERS, json={"mode": "incremental"})
    assert first.status_code == 202
    second = client.post("/api/sync", headers=API_HEADERS, json={"mode": "incremental"})
    assert second.status_code == 409


# --- GET /api/sync/status --------------------------------------------------


def test_status_defaults_without_state_row(sync_key, client):
    response = client.get("/api/sync/status", headers=API_HEADERS)
    assert response.status_code == 200
    assert response.json() == {
        "running": False,
        "last_token_id": None,
        "last_sync_at": None,
        "failed_count": 0,
    }


async def test_status_returns_checkpoint(sync_key, client, db):
    db.add(
        SyncState(
            id=1,
            last_token_id=42,
            last_sync_at=datetime.now(timezone.utc),
            failed_token_ids=[1, 2, 3],
        )
    )
    await db.commit()
    response = client.get("/api/sync/status", headers=API_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["running"] is False
    assert body["last_token_id"] == 42
    assert body["last_sync_at"] is not None
    assert body["failed_count"] == 3
