"""On-chain hire verification via Alchemy RPC.

Queries ERC-20 token transfers to an agent's wallet using Alchemy's
BSC RPC endpoint. Scans blocks in 10-block chunks (free tier limit).

Reference: https://docs.alchemy.com/reference/token-transfer-events
"""
from __future__ import annotations

import asyncio
import logging
import os
from decimal import Decimal
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Alchemy BSC RPC endpoint
ALCHEMY_RPC_BASE = "https://bnb-mainnet.g.alchemy.com/v2"

# $U token addresses (same as in config.py)
U_TOKEN_MAINNET = "0xcE24439F2D9C6a2289F741120FE202248B666666"
U_TOKEN_TESTNET = "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565"

# ERC-20 Transfer event topic
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# Alchemy free tier limit: 10 blocks per eth_getLogs request
ALCHEMY_BLOCK_RANGE_LIMIT = 10


class AlchemyOnchainClient:
    """Client for on-chain hire verification via Alchemy RPC."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("ALCHEMY_API_KEY", "")
        self.rpc_url = f"{ALCHEMY_RPC_BASE}/{self.api_key}" if self.api_key else ""
        # Rate limit: 10 requests/second for free tier
        self._semaphore = asyncio.Semaphore(10)
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create a persistent httpx client (reuses connections)."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    async def close(self) -> None:
        """Close the persistent httpx client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _rpc_call(
        self, method: str, params: list[Any], timeout: float = 10.0
    ) -> dict[str, Any] | None:
        """Make a rate-limited RPC call to Alchemy."""
        if not self.rpc_url:
            logger.warning("Alchemy API key not configured")
            return None

        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": 1,
        }

        async with self._semaphore:
            try:
                client = await self._get_client()
                resp = await client.post(self.rpc_url, json=payload)
                resp.raise_for_status()
                data = resp.json()

                if "result" in data:
                    return data
                elif "error" in data:
                    logger.warning(
                        "Alchemy RPC error: %s", data["error"].get("message", "unknown")
                    )
                    return data
                return None
            except httpx.HTTPError as e:
                logger.error("Alchemy HTTP error: %s", e)
                return None

    async def get_current_block(self) -> int | None:
        """Get the current block number."""
        data = await self._rpc_call("eth_blockNumber", [])
        if data and "result" in data:
            return int(data["result"], 16)
        return None

    async def get_transfer_logs(
        self,
        from_block: int,
        to_block: int,
        token_address: str = U_TOKEN_MAINNET,
        to_address: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get ERC-20 Transfer events in a block range.

        Args:
            from_block: Start block number
            to_block: End block number (inclusive, max from_block + 9)
            token_address: $U token contract address
            to_address: Filter by recipient wallet (padded to 32 bytes)

        Returns:
            List of transfer logs with:
            - from: sender address (from topics[1])
            - to: recipient address (from topics[2])
            - value: transfer amount (from data, 18 decimals)
            - blockNumber: block number
            - transactionHash: tx hash
        """
        # Alchemy free tier: max 10 blocks per request (from_block to to_block inclusive)
        # to_block must be <= from_block + 9
        block_range = to_block - from_block
        if block_range > 9:
            to_block = from_block + 9

        # Pad to_address to 32 bytes for topic filter
        to_topic = None
        if to_address:
            to_topic = "0x000000000000000000000000" + to_address.lower()[2:]

        topics = [TRANSFER_TOPIC, None, to_topic]

        data = await self._rpc_call(
            "eth_getLogs",
            [
                {
                    "address": token_address,
                    "topics": topics,
                    "fromBlock": hex(from_block),
                    "toBlock": hex(to_block),
                }
            ],
        )

        if data and "result" in data and isinstance(data["result"], list):
            return data["result"]
        return []

    async def scan_transfers(
        self,
        wallet_address: str,
        token_address: str = U_TOKEN_MAINNET,
        max_blocks: int = 1000,
        block_range: int = ALCHEMY_BLOCK_RANGE_LIMIT,
    ) -> list[dict[str, Any]]:
        """Scan multiple block ranges for transfers to a wallet.

        Args:
            wallet_address: Agent's wallet address (0x...)
            token_address: $U token contract address
            max_blocks: Maximum number of blocks to scan
            block_range: Blocks per request (Alchemy free tier: 10)

        Returns:
            List of all transfers found
        """
        current_block = await self.get_current_block()
        if current_block is None:
            return []

        all_transfers = []
        scanned = 0

        while scanned < max_blocks:
            # Each request scans exactly block_range blocks (e.g., 10)
            # from_block = current - scanned - (block_range - 1)
            # to_block = current - scanned
            from_block = current_block - scanned - (block_range - 1)
            to_block = current_block - scanned

            transfers = await self.get_transfer_logs(
                from_block=from_block,
                to_block=to_block,
                token_address=token_address,
                to_address=wallet_address,
            )

            all_transfers.extend(transfers)
            scanned += block_range

            # Small delay to avoid rate limiting
            await asyncio.sleep(0.05)

        return all_transfers

    async def get_hire_stats(
        self,
        wallet_address: str,
        token_address: str = U_TOKEN_MAINNET,
        min_amount: Decimal = Decimal("0.01"),
        max_blocks: int = 10000,
    ) -> dict[str, Any]:
        """Get hire statistics for an agent wallet.

        Scans recent blocks for $U token transfers to the wallet.

        Args:
            wallet_address: Agent's wallet address
            token_address: $U token contract address
            min_amount: Minimum amount to consider (filters dust)
            max_blocks: How far back to scan (10000 = ~8 hours)

        Returns:
            {
                "total_hires": int,
                "total_volume": str,
                "transfers": list[dict],
                "unique_senders": list[str],
                "scanned_blocks": int,
            }
        """
        raw_transfers = await self.scan_transfers(
            wallet_address=wallet_address,
            token_address=token_address,
            max_blocks=max_blocks,
        )

        if not raw_transfers:
            return {
                "total_hires": 0,
                "total_volume": "0",
                "transfers": [],
                "unique_senders": [],
                "scanned_blocks": max_blocks,
            }

        # Filter by minimum amount and count unique senders
        unique_senders: set[str] = set()
        total_volume = Decimal("0")
        valid_transfers = []

        for log in raw_transfers:
            # Extract sender from topics[1] (padded hex string)
            # Topics are hex strings like "0x0000...1234..."
            topic = log["topics"][1]
            # Remove 0x prefix and padding, take last 40 chars (20 bytes = address)
            if isinstance(topic, bytes):
                sender = "0x" + topic.hex()[-40:]
            else:
                # It's a hex string
                sender = "0x" + topic[-40:]

            # Extract value from data field (uint256, 18 decimals)
            value = Decimal(int(log["data"], 16)) / Decimal(10**18)

            if value >= min_amount:
                unique_senders.add(sender.lower())
                total_volume += value
                valid_transfers.append(
                    {
                        "from": sender,
                        "value": str(value),
                        "blockNumber": int(log["blockNumber"], 16),
                        "transactionHash": log["transactionHash"],
                    }
                )

        return {
            "total_hires": len(unique_senders),
            "total_volume": str(total_volume),
            "transfers": valid_transfers,
            "unique_senders": list(unique_senders),
            "scanned_blocks": max_blocks,
        }


# Singleton instance
_onchain_client: AlchemyOnchainClient | None = None


def get_onchain_client() -> AlchemyOnchainClient:
    """Get or create Alchemy onchain client singleton."""
    global _onchain_client
    if _onchain_client is None:
        _onchain_client = AlchemyOnchainClient()
    return _onchain_client
