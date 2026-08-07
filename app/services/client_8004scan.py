"""Async client for the 8004scan public API.

See `sdd/marketplace-scaffold/spec/agents-cache` (#19) for R6-R8 and the
design's service-contract table (#26).

Highlights:
- Single `httpx.AsyncClient` per instance, lifetime bound to the async
  context manager.
- Per-host `asyncio.Semaphore(4)` so we stay under the Pro tier 500 rpm
  budget (spec R6 + design Q7).
- tenacity retry: 429 honors `Retry-After`; 5xx exponential; 4xx other
  than 429 aborts.
- `get_agent(...)` returns `None` on 404 (R7) — IdentityRegistry BSC has
  no `totalSupply` so token-id gaps are normal.
- Pydantic models use `extra="allow"` and a catch-all `raw: dict` so
  upstream field drift (R8) lands in JSONB without breaking the app.
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    wait_random_exponential,
)

logger = logging.getLogger(__name__)


#: Default upstream base URL. Override via `8004SCAN_BASE`.
DEFAULT_BASE_URL: str = "https://8004scan.io/api/v1/public"
#: Default concurrency cap per host. Spec R6 — Pro tier is 500 rpm.
DEFAULT_MAX_CONCURRENCY: int = 4
#: Per-request timeouts (connect, read, write, pool).
_TIMEOUT: httpx.Timeout = httpx.Timeout(
    connect=5.0, read=30.0, write=10.0, pool=5.0
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class UpstreamError(Exception):
    """Base class for 8004scan client errors."""


class UpstreamRateLimit(UpstreamError):
    """Raised when 429 persists past retries or when the response is missing
    the `Retry-After` header that would let us wait it out."""


class UpstreamUnavailable(UpstreamError):
    """Raised when 5xx persists past the retry budget."""


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class StatsResponse(BaseModel):
    """Subset of the `/stats` payload we currently consume."""

    model_config = ConfigDict(extra="allow")

    total_agents: int | None = None
    total_feedbacks: int | None = None
    total_chains: int | None = None


class AgentResponse(BaseModel):
    """Single 8004scan agent record.

    `extra="allow"` and `raw: dict` together implement spec R8: unknown
    upstream fields land in `raw` and survive round-trip to the DB.
    """

    model_config = ConfigDict(extra="allow")

    agent_id: str | None = None
    chain_id: int | None = None
    token_id: int | None = None
    registry: str | None = None
    owner: str | None = None
    name: str | None = None
    description: str | None = None
    image: str | None = None
    image_url: str | None = None
    x402_supported: bool = False
    supported_protocols: list[str] = Field(default_factory=list)
    average_score: float | None = None
    total_feedbacks: int | None = None
    is_verified: bool = False
    cross_chain_versions: list[dict[str, Any]] = Field(default_factory=list)

    # Catch-all for fields we don't model explicitly. Populated from
    # __pydantic_extra__ at construction time so unknown upstream fields
    # don't get lost.
    raw: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Retry helpers
# ---------------------------------------------------------------------------


def _retry_after_seconds(response: httpx.Response | None) -> float | None:
    """Parse `Retry-After` honoring the second form."""
    if response is None:
        return None
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _is_retryable_status(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {429, 500, 502, 503, 504}
    return isinstance(exc, (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError))


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class Client8004Scan:
    """Async context manager wrapping a single httpx client."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    ) -> None:
        self.base_url = (base_url or os.environ.get("8004SCAN_BASE") or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = (
            api_key if api_key is not None else os.environ.get("8004SCAN_API_KEY")
        )
        if not self.api_key:
            logger.warning(
                "8004SCAN_API_KEY not set; rate limit ~50 rpm (Pro tier 500 rpm)"
            )
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._client: httpx.AsyncClient | None = None

    # -- context manager -------------------------------------------------

    async def __aenter__(self) -> "Client8004Scan":
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=_TIMEOUT,
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- internals -------------------------------------------------------

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError(
                "Client8004Scan used outside `async with`; "
                "wrap calls in `async with Client8004Scan(...) as client:`"
            )
        return self._client

    async def _request_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET `path` with retry/backoff. Raises on non-recoverable 4xx and
        on exhausted 5xx/429 budgets."""
        client = self._require_client()

        def _wait_strategy(retry_state: RetryCallState) -> float:
            # If the failing attempt was a 429 with Retry-After, honor it.
            exc = retry_state.outcome.exception() if retry_state.outcome else None
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
                wait = _retry_after_seconds(exc.response)
                if wait is not None:
                    return wait
            # Otherwise exponential with jitter, capped at 30s.
            return min(30.0, wait_random_exponential(0.5, 8.0)(retry_state))

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=_wait_strategy,  # type: ignore[arg-type]
                retry=retry_if_exception_type((httpx.HTTPError,)),
                reraise=True,
            ):
                with attempt:
                    async with self._semaphore:
                        response = await client.get(path, params=params or {})
                    if response.status_code == 404:
                        return None
                    if response.status_code == 429:
                        # Will be retried by tenacity.
                        response.raise_for_status()
                    if 500 <= response.status_code < 600:
                        # Will be retried by tenacity.
                        response.raise_for_status()
                    if 400 <= response.status_code < 500:
                        # Other 4xx are not recoverable; surface as-is.
                        response.raise_for_status()
                    return response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 429:
                raise UpstreamRateLimit(str(exc)) from exc
            if 500 <= status < 600:
                raise UpstreamUnavailable(str(exc)) from exc
            raise
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
            raise UpstreamUnavailable(str(exc)) from exc

    @staticmethod
    def _coerce_agent(payload: dict[str, Any]) -> AgentResponse:
        """Build an AgentResponse, putting unknown fields into `raw`."""
        known = set(AgentResponse.model_fields.keys()) - {"raw"}
        raw = {k: v for k, v in payload.items() if k not in known}
        return AgentResponse(**{k: v for k, v in payload.items() if k in known}, raw=raw)

    # -- public API ------------------------------------------------------

    async def get_stats(self) -> StatsResponse:
        data = await self._request_json("/stats")
        if data is None:
            # 404 on /stats is implausible; treat as empty stats.
            return StatsResponse()
        if isinstance(data, dict):
            return StatsResponse(**data)
        # Some upstreams return a top-level list. Coerce defensively.
        return StatsResponse.model_validate({"items": data})  # type: ignore[arg-type]

    async def iter_agents(
        self,
        chain_id: int = 56,
        page_size: int = 200,
    ) -> AsyncIterator[AgentResponse]:
        """Yield agents, filtering client-side to `chain_id` (R2).

        Paginates by appending `?page=N&page_size=page_size` until the
        upstream returns an empty page or we hit a hard stop. The 8004scan
        API is not strict on `chain_id` (id 11) so the client filter is
        mandatory.
        """
        page = 1
        while True:
            data = await self._request_json(
                "/agents",
                params={"chain_id": chain_id, "page": page, "page_size": page_size},
            )
            if not data:
                return
            # Upstream shape may be a list or {"items": [...]}; accept both.
            items: list[dict[str, Any]]
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict) and isinstance(data.get("items"), list):
                items = data["items"]
            elif isinstance(data, dict) and isinstance(data.get("agents"), list):
                items = data["agents"]
            else:
                # Unknown shape — yield nothing this page and stop.
                return
            if not items:
                return
            for payload in items:
                if not isinstance(payload, dict):
                    continue
                # Client-side chain filter (R2).
                row_chain = payload.get("chain_id")
                try:
                    if row_chain is not None and int(row_chain) != int(chain_id):
                        continue
                except (TypeError, ValueError):
                    continue
                yield self._coerce_agent(payload)
            page += 1
            if page > 10_000:  # safety stop — ~2M rows
                return

    async def get_agent(self, chain_id: int, token_id: int) -> AgentResponse | None:
        """Fetch a single agent. Returns `None` on 404 (R7)."""
        data = await self._request_json(
            f"/agents/{int(chain_id)}/{int(token_id)}"
        )
        if data is None:
            return None
        if isinstance(data, dict):
            return self._coerce_agent(data)
        # Unexpected shape — be defensive and return None rather than crash.
        logger.warning("get_agent(%s, %s): unexpected payload shape", chain_id, token_id)
        return None
