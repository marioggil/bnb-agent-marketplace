"""Onchain agent payment history: $U `Transfer` logs via JSON-RPC.

Trace: `CO1.BDOS.2063185 -> CO1.REQ.2121688` (docs/traceability.md).

Pure httpx + JSON-RPC, no web3.py — same transport pattern as
`app.services.payment.OnchainBroadcaster` (design id 52). Every RPC failure
(timeout, 429, 5xx, malformed body) surfaces as `UpstreamUnavailable` (503)
so the caller never sees a raw transport exception.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from eth_utils.crypto import keccak

from app.errors import UpstreamUnavailable

logger = logging.getLogger(__name__)

#: keccak("Transfer(address,address,uint256)") — topic0 of every ERC-20 transfer.
TRANSFER_TOPIC0: str = "0x" + keccak(b"Transfer(address,address,uint256)").hex()

#: Default lookback when `from_block` is omitted: ~50000 BSC blocks ≈ 24-48h
#: (BSC produces a block every ~3s).
DEFAULT_LOOKBACK_BLOCKS: int = 50_000
#: Public RPCs reject eth_getLogs ranges beyond this; every query is clamped
#: to the last MAX_RANGE_BLOCKS blocks even when the caller asks for more.
MAX_RANGE_BLOCKS: int = 200_000
#: HTTP timeout per JSON-RPC call (public nodes can be slow on big ranges).
RPC_TIMEOUT_S: float = 15.0


def _pad32(address: str) -> str:
    """32-byte left-padded indexed topic for `address` (0x + 64 hex)."""
    return "0x" + address[2:].lower().rjust(64, "0")


def _to_int(value: Any) -> int:
    """Coerce a JSON-RPC hex string (or int) to int."""
    if isinstance(value, str) and value.startswith("0x"):
        return int(value, 16)
    return int(value)


def _parse_log(log: dict[str, Any], wallet: str) -> dict[str, Any] | None:
    """Decode one Transfer log; None when it is not addressed to `wallet`."""
    topics = log.get("topics") or []
    if not isinstance(topics, list) or len(topics) < 3:
        return None
    recipient = "0x" + topics[2][-40:]
    if recipient.lower() != wallet.lower():
        return None
    try:
        value_wei = str(int(log["data"], 16))
        block_number = _to_int(log["blockNumber"])
    except (KeyError, TypeError, ValueError):
        logger.warning("malformed Transfer log: %r", log)
        return None
    return {
        "tx_hash": log.get("transactionHash"),
        "from": "0x" + topics[1][-40:],
        "to": recipient,
        "value_wei": value_wei,
        "block_number": block_number,
    }


async def _rpc(client: httpx.AsyncClient, rpc_url: str, method: str, params: list[Any]) -> Any:
    """One JSON-RPC call; transport/HTTP/error responses → `UpstreamUnavailable`."""
    try:
        resp = await client.post(
            rpc_url,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        )
        resp.raise_for_status()
        body = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise UpstreamUnavailable(f"RPC transport error on {method}: {exc}") from exc
    if not isinstance(body, dict) or "error" in body:
        raise UpstreamUnavailable(f"RPC error on {method}: {body.get('error', body)!r}")
    return body.get("result")


async def fetch_agent_payments(
    wallet: str,
    token_address: str,
    rpc_url: str,
    chain_id: int,
    limit: int = 50,
    from_block: int | None = None,
) -> list[dict[str, Any]]:
    """$U `Transfer` logs where `to == wallet`, newest first, capped at `limit`.

    - `from_block` omitted → `eth_blockNumber` - ~50000 (last ~24-48h).
    - `from_block` given → clamped so the query range never exceeds
      `MAX_RANGE_BLOCKS` blocks (public RPCs reject huge eth_getLogs ranges).
    - RPC failure (timeout/429/5xx) → `UpstreamUnavailable` (503).
    """
    async with httpx.AsyncClient(timeout=RPC_TIMEOUT_S) as client:
        latest = await _rpc(client, rpc_url, "eth_blockNumber", [])
        if not isinstance(latest, str) or not latest.startswith("0x"):
            raise UpstreamUnavailable("eth_blockNumber returned no block number")
        latest_int = _to_int(latest)
        if from_block is None:
            effective_from = max(latest_int - DEFAULT_LOOKBACK_BLOCKS, 0)
        else:
            effective_from = max(from_block, latest_int - MAX_RANGE_BLOCKS)
        logger.debug(
            "agent payments chain=%s wallet=%s range=[%s, latest=%s]",
            chain_id,
            wallet,
            effective_from,
            latest_int,
        )
        logs = await _rpc(
            client,
            rpc_url,
            "eth_getLogs",
            [
                {
                    "fromBlock": hex(effective_from),
                    "toBlock": "latest",
                    "address": token_address,
                    "topics": [TRANSFER_TOPIC0, None, _pad32(wallet)],
                }
            ],
        )
    if not isinstance(logs, list):
        return []
    payments = [p for p in (_parse_log(log, wallet) for log in logs) if p is not None]
    payments.sort(key=lambda p: p["block_number"], reverse=True)
    return payments[:limit]


__all__ = [
    "DEFAULT_LOOKBACK_BLOCKS",
    "MAX_RANGE_BLOCKS",
    "TRANSFER_TOPIC0",
    "fetch_agent_payments",
]
