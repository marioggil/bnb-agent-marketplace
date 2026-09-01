"""Healthz endpoint test: liveness + DB ping.

Spec: `sdd/marketplace-scaffold-tests/spec` bootstrap-tests R1.
W5 (503 when DB engine is down) is OUT OF SCOPE for FU-1.
"""

from __future__ import annotations


def test_healthz_returns_ok_with_db_ping(client):
    """`GET /healthz` returns 200 with `{status: ok, db: ok}`."""
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok"}
