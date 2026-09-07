"""Probe client tests (x402-agent-hire PR 1): pure `app/services/x402_client.py`.

Spec: x402-agent-hire R1 (probe → AgentOffer|None), R8 (SSRF guard).
TDD: these tests are RED against the absent module, then GREEN with
`x402_client.py`.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from app.config import get_settings
from app.services.x402_client import AgentOffer, _validate_probe_url, is_supported_offer, probe_agent_offer
from tests.conftest import payai_b64, payai_header

_PUBLIC_URL = "https://example.com/x402/paid-content"


def _offer(asset: str | None = None, network: str | None = None) -> AgentOffer:
    settings = get_settings()
    return AgentOffer(
        pay_to="0x71C7656EC7ab88b098defB751B7401B5f6d8976F",
        amount_wei=10000,
        asset=asset or settings.x402_u_token_address,
        network=network or f"eip155:{settings.x402_chain_id}",
        price_usd=Decimal("10000") / Decimal(10**18),
    )


# T1 RED — valid 402 + payment-required header → AgentOffer.
async def test_probe_agent_offer_parses_real_402(respx_mock):
    respx_mock.get(_PUBLIC_URL).respond(
        402,
        headers={"payment-required": payai_header(asset=_offer().asset, network=_offer().network)},
    )
    offer = await probe_agent_offer(_PUBLIC_URL)
    assert offer is not None
    assert offer.pay_to == "0x71C7656EC7ab88b098defB751B7401B5f6d8976F"
    assert offer.amount_wei == 10000
    assert offer.asset == _offer().asset
    assert offer.network == f"eip155:{get_settings().x402_chain_id}"
    assert offer.price_usd == Decimal(10000) / Decimal(10**18)


# T3 TRIANGULATE — timeout → None, no raise.
async def test_probe_returns_none_on_timeout(respx_mock):
    respx_mock.get(_PUBLIC_URL).mock(side_effect=httpx.TimeoutException("timed out"))
    assert await probe_agent_offer(_PUBLIC_URL) is None


# T3 — non-402 → None.
async def test_probe_returns_none_on_404(respx_mock):
    respx_mock.get(_PUBLIC_URL).respond(404)
    assert await probe_agent_offer(_PUBLIC_URL) is None


# T3 — malformed payment-required payloads → None, never raise.
@pytest.mark.parametrize(
    "header",
    [
        "%%%not-base64%%%",  # bad base64
        "e30=",  # valid base64 but "{}" — not the challenge shape (no accepts)
        "bm90IGpzb24=",  # "not json"
    ],
)
async def test_probe_returns_none_on_malformed_payment_required(respx_mock, header):
    respx_mock.get(_PUBLIC_URL).respond(402, headers={"payment-required": header})
    assert await probe_agent_offer(_PUBLIC_URL) is None


async def test_probe_returns_none_on_missing_payment_required(respx_mock):
    respx_mock.get(_PUBLIC_URL).respond(402)
    assert await probe_agent_offer(_PUBLIC_URL) is None


# T4 RED — SSRF guard: disallowed schemes / private / loopback / link-local.
@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/",
    ],
)
async def test_probe_rejects_private_range_url(respx_mock, url):
    # A route exists, but the guard must block BEFORE any request is issued.
    respx_mock.get(url).respond(200)
    assert await probe_agent_offer(url) is None
    assert respx_mock.calls == []


async def test_probe_never_raises_on_connect_error(respx_mock):
    respx_mock.get(_PUBLIC_URL).mock(side_effect=httpx.ConnectError("refused"))
    assert await probe_agent_offer(_PUBLIC_URL) is None


# T6 RED — is_supported_offer matches the rail case-insensitively.
async def test_is_supported_offer_matches_rail():
    settings = get_settings()
    assert is_supported_offer(_offer(), settings) is True
    # Case-insensitive asset (checksummed either way).
    assert is_supported_offer(_offer(asset=settings.x402_u_token_address.upper()), settings) is True
    # Wrong chain.
    assert is_supported_offer(_offer(network="eip155:56"), settings) is False
    # Wrong asset.
    assert is_supported_offer(_offer(asset="0x" + "11" * 20), settings) is False


# T5 — _validate_probe_url returns an httpx.URL for a public endpoint.
def test_validate_probe_url_accepts_public():
    u = _validate_probe_url(_PUBLIC_URL)
    assert u.scheme == "https"
    assert u.host == "example.com"


# The pinned base64 literal decodes back to the JSON fixture (drift guard).
def test_payai_b64_matches_json_fixture():
    import base64
    import json

    challenge = json.loads(base64.b64decode(payai_b64()).decode("utf-8"))
    assert challenge["x402Version"] == 2
    assert challenge["accepts"][0]["amount"] == "10000"
    assert payai_b64() == payai_header()
