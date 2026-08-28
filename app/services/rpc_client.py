"""Multi-RPC load-balancing client for BSC.

Routes requests through Chainstack (primary, 1 RU/req) with automatic
fallback to Alchemy (secondary, CU-based). Tracks usage per provider
and manages provider health with cooldown on persistent errors.

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

# Provider configs
PROVIDERS = {
    "chainstack": {
        "url_template": "https://bsc-mainnet.core.chainstack.com/{key}",
        "monthly_limit": 3_000_000,
        "cost_per_request": 1,
        "rps_limit": 25,
    },
    "alchemy": {
        "url_template": "https://bnb-mainnet.g.alchemy.com/v2/{key}",
        "monthly_limit": 30_000_000,
        "cost_per_request": 1,
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

# Cooldown after consecutive failures (seconds)
PROVIDER_COOLDOWN = 60
# Number of consecutive failures before cooldown
FAILURES_BEFORE_COOLDOWN = 3


class MultiRPCClient:
    """Load-balancing RPC client with Chainstack primary + Alchemy fallback."""

    def __init__(self, chainstack_key: str = "", alchemy_key: str = ""):
        self._clients: dict[str, httpx.AsyncClient] = {}
        self._urls: dict[str, str] = {}
        self._usage: dict[str, int] = {"chainstack": 0, "alchemy": 0}
        self._available: dict[str, bool] = {"chainstack": False, "alchemy": False}
        self._consecutive_failures: dict[str, int] = {"chainstack": 0, "alchemy": 0}
        self._cooldown_until: dict[str, float] = {"chainstack": 0, "alchemy": 0}

        if chainstack_key:
            self._urls["chainstack"] = PROVIDERS["chainstack"]["url_template"].format(key=chainstack_key)
            self._available["chainstack"] = True
        if alchemy_key:
            self._urls["alchemy"] = PROVIDERS["alchemy"]["url_template"].format(key=alchemy_key)
            self._available["alchemy"] = True

        self._primary = "chainstack" if self._available.get("chainstack") else "alchemy"
        self._fallback = "alchemy" if self._primary == "chainstack" else "chainstack"

        status = []
        if self._available.get("chainstack"):
            status.append("Chainstack ✅")
        if self._available.get("alchemy"):
            status.append("Alchemy ✅")
        logger.info("RPC: %s (primary=%s)", " + ".join(status) or "NONE", self._primary)

    async def _get_client(self, provider: str) -> httpx.AsyncClient:
        if provider not in self._clients or self._clients[provider].is_closed:
            self._clients[provider] = httpx.AsyncClient(timeout=15.0)
        return self._clients[provider]

    async def close(self) -> None:
        for client in self._clients.values():
            if not client.is_closed:
                await client.aclose()
        self._clients.clear()

    def _is_provider_healthy(self, provider: str) -> bool:
        """Check if provider is available and not in cooldown."""
        if not self._available.get(provider):
            return False
        if time.time() < self._cooldown_until.get(provider, 0):
            return False
        return True

    def _record_failure(self, provider: str) -> None:
        """Record a failure and apply cooldown if threshold reached."""
        self._consecutive_failures[provider] = self._consecutive_failures.get(provider, 0) + 1
        if self._consecutive_failures[provider] >= FAILURES_BEFORE_COOLDOWN:
            self._cooldown_until[provider] = time.time() + PROVIDER_COOLDOWN
            logger.warning(
                "RPC %s: %d consecutive failures, cooldown %ds",
                provider, self._consecutive_failures[provider], PROVIDER_COOLDOWN,
            )

    def _record_success(self, provider: str) -> None:
        """Reset failure counter on success."""
        self._consecutive_failures[provider] = 0

    def _estimate_cu(self, method: str) -> int:
        return ALCHEMY_CU.get(method, 10)

    async def rpc_call(
        self, method: str, params: list[Any], timeout: float = 15.0
    ) -> dict[str, Any] | None:
        """Make an RPC call with automatic provider selection and fallback."""
        providers_to_try = [self._primary, self._fallback] if self._fallback != self._primary else [self._primary]

        for provider in providers_to_try:
            if not self._is_provider_healthy(provider):
                continue

            url = self._urls[provider]
            payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}

            try:
                client = await self._get_client(provider)
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()

                if "result" in data:
                    self._record_success(provider)
                    if provider == "chainstack":
                        self._usage[provider] += 1
                    else:
                        self._usage[provider] += self._estimate_cu(method)
                    return data

                if "error" in data:
                    logger.warning("RPC %s error: %s", provider, data["error"].get("message"))
                    return None

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    logger.warning("RPC %s rate limited (429)", provider)
                    self._record_failure(provider)
                    continue
                # 403, 500, etc — try fallback
                logger.warning("RPC %s HTTP %d, trying fallback", provider, e.response.status_code)
                self._record_failure(provider)
                continue
            except httpx.HTTPError as e:
                logger.warning("RPC %s connection error: %s", provider, e)
                self._record_failure(provider)
                continue
            except Exception as e:
                logger.error("RPC %s unexpected: %s", provider, e)
                self._record_failure(provider)
                continue

        return None

    def get_usage_summary(self) -> dict[str, Any]:
        return {
            "primary": self._primary,
            "fallback": self._fallback,
            "usage": dict(self._usage),
            "available": dict(self._available),
            "healthy": {
                p: self._is_provider_healthy(p) for p in ["chainstack", "alchemy"]
            },
        }
