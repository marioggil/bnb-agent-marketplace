"""Admin compliance endpoint — body-shape tests (Phase 1a-ii).

Spec: `openspec/changes/compliance-flags/spec.md` AC-10 + design §3.4. Tests
the body shape of the two admin endpoints with a valid `X-API-Key`:

- `POST /api/admin/compliance/refresh` invokes `run_compliance_refresh()` and
  serializes the `ComplianceRefreshReport` as JSON.
- `GET  /api/admin/compliance/status` returns
  `{last_refreshed_at, flagged_data_stale, row_count}`.

Auth contract is covered by `tests/test_admin_compliance.py` (per design
§6.R-1 split — auth-only file is ≤200 lines). Body-shape cases are mocked so
the DB content does not matter.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.services import compliance_refresh
from app.services.compliance_refresh import ComplianceRefreshReport

API_HEADERS = {"X-API-Key": "test-sync-api-key"}


@pytest.fixture
def sync_key(monkeypatch):
    """Set SYNC_API_KEY before the app fixture re-instantiates Settings."""
    monkeypatch.setenv("SYNC_API_KEY", "test-sync-api-key")
    return "test-sync-api-key"


# ---------------------------------------------------------------------------
# POST /api/admin/compliance/refresh — happy path
# ---------------------------------------------------------------------------


def test_refresh_success_returns_200_and_calls_orchestrator(sync_key, client):
    expected = ComplianceRefreshReport(
        sources_total_fetched=42,
        sources_total_inserted=40,
        agents_matched=7,
        agents_upserted=7,
        penalty_distribution={"0": 5, "30": 1, "50": 1},
        last_refreshed_at="2026-01-15T10:30:00+00:00",
    )

    with patch.object(
        compliance_refresh,
        "run_compliance_refresh",
        AsyncMock(return_value=expected),
    ) as mock_run:
        response = client.post("/api/admin/compliance/refresh", headers=API_HEADERS)

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == expected.as_dict()
    assert mock_run.await_count == 1


# ---------------------------------------------------------------------------
# GET /api/admin/compliance/status — body shape
# ---------------------------------------------------------------------------


def test_status_returns_last_refreshed_at_and_stale_flag(sync_key, client):
    fake_summary = (
        datetime(2026, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
        3,
    )

    with patch.object(
        compliance_refresh, "status_summary", AsyncMock(return_value=fake_summary)
    ), patch.object(
        compliance_refresh, "flagged_data_stale", AsyncMock(return_value=False)
    ):
        response = client.get("/api/admin/compliance/status", headers=API_HEADERS)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["last_refreshed_at"] == "2026-01-15T10:30:00+00:00"
    assert body["flagged_data_stale"] is False
    assert body["row_count"] == 3


def test_status_empty_mirror_returns_null_and_stale_true(sync_key, client):
    """Empty DB → `last_refreshed_at=None`, `flagged_data_stale=True`,
    `row_count=0`."""
    with patch.object(
        compliance_refresh,
        "status_summary",
        AsyncMock(return_value=(None, 0)),
    ), patch.object(
        compliance_refresh, "flagged_data_stale", AsyncMock(return_value=True)
    ):
        response = client.get("/api/admin/compliance/status", headers=API_HEADERS)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["last_refreshed_at"] is None
    assert body["flagged_data_stale"] is True
    assert body["row_count"] == 0
