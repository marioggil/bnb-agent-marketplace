"""Async client for the EvoEvo Agent Registration API.

Returns the EIP-8004 registration record (name, description, services,
active status, registrations) for agents on the EvoEvo platform.  Used
by the agent detail page to supplement the on-chain / off-chain cache
with live EvoEvo data.

The API is simple enough that a single httpx.get with a short timeout
and no retries is appropriate — failures are non-blocking (the detail
page renders without the enrichment).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.evoevo.ai/agents"
_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)


async def fetch_evoevo_card(agent_id: int) -> dict[str, Any] | None:
    """Fetch the EvoEvo registration record for *agent_id*.

    Returns the parsed JSON dict on success, ``None`` on any failure
    (network, timeout, non-200 status, parse error).  Callers treat
    ``None`` as "no enrichment available" — the page still renders.
    """
    url = f"{_BASE_URL}/{agent_id}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict):
                return None
            return data
    except Exception:
        logger.debug("EvoEvo card fetch failed for agent_id=%s", agent_id, exc_info=True)
        return None
