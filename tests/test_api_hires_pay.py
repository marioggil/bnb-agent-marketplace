"""Hire pay API tests: happy, double-pay, expired, no-wallet, auth, TTL sweep.

Spec: `sdd/x402-real-payment/spec` hires-x402 (H1-H4) + x402-payments
(X2/X5/X6/X7) · design id 52 (error table D6, lazy TTL H3). Fully offline:
the broadcaster is overridden with FakeBroadcaster; envelopes are signed
locally with the signed-in user's keypair.

x402-remove-fee-multichain: every hire is offer-driven and settles on the
hire's recorded chain via the rail map; no fee accept / fee verify / fee
broadcast (R5/R6/AC-5).
"""

from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.db.models.agent import (
    BSC_CHAIN_ID,
    BSC_IDENTITY_REGISTRY,
    AgentCache,
    build_agent_id,
)
from app.db.models.hired_agent import HiredAgent, HiredStatus
from app.services.auth import issue_csrf
from tests.conftest import (
    _now,
    _sign_authorization,
    _sign_in_with_key,
    build_signed_envelope,
    payai_header,
)

_PAY_TO = "0x" + "77" * 20
_BASE_CHAIN = 8453
_BASE_USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
_OFFER_PAYTO = "0x71C7656EC7ab88b098defB751B7401B5f6d8976F"
_A2A = "https://example.com/a2a/card"
_FEE_WALLET = "0x" + "88" * 20


async def _seed_agent(
    session, token_id: int = 1, wallet: str = _PAY_TO, *, endpoint: str | None = _A2A
) -> str:
    aid = build_agent_id(56, BSC_IDENTITY_REGISTRY, token_id)
    session.add(
        AgentCache(
            agent_id=aid,
            chain_id=BSC_CHAIN_ID,
            token_id=token_id,
            registry_address=BSC_IDENTITY_REGISTRY,
            name=f"A{token_id}",
            agent_wallet=wallet,
            x402_supported=endpoint is not None,
            a2a_endpoint=endpoint,
            supported_protocols=[],
            cross_chain_versions=[],
            raw={},
            created_at=_now(),
            updated_at=_now(),
        )
    )
    await session.commit()
    return aid


def _ck(cookie: str) -> dict:
    return {"bnb_agent_session": cookie}


def _ch(cookie: str) -> dict:
    return {"X-CSRF-Token": issue_csrf(cookie)}


def _mock_offer(respx_mock, endpoint: str, chain_id: int, token_address: str) -> None:
    respx_mock.get(endpoint).respond(
        402,
        headers={
            "payment-required": payai_header(
                asset=token_address, network=f"eip155:{chain_id}"
            )
        },
    )


def _create_hire(
    client,
    cookie: str,
    aid: str,
    respx_mock,
    *,
    chain_id: int = _BASE_CHAIN,
    endpoint: str = _A2A,
) -> dict:
    token_address = get_settings().x402_rail_for(chain_id).token_address
    _mock_offer(respx_mock, endpoint, chain_id, token_address)
    r = client.post("/api/hires", json={"agent_id": aid}, cookies=_ck(cookie), headers=_ch(cookie))
    assert r.status_code == 201, r.text
    return r.json()


def _envelope_with_legacy_fee(account, challenge: dict, fee_wallet: str) -> str:
    """Signed single-accept envelope plus a well-formed legacy payload.fee."""
    accept = challenge["accepts"][0]
    now = int(time.time())
    envelope = json.loads(
        base64.b64decode(build_signed_envelope(account, challenge)).decode("utf-8")
    )
    fee_auth, fee_sig = _sign_authorization(
        account, accept, now=now, to=fee_wallet, value=int(accept["amount"])
    )
    envelope["payload"]["fee"] = {"signature": fee_sig, "authorization": fee_auth}
    return base64.b64encode(json.dumps(envelope).encode("utf-8")).decode("ascii")


# H1 — happy pay: 200 + status paid + tx_hash, one broadcast on the offer chain.
async def test_pay_happy_path(client, db, fake_broadcaster, signed_envelope, respx_mock):
    account, _address, cookie = _sign_in_with_key(client)
    aid = await _seed_agent(db, 1)
    hire = _create_hire(client, cookie, aid, respx_mock)
    assert hire["pay_to"] == _OFFER_PAYTO and hire["challenge"] is not None
    assert hire["network_agent"] == f"eip155:{_BASE_CHAIN}"

    pay = client.post(
        f"/api/hires/{hire['id']}/pay",
        cookies=_ck(cookie),
        headers={**_ch(cookie), "X-PAYMENT": signed_envelope(account, hire["challenge"])},
    )
    assert pay.status_code == 200, pay.text
    assert pay.json()["status"] == "paid"
    assert pay.json()["tx_hash"] == fake_broadcaster.tx_hash
    assert len(fake_broadcaster.calls) == 1


