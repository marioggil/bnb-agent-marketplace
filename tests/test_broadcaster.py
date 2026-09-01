"""Broadcaster tests: FakeBroadcaster double + respx-mocked RPC (X5).

Spec: `sdd/x402-real-payment/spec` x402-payments X5 · design id 52 (settle.ts
flow: estimateGas → sendRawTransaction → receipt 0x1). Offline by design —
the RPC is mocked with respx, never touched.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from app.config import get_settings
from app.services.payment import (
    BroadcastFailed,
    DecodedPayment,
    FakeBroadcaster,
    OnchainBroadcaster,
    TokenConfig,
    decode_envelope,
    get_token_config,
)

FIXTURE = json.loads(
    Path(__file__).parent.joinpath("fixtures", "b402_challenge.json").read_text(encoding="utf-8")
)
RPC_URL = "https://rpc.example.invalid"


def _decoded(payer, signed_envelope) -> DecodedPayment:
    return decode_envelope(signed_envelope(payer, dict(FIXTURE)))


def _token_cfg() -> TokenConfig:
    return get_token_config(get_settings(), 97)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# FakeBroadcaster — canned hash + call recording
# ---------------------------------------------------------------------------


async def test_fake_broadcaster_returns_canned_hash_and_records(payer, signed_envelope):
    fb = FakeBroadcaster(tx_hash="0x" + "ef" * 32)
    decoded = _decoded(payer, signed_envelope)
    result = await fb.broadcast(
        decoded, _token_cfg(), facilitator_key="0x1", rpc_url=RPC_URL, now=_now()
    )
    assert result.tx_hash == "0x" + "ef" * 32
    assert len(fb.calls) == 1
    assert fb.calls[0]["decoded"] is decoded
    assert fb.calls[0]["rpc_url"] == RPC_URL


async def test_fake_broadcaster_default_hash(payer, signed_envelope):
    fb = FakeBroadcaster()
    result = await fb.broadcast(
        _decoded(payer, signed_envelope),
        _token_cfg(),
        facilitator_key="0x1",
        rpc_url=RPC_URL,
        now=_now(),
    )
    assert result.tx_hash == "0x" + "ab" * 32


# ---------------------------------------------------------------------------
# OnchainBroadcaster vs respx-mocked RPC
# ---------------------------------------------------------------------------


def _rpc_response(result) -> httpx.Response:
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})


def _rpc_error_response() -> httpx.Response:
    return httpx.Response(
        200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "boom"}}
    )


async def test_onchain_broadcaster_happy_receipt_0x1(respx_mock, payer, signed_envelope):
    tx_hash = "0x" + "12" * 32
    respx_mock.post(RPC_URL).mock(
        side_effect=[
            _rpc_response("0x1"),  # eth_getTransactionCount
            _rpc_response("0x59682f00"),  # eth_gasPrice
            _rpc_response("0x5208"),  # eth_estimateGas
            _rpc_response(tx_hash),  # eth_sendRawTransaction
            _rpc_response({"status": "0x1"}),  # eth_getTransactionReceipt
        ]
    )
    broadcaster = OnchainBroadcaster(poll_interval=0.01, poll_attempts=5)
    result = await broadcaster.broadcast(
        _decoded(payer, signed_envelope),
        _token_cfg(),
        facilitator_key="0x" + "01" * 32,
        rpc_url=RPC_URL,
        now=_now(),
    )
    assert result.tx_hash == tx_hash


async def test_onchain_broadcaster_revert_0x0(respx_mock, payer, signed_envelope):
    tx_hash = "0x" + "34" * 32
    respx_mock.post(RPC_URL).mock(
        side_effect=[
            _rpc_response("0x1"),
            _rpc_response("0x59682f00"),
            _rpc_response("0x5208"),
            _rpc_response(tx_hash),
            _rpc_response({"status": "0x0"}),
        ]
    )
    broadcaster = OnchainBroadcaster(poll_interval=0.01, poll_attempts=5)
    with pytest.raises(BroadcastFailed):
        await broadcaster.broadcast(
            _decoded(payer, signed_envelope),
            _token_cfg(),
            facilitator_key="0x" + "01" * 32,
            rpc_url=RPC_URL,
            now=_now(),
        )


async def test_onchain_broadcaster_timeout_no_receipt(respx_mock, payer, signed_envelope):
    tx_hash = "0x" + "56" * 32
    respx_mock.post(RPC_URL).mock(
        side_effect=[
            _rpc_response("0x1"),
            _rpc_response("0x59682f00"),
            _rpc_response("0x5208"),
            _rpc_response(tx_hash),
            _rpc_response(None),  # receipt not mined yet (poll 1)
            _rpc_response(None),  # poll 2 (last)
        ]
    )
    broadcaster = OnchainBroadcaster(poll_interval=0.01, poll_attempts=2)
    with pytest.raises(BroadcastFailed):
        await broadcaster.broadcast(
            _decoded(payer, signed_envelope),
            _token_cfg(),
            facilitator_key="0x" + "01" * 32,
            rpc_url=RPC_URL,
            now=_now(),
        )


async def test_onchain_broadcaster_rpc_error(respx_mock, payer, signed_envelope):
    respx_mock.post(RPC_URL).mock(side_effect=[_rpc_error_response()])
    broadcaster = OnchainBroadcaster(poll_interval=0.01, poll_attempts=2)
    with pytest.raises(BroadcastFailed):
        await broadcaster.broadcast(
            _decoded(payer, signed_envelope),
            _token_cfg(),
            facilitator_key="0x" + "01" * 32,
            rpc_url=RPC_URL,
            now=_now(),
        )


async def test_onchain_broadcaster_empty_key_raises(payer, signed_envelope):
    broadcaster = OnchainBroadcaster()
    with pytest.raises(BroadcastFailed):
        await broadcaster.broadcast(
            _decoded(payer, signed_envelope),
            _token_cfg(),
            facilitator_key="",
            rpc_url=RPC_URL,
            now=_now(),
        )
