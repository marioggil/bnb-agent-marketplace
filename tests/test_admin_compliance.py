"""Admin compliance endpoint — auth-only tests (Phase 1a-ii)."""

from __future__ import annotations

import pytest

API_HEADERS = {"X-API-Key": "test-sync-api-key"}


@pytest.fixture
def sync_key(monkeypatch):
    monkeypatch.setenv("SYNC_API_KEY", "test-sync-api-key")
    return "test-sync-api-key"


def test_refresh_endpoint_missing_api_key_returns_401(sync_key, client):
    response = client.post("/api/admin/compliance/refresh")
    assert response.status_code == 401


def test_refresh_endpoint_wrong_api_key_returns_401(sync_key, client):
    response = client.post(
        "/api/admin/compliance/refresh", headers={"X-API-Key": "wrong-key"}
    )
    assert response.status_code == 401


def test_refresh_endpoint_unconfigured_key_returns_503(client, monkeypatch):
    monkeypatch.delenv("SYNC_API_KEY", raising=False)
    from app.config import _settings_cache
    _settings_cache.cache_clear()
    response = client.post("/api/admin/compliance/refresh", headers=API_HEADERS)
    assert response.status_code == 503


def test_status_endpoint_missing_api_key_returns_401(sync_key, client):
    response = client.get("/api/admin/compliance/status")
    assert response.status_code == 401


def test_status_endpoint_wrong_api_key_returns_401(sync_key, client):
    response = client.get(
        "/api/admin/compliance/status", headers={"X-API-Key": "wrong-key"}
    )
    assert response.status_code == 401


def test_status_endpoint_unconfigured_key_returns_503(client, monkeypatch):
    monkeypatch.delenv("SYNC_API_KEY", raising=False)
    from app.config import _settings_cache
    _settings_cache.cache_clear()
    response = client.get("/api/admin/compliance/status", headers=API_HEADERS)
    assert response.status_code == 503
