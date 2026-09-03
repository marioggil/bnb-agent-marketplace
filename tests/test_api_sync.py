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


# --- POST /api/sync/agent/{chain}/{token} ----------------------------------

UPSTREAM_BASE = "https://8004scan.io/api/v1/public"


def _agent_payload(token_id: int, **overrides) -> dict:
    payload = {
        "id": f"00000000-0000-0000-0000-{token_id:012d}",
        "agent_id": f"56:0x8004A169FB4a3325136EB29fA0ceB6D2e539a432:{token_id}",
        "token_id": str(token_id),
        "chain_id": 56,
        "chain_type": "evm",
        "contract_address": "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432",
        "is_testnet": False,
        "name": f"Agent-{token_id}",
        "description": "single-agent sync test",
        "agent_wallet": "0x" + "77" * 20,
        "x402_supported": True,
        "supported_protocols": [],
        "tags": [],
        "categories": [],
    }
    payload.update(overrides)
    return payload


def test_agent_endpoint_without_key_returns_401(sync_key, client):
    response = client.post("/api/sync/agent/56/12345")
    assert response.status_code == 401


def test_agent_endpoint_non_bsc_chain_422(sync_key, client):
    response = client.post("/api/sync/agent/97/12345", headers=API_HEADERS)
    assert response.status_code == 422


async def test_agent_endpoint_not_found_upstream_404(sync_key, client, respx_mock):
    respx_mock.get(f"{UPSTREAM_BASE}/agents/56/12345").respond(404)
    response = client.post("/api/sync/agent/56/12345", headers=API_HEADERS)
    assert response.status_code == 404


async def test_agent_endpoint_upstream_error_502(sync_key, client, respx_mock):
    respx_mock.get(f"{UPSTREAM_BASE}/agents/56/12345").respond(500)
    response = client.post("/api/sync/agent/56/12345", headers=API_HEADERS)
    assert response.status_code == 502


async def test_agent_endpoint_upserts_row(sync_key, client, respx_mock):
    respx_mock.get(f"{UPSTREAM_BASE}/agents/56/12345").respond(
        200, json=_agent_payload(12345)
    )
    response = client.post("/api/sync/agent/56/12345", headers=API_HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["agent_id"] == "56:0x8004A169FB4a3325136EB29fA0ceB6D2e539a432:12345"
    assert body["name"] == "Agent-12345"
    assert body["x402_supported"] is True
    assert body["wallet"] == "0x" + "77" * 20
    # Category: sqlite tests pin the column default to 'other'; Postgres
    # computes 'rebalancing' via the GENERATED expression for x402 rows.
    assert body["category"] in {"rebalancing", "other"}

    # Persistence is verified through the public detail endpoint (same loop
    # as the TestClient) — reading with the `db` fixture after TestClient
    # activity leaves aiosqlite connections open (ResourceWarning).
    fetch = client.get("/api/agents/56/12345")
    assert fetch.status_code == 200, fetch.text
    assert fetch.json()["name"] == "Agent-12345"
