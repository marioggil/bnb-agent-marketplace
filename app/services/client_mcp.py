"""Async client for MCP server info endpoints.

Returns the MCP server definition (name, version, tools, registry links,
install commands, docs/dashboard URLs) for agents that expose an MCP
endpoint.  Used by the agent detail page to supplement the on-chain /
off-chain cache with live MCP metadata.

The API is simple enough that a single httpx.get with a short timeout
and no retries is appropriate — failures are non-blocking (the detail
page renders without the enrichment).
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)


async def fetch_mcp_info(endpoint: str) -> dict[str, Any] | None:
    """Fetch the MCP server info from *endpoint*.

    Returns the parsed JSON dict on success, ``None`` on any failure
    (network, timeout, non-200 status, parse error).  Callers treat
    ``None`` as "no enrichment available" — the page still renders.
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(endpoint)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict):
                return None
            return data
    except Exception:
        logger.debug("MCP info fetch failed for endpoint=%s", endpoint, exc_info=True)
        return None
