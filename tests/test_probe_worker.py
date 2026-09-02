"""A2A probe worker tests (agent-score P1-P6, D1-D3/D10/D11).

Layers:
- Unit (respx-mocked HTTP, no DB): `probe_termix_card` status awareness
  (P3, D2, D11) and the 429 throttle (P4, D10).
- Integration (aiosqlite harness + respx): `_select_probeable` eligibility +
  chunking (P2, P4, D1), `_probe_cycle` history + materialization (P5, P6,
  D3), composite via agent_score (S2, D5).
- Lifespan (TestClient): P1 task creation + cancellation.

Spec: `sdd/agent-score/spec` agent-probes.
Design: D1 (pinned probe URL, JSONB eligibility only), D2 (status-aware,
no raise_for_status collapse), D3 (health_status MERGE), D10 (429 throttle),
D11 (timeouts).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import httpx
from fastapi.testclient import TestClient

TERMIX_CARD = "https://platform-backend.prod.termix.live/api/v1/a2a/agents/{}/card"


# ---------------------------------------------------------------------------
# P3/D2/D11 — probe_termix_card status awareness
# ---------------------------------------------------------------------------


async def test_probe_termix_card_200_responded(respx_mock):
    from app.services.client_termix import probe_termix_card

    respx_mock.get(TERMIX_CARD.format(601)).respond(
        200,
        json={"status": "BOUND", "presence": "online", "skills": ["a", "b"]},
    )
    result = await probe_termix_card(601)
    assert result["responded"] is True
    assert result["http_status"] == 200
    assert result["status"] == "BOUND"
    assert result["presence"] == "online"
    assert result["skills_count"] == 2
    assert result["error"] is None
    assert isinstance(result["latency_ms"], int) and result["latency_ms"] >= 0


async def test_probe_termix_card_401_is_responded_no_collapse(respx_mock):
    # P3 scenario "401 is responded": 401 must NOT be collapsed into a failure.
    from app.services.client_termix import probe_termix_card

    # 401 bodies are usually non-JSON; the probe still counts as responded.
    respx_mock.get(TERMIX_CARD.format(602)).respond(401, text="Unauthorized")
    result = await probe_termix_card(602)
    assert result["responded"] is True
    assert result["http_status"] == 401
    assert result["error"] is None
    assert result["latency_ms"] >= 0


async def test_probe_termix_card_404_not_responded(respx_mock):
    from app.services.client_termix import probe_termix_card

    respx_mock.get(TERMIX_CARD.format(603)).respond(404, text="not found")
    result = await probe_termix_card(603)
    assert result["responded"] is False
    assert result["http_status"] == 404
    assert result["error"] == "http_404"
    assert result["latency_ms"] >= 0


async def test_probe_termix_card_5xx_not_responded(respx_mock):
    from app.services.client_termix import probe_termix_card

    respx_mock.get(TERMIX_CARD.format(604)).respond(503, text="boom")
    result = await probe_termix_card(604)
    assert result["responded"] is False
    assert result["http_status"] == 503
    assert result["error"] == "http_503"


async def test_probe_termix_card_2xx_parse_error_not_responded(respx_mock):
    # P3: a parse error on a 2xx body counts as not responded, classified.
    from app.services.client_termix import probe_termix_card

    respx_mock.get(TERMIX_CARD.format(608)).respond(200, text="<html>not json</html>")
    result = await probe_termix_card(608)
    assert result["responded"] is False
    assert result["http_status"] == 200
    assert result["error"] == "parse_error"


async def test_probe_termix_card_connect_error_not_responded(respx_mock, monkeypatch):
    from app.services import client_termix
    from app.services.client_termix import probe_termix_card

    # The D10 fallback wait (60s) is unit-tested separately; patch it so the
    # retried ConnectError never actually sleeps.
    monkeypatch.setattr(client_termix, "_probe_wait", lambda _state: 0.0)
    respx_mock.get(TERMIX_CARD.format(605)).mock(side_effect=httpx.ConnectError("refused"))
    result = await probe_termix_card(605)
    assert result["responded"] is False
    assert result["http_status"] is None
    assert result["error"] == "connect_error"
    assert result["latency_ms"] >= 0


# ---------------------------------------------------------------------------
# P4/D10 — 429 throttle: 2 attempts max, Retry-After honored, else wait 60s
# ---------------------------------------------------------------------------


async def test_probe_429_retries_then_success(respx_mock):
    from app.services.client_termix import probe_termix_card

    route = respx_mock.get(TERMIX_CARD.format(606)).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}, text="slow down"),
            httpx.Response(200, json={"status": "BOUND", "presence": "online"}),
        ]
    )
    result = await probe_termix_card(606)
    assert route.call_count == 2  # the 429 triggered exactly one retry
    assert result["responded"] is True
    assert result["http_status"] == 200


async def test_probe_429_two_attempts_max_then_not_responded(respx_mock):
    from app.services.client_termix import probe_termix_card

    route = respx_mock.get(TERMIX_CARD.format(607)).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}, text="slow down"),
            httpx.Response(429, headers={"Retry-After": "0"}, text="still slow"),
        ]
    )
    result = await probe_termix_card(607)
    assert route.call_count == 2  # stop_after_attempt(2) caps a 429 storm
    assert result["responded"] is False
    assert result["http_status"] == 429
    assert result["error"] == "http_429"


class _FakeOutcome:
    def __init__(self, exc: BaseException | None) -> None:
        self._exc = exc

    def exception(self) -> BaseException | None:
        return self._exc


class _FakeRetryState:
    def __init__(self, exc: BaseException | None) -> None:
        self.outcome = _FakeOutcome(exc)


def _http_status_error(status: int, headers: dict[str, str] | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", TERMIX_CARD.format(1))
    response = httpx.Response(status, headers=headers or {}, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


def test_probe_wait_honors_retry_after_else_60s():
    from app.services.client_termix import _probe_wait

    # 429 with Retry-After → honored verbatim.
    assert _probe_wait(_FakeRetryState(_http_status_error(429, {"Retry-After": "7"}))) == 7.0
    # 429 without the header → fixed 60s (P4 "back off 60s").
    assert _probe_wait(_FakeRetryState(_http_status_error(429))) == 60.0
    # Non-429 retryable error (e.g. 500) → fixed 60s.
    assert _probe_wait(_FakeRetryState(_http_status_error(500))) == 60.0
    # Network error (no response to read a header from) → fixed 60s.
    assert _probe_wait(_FakeRetryState(httpx.ConnectError("refused"))) == 60.0


# ---------------------------------------------------------------------------
