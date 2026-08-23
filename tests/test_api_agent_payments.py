"""GET /api/agents/{chain_id}/{token_id}/payments — offline RPC via respx.

Trace: `CO1.BDOS.2063185 -> CO1.REQ.2121688`. The default suite never
touches a real RPC: `eth_blockNumber` + `eth_getLogs` are mocked with respx
against the test `X402_RPC_URL` (https://rpc.example.invalid, conftest).
"""

from __future__ import annotations

import json

import httpx
from eth_utils import keccak

from app.config import X402_U_TOKEN_ADDRESS_TESTNET
from app.db.models.agent import (
    BSC_IDENTITY_REGISTRY,
    AgentCache,
    build_agent_id,
)
from tests.conftest import _now

CHAIN = 97
TOKEN_ID = 42
#: Mixed-case wallet — the onchain filter must match case-insensitively.
WALLET = "0x" + "aB" * 20
RPC_URL = "https://rpc.example.invalid"
TRANSFER_TOPIC0 = "0x" + keccak(b"Transfer(address,address,uint256)").hex()

LATEST = 3_141_592
DEFAULT_FROM = LATEST - 50_000


def _rpc_result(result) -> httpx.Response:
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})


def _rpc_error() -> httpx.Response:
    return httpx.Response(
        200,
        json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32005, "message": "limit exceeded"}},
    )


def _log(tx_suffix: str, sender: str, recipient: str, value: int, block: int) -> dict:
    return {
        "address": X402_U_TOKEN_ADDRESS_TESTNET,
        "topics": [
            TRANSFER_TOPIC0,
            "0x" + sender[2:].lower().rjust(64, "0"),
            "0x" + recipient[2:].lower().rjust(64, "0"),
        ],
        "data": hex(value),
        "blockNumber": hex(block),
        "transactionHash": "0x" + tx_suffix,
        "transactionIndex": "0x0",
        "logIndex": "0x0",
    }


async def _seed_agent(session, *, chain_id=CHAIN, token_id=TOKEN_ID, wallet=WALLET) -> None:
    session.add(
        AgentCache(
            agent_id=build_agent_id(chain_id, BSC_IDENTITY_REGISTRY, token_id),
            chain_id=chain_id,
            token_id=token_id,
            registry_address=BSC_IDENTITY_REGISTRY,
            agent_wallet=wallet,
            name="P",
            x402_supported=True,
            category="other",
            supported_protocols=[],
            cross_chain_versions=[],
            raw={},
            created_at=_now(),
            updated_at=_now(),
        )
    )
    await session.commit()


def _last_rpc_request(respx_mock) -> dict:
    return json.loads(respx_mock.calls[-1].request.content)


# 404 — agent not in the local cache (no RPC call involved).
async def test_payments_404_unknown_agent(client, db):
    resp = client.get(f"/api/agents/{CHAIN}/{TOKEN_ID}/payments")
    assert resp.status_code == 404
    assert resp.json() == {
        "error": {"code": "not_found", "message": f"agent {CHAIN}:{TOKEN_ID} not cached"}
    }


# 200 — two matching transfers (out of order + one to another wallet), newest
# first, fields parsed, and the eth_getLogs payload is shaped correctly.
async def test_payments_200_with_transfers(client, db, respx_mock):
    await _seed_agent(db)
    other_wallet = "0x" + "cc" * 20
    logs = [
        _log("aa" * 32, "0x" + "11" * 20, other_wallet, 1, LATEST),  # filtered out
        _log("bb" * 32, "0x" + "22" * 20, WALLET, 1000, LATEST),  # newest match
        _log("cc" * 32, "0x" + "33" * 20, WALLET, 42, LATEST - 10),  # older match
    ]
    respx_mock.post(RPC_URL).mock(side_effect=[_rpc_result(hex(LATEST)), _rpc_result(logs)])

    resp = client.get(f"/api/agents/{CHAIN}/{TOKEN_ID}/payments")
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent_id"] == build_agent_id(CHAIN, BSC_IDENTITY_REGISTRY, TOKEN_ID)
    assert body["chain_id"] == CHAIN and body["token_id"] == TOKEN_ID
    assert body["wallet"] == WALLET
    assert body["token"] == X402_U_TOKEN_ADDRESS_TESTNET
    assert [p["tx_hash"] for p in body["payments"]] == ["0x" + "bb" * 32, "0x" + "cc" * 32]
    assert body["payments"][0]["value_wei"] == "1000"
    assert body["payments"][0]["from"] == "0x" + "22" * 20
    assert body["payments"][0]["to"] == WALLET.lower()
    assert body["payments"][0]["block_number"] == LATEST

    get_logs = _last_rpc_request(respx_mock)
    assert get_logs["method"] == "eth_getLogs"
    params = get_logs["params"][0]
    assert params["address"] == X402_U_TOKEN_ADDRESS_TESTNET
    assert params["fromBlock"] == hex(DEFAULT_FROM)
    assert params["toBlock"] == "latest"
    assert params["topics"] == [
        TRANSFER_TOPIC0,
        None,
        "0x" + WALLET[2:].lower().rjust(64, "0"),
    ]


# 200 — agent with no payment wallet: empty list, no RPC call at all.
async def test_payments_200_wallet_null_no_rpc(client, db, respx_mock):
    await _seed_agent(db, wallet=None)
    resp = client.get(f"/api/agents/{CHAIN}/{TOKEN_ID}/payments")
    assert resp.status_code == 200
    body = resp.json()
    assert body["wallet"] is None
    assert body["payments"] == []
    assert body["token"] == X402_U_TOKEN_ADDRESS_TESTNET
    assert respx_mock.calls == []


# limit is respected (newest kept) and enforced at the API edge (422 > 200).
async def test_payments_limit_param(client, db, respx_mock):
    await _seed_agent(db)
    logs = [
        _log("bb" * 32, "0x" + "22" * 20, WALLET, 1000, LATEST),
        _log("cc" * 32, "0x" + "33" * 20, WALLET, 42, LATEST - 10),
    ]
    respx_mock.post(RPC_URL).mock(side_effect=[_rpc_result(hex(LATEST)), _rpc_result(logs)])
    body = client.get(f"/api/agents/{CHAIN}/{TOKEN_ID}/payments?limit=1").json()
    assert [p["tx_hash"] for p in body["payments"]] == ["0x" + "bb" * 32]

    assert client.get(f"/api/agents/{CHAIN}/{TOKEN_ID}/payments?limit=201").status_code == 422


# 503 — RPC failure (eth_blockNumber errors) → upstream_unavailable envelope.
async def test_payments_503_rpc_error(client, db, respx_mock):
    await _seed_agent(db)
    respx_mock.post(RPC_URL).mock(side_effect=[_rpc_error()])
    resp = client.get(f"/api/agents/{CHAIN}/{TOKEN_ID}/payments")
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "upstream_unavailable"
