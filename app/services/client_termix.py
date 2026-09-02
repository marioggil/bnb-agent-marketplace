"""Async client for the Termix Agent Card API.

Returns enriched agent metadata (status, presence, skills, displayName,
updatedAt) for agents on the Termix platform.  Used by the agent detail
page to supplement the on-chain / off-chain cache with live Termix data.

The API is simple enough that a single httpx.get with a short timeout
and no retries is appropriate — failures are non-blocking (the detail
page renders without the enrichment).

`probe_termix_card` is the A2A probe worker's status-aware entry point
(agent-score P3): unlike `fetch_termix_card` it never collapses statuses
with `raise_for_status`, records latency, and classifies failures so the
worker can materialize health columns.
"""

from __future__ import annotations

import logging
import time
from typing import Any, cast

import httpx
from tenacity import AsyncRetrying, RetryCallState, retry_if_exception_type, stop_after_attempt

from app.services.client_8004scan import _retry_after_seconds

logger = logging.getLogger(__name__)

_BASE_URL = "https://platform-backend.prod.termix.live/api/v1/a2a/agents"
_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)

#: Max card-GET attempts when the upstream answers 429 (D10).
_PROBE_MAX_ATTEMPTS = 2
#: Fallback backoff when a 429 carries no Retry-After header (P4 "back off 60s").
_PROBE_RATE_LIMIT_WAIT_S = 60.0


def _probe_wait(retry_state: RetryCallState) -> float:
    """Wait before the next probe attempt (P4, D10).

    Honors `Retry-After` on 429 responses; every other retryable failure
    (or a 429 without the header) falls back to the fixed 60s backoff.
    """
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
        wait = _retry_after_seconds(exc.response)
        if wait is not None:
            return wait
    return _PROBE_RATE_LIMIT_WAIT_S


def _parse_json(response: httpx.Response) -> dict[str, Any] | None:
    """Best-effort JSON-object parse of the card body (D2)."""
    try:
        data = response.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _skills_count(payload: dict[str, Any]) -> int | None:
    """Count the card's `skills` array; None when the key is absent/not a list."""
    skills = payload.get("skills")
    return len(skills) if isinstance(skills, list) else None


def _not_responded(http_status: int | None, latency_ms: int, error: str) -> dict[str, Any]:
    """The failure verdict — status-aware, never raises (D2)."""
    return {
        "responded": False,
        "http_status": http_status,
        "latency_ms": latency_ms,
        "status": None,
        "presence": None,
        "skills_count": None,
        "error": error,
    }


def _classify(response: httpx.Response, latency_ms: int) -> dict[str, Any]:
    """Map a card response to the probe verdict (P3, D2).

    2xx/401 → responded (a 401 body is usually non-JSON and must not flip
    the verdict); 404/5xx → not responded with `http_<status>`; a 2xx body
    that fails to parse → not responded with `parse_error`.
    """
    status = response.status_code
    if status == 401 or 200 <= status < 300:
        payload = _parse_json(response)
        if payload is None and status != 401:
            return _not_responded(status, latency_ms, "parse_error")
        return {
            "responded": True,
            "http_status": status,
            "latency_ms": latency_ms,
            "status": payload.get("status") if payload else None,
            "presence": payload.get("presence") if payload else None,
            "skills_count": _skills_count(payload) if payload else None,
            "error": None,
        }
    return _not_responded(status, latency_ms, f"http_{status}")


async def fetch_termix_card(token_id: int) -> dict[str, Any] | None:
    """Fetch the Termix agent card for *token_id*.

    Returns the parsed JSON dict on success, ``None`` on any failure
    (network, timeout, non-200 status, parse error).  Callers treat
    ``None`` as "no enrichment available" — the page still renders.
    """
    url = f"{_BASE_URL}/{token_id}/card"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return cast(dict[str, Any], resp.json())
    except Exception:
        logger.debug("Termix card fetch failed for token_id=%s", token_id, exc_info=True)
        return None


async def probe_termix_card(token_id: int) -> dict[str, Any]:
    """Status-aware probe of the Termix agent card (P3, D2, D11).

    Unlike `fetch_termix_card`, this never collapses statuses: 2xx/401
    count as responded, 404/5xx/timeout/parse errors count as not responded
    with a classified ``error``. Latency is measured per probe. The card GET
    retries once on 429 (P4, D10), honoring `Retry-After`, capped at 2
    attempts.

    Returns a dict with ``responded, http_status, latency_ms, status,
    presence, skills_count, error`` — always a dict, never raises for
    HTTP/network outcomes.
    """
    url = f"{_BASE_URL}/{token_id}/card"
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(_PROBE_MAX_ATTEMPTS),
                wait=_probe_wait,
                retry=retry_if_exception_type(httpx.HTTPError),
                reraise=True,
            ):
                with attempt:
                    response = await client.get(url)
                    if response.status_code == 429:
                        # 429 is retried once (Retry-After honored by `_probe_wait`).
                        response.raise_for_status()
                    return _classify(response, int((time.monotonic() - started) * 1000))
    except httpx.HTTPStatusError as exc:
        # The 2-attempt budget was exhausted (429 storm, D10); no card served.
        return _not_responded(
            exc.response.status_code,
            int((time.monotonic() - started) * 1000),
            f"http_{exc.response.status_code}",
        )
    except httpx.TimeoutException:
        return _not_responded(None, int((time.monotonic() - started) * 1000), "timeout")
    except httpx.NetworkError:
        return _not_responded(None, int((time.monotonic() - started) * 1000), "connect_error")
    # AsyncRetrying never exhausts its iterator normally — every attempt
    # either returns or raises; this guard exists for mypy's termination view.
    raise AssertionError("unreachable")  # pragma: no cover
