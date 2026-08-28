"""Multi-RPC load-balancing client for BSC.

Routes requests through Chainstack (primary, 1 RU/req) with automatic
fallback to Alchemy (secondary, CU-based). Tracks usage per provider
to stay within free-tier limits.

Usage:
    client = MultiRPCClient(chainstack_key="...", alchemy_key="...")
    result = await client.rpc_call("eth_getLogs", [...])
    await client.close()
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Provider configs: (name, url_template, monthly_limit, cost_per_request)
# Chainstack: 1 RU per request, 3M RU/month free tier
# Alchemy: CU-based, variable per method
PROVIDERS = {
    "chainstack": {
        "url_template": "https://bsc-mainnet.core.chainstack.com/{key}",
        "monthly_limit": 3_000_000,  # 3M RU/month
        "cost_per_request": 1,  # 1 RU per request
        "rps_limit": 25,
    },
    "alchemy": {
        "url_template": "https://bnb-mainnet.g.alchemy.com/v2/{key}",
        "monthly_limit": 30_000_000,  # 30M CU/month
        "cost_per_request": 1,  # variable, we track per-method
        "rps_limit": 15,
    },
}

# CU costs per method for Alchemy
ALCHEMY_CU = {
    "eth_blockNumber": 10,
    "eth_getBlockByNumber": 16,
    "eth_getLogs": 75,
    "eth_call": 26,
    "eth_getBalance": 16,
}


class MultiRPCClient:
    """Load-balancing RPC client with Chainstack primary + Alchemy fallback."""

    def __init__(self, chainstack_key: str = "", alchemy_key: str = ""):
        self._clients: dict[str, httpx.AsyncClient] = {}
        self._urls: dict[str, str] = {}
        self._usage: dict[str, int] = {"chainstack": 0, "alchemy": 0}
        self._cycle_usage: dict[str, int] = {"chainstack": 0, "alchemy": 0}
        self._last_request_time: dict[str, float] = {"chainstack": 0, "alchemy": 0}
        self._available: dict[str, bool] = {"chainstack": False, "alchemy": False}

        # Build URLs from keys
        if chainstack_key:
            self._urls["chainstack"] = PROVIDERS["chainstack"]["url_template"].format(key=chainstack_key)
            self._available["chainstack"] = True
        if alchemy_key:
            self._urls["alchemy"] = PROVIDERS["alchemy"]["url_template"].format(key=alchemy_key)
            self._available["alchemy"] = True

        # Determine primary (Chainstack if available, else Alchemy)
        self._primary = "chainstack" if self._available.get("chainstack") else "alchemy"
        self._fallback = "alchemy" if self._primary == "chainstack" else "chainstack"

        if self._primary == "chainstack":
            logger.info("RPC: Chainstack PRIMARY, Alchemy FALLBACK")
        elif self._primary == "alchemy":
            logger.info("RPC: Alchemy PRIMARY (no Chainstack key)")
        else:
            logger.warning("RPC: NO PROVIDERS CONFIGURED")

    async def _get_client(self, provider: str) -> httpx.AsyncClient:
        """Get or create a persistent httpx client for a provider."""
        if provider not in self._clients or self._clients[provider].is_closed:
            self._clients[provider] = httpx.AsyncClient(timeout=15.0)
        return self._clients[provider]

    async def close(self) -> None:
        """Close all httpx clients."""
        for client in self._clients.values():
            if not client.is_closed:
                await client.aclose()
        self._clients.clear()

    def _estimate_cu(self, method: str) -> int:
        """Estimate CU cost for an Alchemy request."""
        return ALCHEMY_CU.get(method, 10)  # default 10 CU

    async def rpc_call(
        self, method: str, params: list[Any], timeout: float = 15.0
    ) -> dict[str, Any] | None:
        """Make an RPC call with automatic provider selection and fallback.

        Strategy:
        1. Try primary provider
        2. On failure/rate-limit, try fallback
        3. Track usage per provider
        """
        providers_to_try = [self._primary, self._fallback] if self._fallback != self._primary else [self._primary]

        for provider in providers_to_try:
            if not self._available.get(provider):
                continue

            url = self._urls[provider]
            payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}

            try:
                client = await self._get_client(provider)
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()

                if "result" in data:
                    # Track usage
                    if provider == "chainstack":
                        self._usage[provider] += 1
                        self._cycle_usage[provider] += 1
                    else:
                        cu = self._estimate_cu(method)
                        self._usage[provider] += cu
                        self._cycle_usage[provider] += cu

                    return data

                if "error" in data:
                    logger.warning("RPC %s error: %s", provider, data["error"].get("message"))
                    # Don't fallback on RPC errors (contract errors, etc.)
                    return None

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    logger.warning("RPC %s rate limited (429), trying fallback", provider)
                    continue
                logger.error("RPC %s HTTP %d: %s", provider, e.response.status_code, e)
                continue
            except httpx.HTTPError as e:
                logger.error("RPC %s error: %s", provider, e)
                continue
            except Exception as e:
                logger.error("RPC %s unexpected error: %s", provider, e)
                continue

        logger.error("All RPC providers failed for %s", method)
        return None

    def get_usage_summary(self) -> dict[str, Any]:
        """Return current usage stats."""
        return {
            "primary": self._primary,
            "fallback": self._fallback,
            "usage": dict(self._usage),
            "cycle_usage": dict(self._cycle_usage),
            "available": dict(self._available),
        }

    def reset_cycle_usage(self) -> None:
        """Reset per-cycle usage counters."""
        self._cycle_usage = {"chainstack": 0, "alchemy": 0}
