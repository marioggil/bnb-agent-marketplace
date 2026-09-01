"""Async client for the Termix Agent Card API.

Returns enriched agent metadata (status, presence, skills, displayName,
updatedAt) for agents on the Termix platform.  Used by the agent detail
page to supplement the on-chain / off-chain cache with live Termix data.

The API is simple enough that a single httpx.get with a short timeout
and no retries is appropriate — failures are non-blocking (the detail
page renders without the enrichment).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://platform-backend.prod.termix.live/api/v1/a2a/agents"
_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)


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
            return resp.json()
    except Exception:
        logger.debug("Termix card fetch failed for token_id=%s", token_id, exc_info=True)
        return None
