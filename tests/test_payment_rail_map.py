"""Rail-map + per-chain token-config tests (x402-remove-fee-multichain, PR 1).

Spec: R1 (static rail map covers Base/Polygon/Avalanche USDC + $U 56/97), R2
(get_token_config returns the rail-map token), R3 (unknown chain raises).
Strict TDD: these tests are RED against the absent `X402_RAIL_MAP` /
map-driven `get_token_config`, then GREEN with config + payment changes.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from app.config import (
    X402_RAIL_MAP,
    X402_U_TOKEN_ADDRESS_MAINNET,
    X402_U_TOKEN_ADDRESS_TESTNET,
    U_TOKEN_NAME,
    U_TOKEN_VERSION,
    get_settings,
)
from app.errors import UnknownRail
from app.services.payment import get_token_config


# T1 RED — map shape: exactly the five settlement chains, each resolvable.
def test_rail_map_has_evm_and_uo_chains():
    assert set(X402_RAIL_MAP) == {8453, 137, 43114, 56, 97}
    for rail in X402_RAIL_MAP.values():
        assert rail.rpc_url
        assert rail.token_address
        assert rail.token_name
        assert rail.token_version
    # USDC chains pin their canonical addresses.
    assert X402_RAIL_MAP[8453].token_address == "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    assert X402_RAIL_MAP[137].token_address == "0x3c499c542cEF5E3811e1792ce70d8cC03d50c3359"
    assert X402_RAIL_MAP[43114].token_address == "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48A6E"


# T3 RED — per-chain token config: USDC facts on the EVM chains, $U on 56/97.
def test_get_token_config_returns_per_chain_token():
    settings = get_settings()
    base = get_token_config(settings, 8453)
    assert base.address == "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    assert base.name == "USD Coin"
    assert base.version == "2"
    u = get_token_config(settings, 56)
    assert u.address == X402_U_TOKEN_ADDRESS_MAINNET
    assert u.name == U_TOKEN_NAME
    assert u.version == U_TOKEN_VERSION
    testnet = get_token_config(settings, 97)
    assert testnet.address == X402_U_TOKEN_ADDRESS_TESTNET
    assert testnet.name == U_TOKEN_NAME
    assert testnet.version == U_TOKEN_VERSION


# T5 RED — unknown chain raises UnknownRail (R3).
@pytest.mark.parametrize("chain_id", [999999, 84532, 1])
def test_get_token_config_unknown_chain_raises(chain_id):
    with pytest.raises(UnknownRail):
        get_token_config(get_settings(), chain_id)


# T7 TRIANGULATE — frozen per-chain facts (locks the EIP-712 domain per rail).
def test_token_config_frozen_fixture():
    settings = get_settings()
    frozen = json.dumps(
        [
            dataclasses.asdict(get_token_config(settings, c))
            for c in sorted(X402_RAIL_MAP)
        ]
    )
    assert frozen == (
        '[{"address": "0xcE24439F2D9C6a2289F741120FE202248B666666", "name": "United Stables", "version": "1"}, '
        '{"address": "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565", "name": "United Stables", "version": "1"}, '
        '{"address": "0x3c499c542cEF5E3811e1792ce70d8cC03d50c3359", "name": "USD Coin", "version": "2"}, '
        '{"address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "name": "USD Coin", "version": "2"}, '
        '{"address": "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48A6E", "name": "USD Coin", "version": "2"}]'
    )
