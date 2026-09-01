"""Agent detail x402 UI tests: CTA render/disable, redirect target, status UI.

Spec: `sdd/x402-real-payment/spec` web-pages-x402 (W1/W3/W4) · design id 52
(Q6, D1). The tests validate the rendered attributes and markup the signer
JS consumes — they do not execute JS (offline smoke suite).
"""

from __future__ import annotations

from app.db.models.agent import (
    BSC_CHAIN_ID,
    BSC_IDENTITY_REGISTRY,
    AgentCache,
    build_agent_id,
)
from tests.conftest import _now

_AGENT_URL = "https://agent.example.com/chat"


async def _seed_one(session, token_id: int = 1, wallet: str | None = "0x" + "77" * 20) -> str:
    aid = build_agent_id(56, BSC_IDENTITY_REGISTRY, token_id)
    session.add(
        AgentCache(
            agent_id=aid,
            chain_id=BSC_CHAIN_ID,
            token_id=token_id,
            registry_address=BSC_IDENTITY_REGISTRY,
            name="Alpha",
            agent_wallet=wallet,
            agent_url=_AGENT_URL,
            supported_protocols=[],
            cross_chain_versions=[],
            raw={},
            created_at=_now(),
            updated_at=_now(),
            tags=[],
            categories=[],
        )
    )
    await session.commit()
    return aid


def _detail(client, token_id: int = 1) -> str:
    return client.get(f"/agents/{BSC_CHAIN_ID}/{token_id}").text


# W1 — CTA shows the flat price and is enabled when the agent has a wallet.
async def test_cta_renders_price_and_is_enabled(client, db):
    await _seed_one(db, 1)
    body = _detail(client, 1)
    assert "Hire for $1.00" in body
    assert 'id="hire-cta"' in body
    assert "disabled" not in body
    # W3 — redirect target wiring: http(s) agent_url exposed to the signer.
    assert f'data-agent-url="{_AGENT_URL}"' in body
    assert 'data-agent-id="56:0x8004A169FB4a3325136EB29fA0ceB6D2e539a432:1"' in body
    assert "data-csrf=" in body


# W1 (edge) — no wallet: CTA disabled with a hint.
async def test_cta_disabled_without_wallet(client, db):
    await _seed_one(db, 2, wallet=None)
    body = _detail(client, 2)
    assert 'id="hire-cta"' in body
    assert "disabled" in body
    assert "no payment wallet (payTo) is registered" in body
    assert "Hire for $1.00" in body


# W4 — status UI container with all lifecycle states.
async def test_status_ui_present(client, db):
    await _seed_one(db, 3)
    body = _detail(client, 3)
    assert 'id="hire-status"' in body
    assert 'data-state="idle"' in body
    for state in ("status-idle", "status-pending", "status-paid", "status-failed"):
        assert state in body
    assert 'id="hire-error"' in body


# W2 — signer assets are wired into the page (vendored ethers + payment.js).
async def test_signer_scripts_included(client, db):
    await _seed_one(db, 4)
    body = _detail(client, 4)
    assert "/static/js/ethers-6.14.min.js" in body
    assert "/static/js/payment.js" in body


# W1/W4 — page stays usable when the agent has no wallet (CTA + status only).
async def test_agent_without_wallet_page_still_renders(client, db):
    await _seed_one(db, 5, wallet=None)
    body = _detail(client, 5)
    assert "<html" in body and "Alpha" in body
    assert 'id="hire-status"' in body
