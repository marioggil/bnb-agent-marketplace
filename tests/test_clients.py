"""Unit tests for the Termix, EvoEvo, and Alchemy onchain clients."""

from __future__ import annotations

import httpx
import pytest

from app.services.client_bscscan import U_TOKEN_MAINNET, AlchemyOnchainClient
from app.services.client_evoevo import fetch_evoevo_card
from app.services.client_termix import fetch_termix_card

EVOEVO_BASE = "https://api.evoevo.ai/agents"
TERMIX_BASE = "https://platform-backend.prod.termix.live/api/v1/a2a/agents"
ALCHEMY_RPC_BASE = "https://bnb-mainnet.g.alchemy.com/v2"


@pytest.mark.anyio
async def test_fetch_evoevo_card_success(respx_mock):
    respx_mock.get(f"{EVOEVO_BASE}/123").respond(
        200,
        json={
            "name": "Bot123",
            "active": True,
            "x402Support": False,
            "services": [],
            "registrations": [],
        },
    )
    result = await fetch_evoevo_card(123)
    assert result is not None
    assert result["name"] == "Bot123"
    assert result["active"] is True


@pytest.mark.anyio
async def test_fetch_evoevo_card_404_returns_none(respx_mock):
    respx_mock.get(f"{EVOEVO_BASE}/999").respond(404)
    result = await fetch_evoevo_card(999)
    assert result is None


@pytest.mark.anyio
async def test_fetch_evoevo_card_network_error_returns_none(respx_mock):
    respx_mock.get(f"{EVOEVO_BASE}/888").mock(side_effect=httpx.ConnectError("refused"))
    result = await fetch_evoevo_card(888)
    assert result is None


@pytest.mark.anyio
async def test_fetch_termix_card_success(respx_mock):
    respx_mock.get(f"{TERMIX_BASE}/456/card").respond(
        200,
        json={
            "status": "BOUND",
            "presence": "online",
        },
    )
    result = await fetch_termix_card(456)
    assert result is not None
    assert result["status"] == "BOUND"


@pytest.mark.anyio
async def test_fetch_termix_card_404_returns_none(respx_mock):
    respx_mock.get(f"{TERMIX_BASE}/789/card").respond(404)
    result = await fetch_termix_card(789)
    assert result is None


@pytest.mark.anyio
async def test_fetch_termix_card_network_error_returns_none(respx_mock):
    respx_mock.get(f"{TERMIX_BASE}/111/card").mock(side_effect=httpx.ConnectError("refused"))
    result = await fetch_termix_card(111)
    assert result is None


# ---------------------------------------------------------------------------
# Alchemy onchain client tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_alchemy_client_no_api_key(monkeypatch):
    """Alchemy client returns None when no API key is configured."""
    # Ensure no API key is set
    monkeypatch.delenv("ALCHEMY_API_KEY", raising=False)
    client = AlchemyOnchainClient(api_key="")
    result = await client._rpc_call("eth_blockNumber", [])
    assert result is None


@pytest.mark.anyio
async def test_alchemy_get_current_block_success(respx_mock):
    """Alchemy client fetches current block successfully."""
    mock_response = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": "0x70ee87c",  # Block 118417532
    }
    respx_mock.post(f"{ALCHEMY_RPC_BASE}/test_key").respond(200, json=mock_response)

    client = AlchemyOnchainClient(api_key="test_key")
    block = await client.get_current_block()

    assert block == 118417532


@pytest.mark.anyio
async def test_alchemy_get_transfer_logs_success(respx_mock):
    """Alchemy client fetches transfer logs successfully."""
    mock_response = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": [
            {
                "address": U_TOKEN_MAINNET,
                "topics": [
                    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
                    "0x0000000000000000000000001234567890abcdef1234567890abcdef12345678",
                    "0x000000000000000000000000842b3628689adab7507722de702eae3bc215dd0c",
                ],
                "data": "0x0de0b6b3a7640000",  # 1.0 $U (18 decimals)
                "blockNumber": "0x70ee87c",
                "transactionHash": "0xabc123",
            }
        ],
    }
    respx_mock.post(f"{ALCHEMY_RPC_BASE}/test_key").respond(200, json=mock_response)

    client = AlchemyOnchainClient(api_key="test_key")
    logs = await client.get_transfer_logs(
        from_block=118417530,
        to_block=118417539,
        to_address="0x842b3628689adab7507722de702eae3bc215dd0c",
    )

    assert len(logs) == 1
    assert logs[0]["blockNumber"] == "0x70ee87c"
    assert logs[0]["transactionHash"] == "0xabc123"


@pytest.mark.anyio
async def test_alchemy_get_hire_stats_filters_dust():
    """Alchemy client filters out dust transfers below min_amount."""
    from decimal import Decimal

    client = AlchemyOnchainClient(api_key="test_key")

    # Mock scan_transfers to return test data
    async def mock_scan_transfers(**kwargs):
        return [
            {
                "address": U_TOKEN_MAINNET,
                "topics": [
                    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
                    "0x0000000000000000000000001111111111111111111111111111111111111111",
                    "0x000000000000000000000000842b3628689adab7507722de702eae3bc215dd0c",
                ],
                "data": "0x2386f26fc10000",  # 0.01 $U (below min)
                "blockNumber": "0x70ee87c",
                "transactionHash": "0xabc123",
            },
            {
                "address": U_TOKEN_MAINNET,
                "topics": [
                    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
                    "0x0000000000000000000000002222222222222222222222222222222222222222",
                    "0x000000000000000000000000842b3628689adab7507722de702eae3bc215dd0c",
                ],
                "data": "0x0de0b6b3a7640000",  # 1.0 $U (above min)
                "blockNumber": "0x70ee87d",
                "transactionHash": "0xdef456",
            },
        ]

    client.scan_transfers = mock_scan_transfers

    stats = await client.get_hire_stats(
        wallet_address="0x842b3628689adab7507722de702eae3bc215dd0c",
        min_amount=Decimal("0.05"),  # Filter out 0.01 $U
    )

    assert stats["total_hires"] == 1  # Only one sender above min
    assert stats["total_volume"] == "1"
    assert len(stats["unique_senders"]) == 1
