"""X402 settings tests: testnet defaults + mainnet override (B1).

Spec: `sdd/x402-real-payment/spec` app-bootstrap-x402 B1 · design id 52 D5.
Uses a fresh `Settings` per case (cache cleared) so the env surface is
exercised exactly as the runtime sees it.
"""
from __future__ import annotations

from decimal import Decimal

from app.config import (
    X402_CHAIN_MAINNET,
    X402_CHAIN_TESTNET,
    X402_U_TOKEN_ADDRESS_MAINNET,
    X402_U_TOKEN_ADDRESS_TESTNET,
    Settings,
    _settings_cache,
    get_settings,
)


def _fresh() -> Settings:
    _settings_cache.cache_clear()
    return Settings()


# B1 — no X402 env: chain 97, $1.00, pinned 97 addresses, testnet RPC.
def test_defaults_are_testnet_97(monkeypatch):
    monkeypatch.delenv("X402_CHAIN_ID", raising=False)
    monkeypatch.delenv("X402_RPC_URL", raising=False)
    s = _fresh()
    assert s.x402_chain_id == X402_CHAIN_TESTNET
    assert s.x402_default_price_usd == Decimal("1.00")
    assert s.x402_u_token_address == X402_U_TOKEN_ADDRESS_TESTNET
    assert s.x402_rpc_url_resolved == "https://bsc-testnet-rpc.publicnode.com"
    assert s.x402_permit2_address == "0x000000000022D473030F116dDEE9F6B43aC78BA3"


# B1 — X402_CHAIN_ID=56 switches the pinned $U address and RPC (demo day).
def test_mainnet_override_56(monkeypatch):
    monkeypatch.setenv("X402_CHAIN_ID", str(X402_CHAIN_MAINNET))
    monkeypatch.delenv("X402_RPC_URL", raising=False)
    s = _fresh()
    assert s.x402_chain_id == X402_CHAIN_MAINNET
    assert s.x402_u_token_address == X402_U_TOKEN_ADDRESS_MAINNET
    assert s.x402_rpc_url_resolved == "https://bsc-rpc.publicnode.com"


# D5 — RPC override wins over the per-chain default.
def test_rpc_url_override(monkeypatch):
    monkeypatch.setenv("X402_RPC_URL", "https://rpc.my-node.example")
    s = _fresh()
    assert s.x402_rpc_url_resolved == "https://rpc.my-node.example"


# D5/B3 — empty facilitator key disables payments; a set key enables them.
def test_facilitator_key_gates_payments(monkeypatch):
    monkeypatch.setenv("X402_FACILITATOR_KEY", "")
    assert _fresh().x402_payments_configured is False
    monkeypatch.setenv("X402_FACILITATOR_KEY", "0x" + "01" * 32)
    assert _fresh().x402_payments_configured is True