# T12 — settle on the hire's chain via the rail map, no fee (AC-5/R6).
async def test_pay_hire_verifies_against_hire_chain_and_skips_fee(
    client, db, fake_broadcaster, signed_envelope, respx_mock
):
    account, _address, cookie = _sign_in_with_key(client)
    aid = await _seed_agent(db, 11)
    hire = _create_hire(client, cookie, aid, respx_mock, chain_id=_BASE_CHAIN)
    accepts = hire["challenge"]["accepts"]
    assert len(accepts) == 1  # no fee accept
    assert accepts[0]["network"] == f"eip155:{_BASE_CHAIN}"
    assert accepts[0]["asset"] == _BASE_USDC

    pay = client.post(
        f"/api/hires/{hire['id']}/pay",
        cookies=_ck(cookie),
        headers={**_ch(cookie), "X-PAYMENT": signed_envelope(account, hire["challenge"])},
    )
    assert pay.status_code == 200, pay.text
    assert pay.json()["status"] == "paid"
    # Exactly one broadcast, against the rail-map RPC + Base USDC token config.
    assert len(fake_broadcaster.calls) == 1
    call = fake_broadcaster.calls[0]
    assert call["rpc_url"] == get_settings().x402_rail_for(_BASE_CHAIN).rpc_url
    assert call["token_cfg"].address == _BASE_USDC
    assert call["token_cfg"].name == "USD Coin"
    assert call["token_cfg"].version == "2"
    assert call["decoded"].authorization["to"] == _OFFER_PAYTO


# T14 — legacy payload.fee is ignored: still paid, one broadcast, fee wallet
# never appears in any broadcast (R6/R9).
async def test_pay_legacy_fee_ignored(client, db, fake_broadcaster, respx_mock):
    account, _address, cookie = _sign_in_with_key(client)
    aid = await _seed_agent(db, 12)
    hire = _create_hire(client, cookie, aid, respx_mock, chain_id=_BASE_CHAIN)
    envelope = _envelope_with_legacy_fee(account, hire["challenge"], _FEE_WALLET)

    pay = client.post(
        f"/api/hires/{hire['id']}/pay",
        cookies=_ck(cookie),
        headers={**_ch(cookie), "X-PAYMENT": envelope},
    )
    assert pay.status_code == 200, pay.text
    assert pay.json()["status"] == "paid"
    assert len(fake_broadcaster.calls) == 1
    call = fake_broadcaster.calls[0]
    assert call["decoded"].fee is not None  # decode kept parsing it (back-compat)
    assert call["decoded"].authorization["to"] == _OFFER_PAYTO
    assert all(c["decoded"].authorization["to"] != _FEE_WALLET for c in fake_broadcaster.calls)


# T14 — wrong-chain envelope rejected visibly; zero broadcasts.
async def test_pay_wrong_chain_rejected(client, db, fake_broadcaster, signed_envelope, respx_mock):
    account, _address, cookie = _sign_in_with_key(client)
    aid = await _seed_agent(db, 13)
    hire = _create_hire(client, cookie, aid, respx_mock, chain_id=_BASE_CHAIN)
    # Envelope signed on Polygon 137 while the hire is on Base 8453.
    pay = client.post(
        f"/api/hires/{hire['id']}/pay",
        cookies=_ck(cookie),
        headers={
            **_ch(cookie),
            "X-PAYMENT": signed_envelope(account, hire["challenge"], network="eip155:137"),
        },
    )
    assert pay.status_code == 403, pay.text
    assert pay.json()["error"]["code"] == "payment_wrong_chain"
    assert len(fake_broadcaster.calls) == 0


# X3 — PAYMENT-SIGNATURE dialect header also accepted.
async def test_pay_via_payment_signature_header(client, db, fake_broadcaster, signed_envelope, respx_mock):
    account, _address, cookie = _sign_in_with_key(client)
    aid = await _seed_agent(db, 2)
    hire = _create_hire(client, cookie, aid, respx_mock)
    pay = client.post(
        f"/api/hires/{hire['id']}/pay",
        cookies=_ck(cookie),
        headers={**_ch(cookie), "PAYMENT-SIGNATURE": signed_envelope(account, hire["challenge"])},
    )
    assert pay.status_code == 200, pay.text
    assert pay.json()["status"] == "paid"


