"""Unit tests for the Termix and EvoEvo API clients."""
from __future__ import annotations

import httpx
import pytest

from app.services.client_evoevo import fetch_evoevo_card
from app.services.client_termix import fetch_termix_card

EVOEVO_BASE = "https://api.evoevo.ai/agents"
TERMIX_BASE = "https://platform-backend.prod.termix.live/api/v1/a2a/agents"


@pytest.mark.anyio
async def test_fetch_evoevo_card_success(respx_mock):
    respx_mock.get(f"{EVOEVO_BASE}/123").respond(200, json={
        "name": "Bot123",
        "active": True,
        "x402Support": False,
        "services": [],
        "registrations": [],
    })
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
    respx_mock.get(f"{TERMIX_BASE}/456/card").respond(200, json={
        "status": "BOUND",
        "presence": "online",
    })
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