# H4/X6 — double-pay: 409 already_paid, no second broadcast.
async def test_pay_double_pay_409(client, db, fake_broadcaster, signed_envelope, respx_mock):
    account, _address, cookie = _sign_in_with_key(client)
    aid = await _seed_agent(db, 3)
    hire = _create_hire(client, cookie, aid, respx_mock)
    envelope = signed_envelope(account, hire["challenge"])
    first = client.post(
        f"/api/hires/{hire['id']}/pay",
        cookies=_ck(cookie),
        headers={**_ch(cookie), "X-PAYMENT": envelope},
    )
    assert first.status_code == 200
    second = client.post(
        f"/api/hires/{hire['id']}/pay",
        cookies=_ck(cookie),
        headers={**_ch(cookie), "X-PAYMENT": envelope},
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "already_paid"
    assert len(fake_broadcaster.calls) == 1  # no re-broadcast


# X7 — expired challenge: 409 before decode/broadcast.
async def test_pay_expired_challenge_409(client, db, fake_broadcaster, signed_envelope, respx_mock):
    account, _address, cookie = _sign_in_with_key(client)
    aid = await _seed_agent(db, 4)
    hire = _create_hire(client, cookie, aid, respx_mock)
    row = await db.get(HiredAgent, hire["id"])
    row.challenge_expiry = datetime.now(tz=timezone.utc) - timedelta(seconds=60)
    await db.commit()
    pay = client.post(
        f"/api/hires/{hire['id']}/pay",
        cookies=_ck(cookie),
        headers={**_ch(cookie), "X-PAYMENT": signed_envelope(account, hire["challenge"])},
    )
    assert pay.status_code == 409
    assert pay.json()["error"]["code"] == "challenge_expired"
    assert len(fake_broadcaster.calls) == 0


# X2/R4 — agent with no endpoint and no wallet → no offer → 503
# agent_offer_unavailable (the flat fallback and the no_pay_to 422 are gone).
async def test_create_hire_unavailable_without_offer_503(client, db):
    _account, _address, cookie = _sign_in_with_key(client)
    aid = await _seed_agent(db, 5, wallet=None, endpoint=None)
    r = client.post("/api/hires", json={"agent_id": aid}, cookies=_ck(cookie), headers=_ch(cookie))
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "agent_offer_unavailable"


# H4 — unauthenticated pay → 401.
async def test_pay_unauth_401(client):
    r = client.post("/api/hires/1/pay", headers={"X-PAYMENT": "x"})
    assert r.status_code == 401


# H4 — missing CSRF → 403.
async def test_pay_missing_csrf_403(client, db, respx_mock):
    account, _address, cookie = _sign_in_with_key(client)
    aid = await _seed_agent(db, 6)
    hire = _create_hire(client, cookie, aid, respx_mock)
    r = client.post(
        f"/api/hires/{hire['id']}/pay",
        cookies=_ck(cookie),
        headers={"X-PAYMENT": "e30="},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


# D6 — empty facilitator key: 503 payment_gateway_unconfigured.
async def test_pay_gateway_unconfigured_503(
    client, db, monkeypatch, fake_broadcaster, signed_envelope, respx_mock
):
    monkeypatch.setenv("X402_FACILITATOR_KEY", "")
    from app.config import _settings_cache

    _settings_cache.cache_clear()
    account, _address, cookie = _sign_in_with_key(client)
    aid = await _seed_agent(db, 7)
    hire = _create_hire(client, cookie, aid, respx_mock)
    pay = client.post(
        f"/api/hires/{hire['id']}/pay",
        cookies=_ck(cookie),
        headers={**_ch(cookie), "X-PAYMENT": signed_envelope(account, hire["challenge"])},
    )
    assert pay.status_code == 503
    assert pay.json()["error"]["code"] == "payment_gateway_unconfigured"
    assert len(fake_broadcaster.calls) == 0


# H3 — lazy TTL sweep: creating a hire cancels the user's expired pendings.
async def test_ttl_sweep_cancels_expired_pending(client, db, respx_mock):
    account, _address, cookie = _sign_in_with_key(client)
    aid = await _seed_agent(db, 8)
    first = _create_hire(client, cookie, aid, respx_mock)
    row = await db.get(HiredAgent, first["id"])
    row.challenge_expiry = datetime.now(tz=timezone.utc) - timedelta(seconds=60)
    await db.commit()

    second = _create_hire(client, cookie, aid, respx_mock)

    # The sweep ran in the request's session — refresh to see the new state.
    h1 = await db.get(HiredAgent, first["id"])
    await db.refresh(h1)
    h2 = await db.get(HiredAgent, second["id"])
    assert h1.status == HiredStatus.CANCELLED
    assert h2.status == HiredStatus.PENDING


# H2 — status endpoint exposes payment fields after the pay.
async def test_get_hire_status_after_pay(client, db, fake_broadcaster, signed_envelope, respx_mock):
    account, _address, cookie = _sign_in_with_key(client)
    aid = await _seed_agent(db, 9)
    hire = _create_hire(client, cookie, aid, respx_mock)
    client.post(
        f"/api/hires/{hire['id']}/pay",
        cookies=_ck(cookie),
        headers={**_ch(cookie), "X-PAYMENT": signed_envelope(account, hire["challenge"])},
    )
    status = client.get(f"/api/hires/{hire['id']}", cookies=_ck(cookie))
    assert status.status_code == 200
    body = status.json()
    assert body["status"] == "paid"
    assert body["tx_hash"] == fake_broadcaster.tx_hash
    assert body["pay_to"] == _OFFER_PAYTO