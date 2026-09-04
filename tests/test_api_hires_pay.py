"""Hire pay API tests: happy, double-pay, expired, no-wallet, auth, TTL sweep.

Spec: `sdd/x402-real-payment/spec` hires-x402 (H1-H4) + x402-payments
(X2/X5/X6/X7) · design id 52 (error table D6, lazy TTL H3). Fully offline:
the broadcaster is overridden with FakeBroadcaster; envelopes are signed
locally with the signed-in user's keypair.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db.models.agent import (
    BSC_CHAIN_ID,
    BSC_IDENTITY_REGISTRY,
    AgentCache,
    build_agent_id,
)
from app.db.models.hired_agent import HiredAgent, HiredStatus
from app.services.auth import issue_csrf
from tests.conftest import _now, _sign_in_with_key

_PAY_TO = "0x" + "77" * 20


async def _seed_agent(session, token_id: int = 1, wallet: str = _PAY_TO) -> str:
    aid = build_agent_id(56, BSC_IDENTITY_REGISTRY, token_id)
    session.add(
        AgentCache(
            agent_id=aid,
            chain_id=BSC_CHAIN_ID,
            token_id=token_id,
            registry_address=BSC_IDENTITY_REGISTRY,
            name=f"A{token_id}",
            agent_wallet=wallet,
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


def _create_hire(client, cookie: str, aid: str) -> dict:
    r = client.post("/api/hires", json={"agent_id": aid}, cookies=_ck(cookie), headers=_ch(cookie))
    assert r.status_code == 201, r.text
    return r.json()


# H1 — happy pay: 200 + status paid + tx_hash, one broadcast.
async def test_pay_happy_path(client, db, fake_broadcaster, signed_envelope):
    account, _address, cookie = _sign_in_with_key(client)
    aid = await _seed_agent(db, 1)
    hire = _create_hire(client, cookie, aid)
    assert hire["pay_to"] == _PAY_TO and hire["challenge"] is not None

    pay = client.post(
        f"/api/hires/{hire['id']}/pay",
        cookies=_ck(cookie),
        headers={**_ch(cookie), "X-PAYMENT": signed_envelope(account, hire["challenge"])},
    )
    assert pay.status_code == 200, pay.text
    assert pay.json()["status"] == "paid"
    assert pay.json()["tx_hash"] == fake_broadcaster.tx_hash
    assert len(fake_broadcaster.calls) == 1


# Model-A marketplace fee: challenge carries 2 accepts (agent + fee wallet),
# pay verifies both and broadcasts fee first, then the hire payment.
async def test_pay_with_marketplace_fee(
    client, db, fake_broadcaster, signed_envelope, monkeypatch
):
    from app.config import _settings_cache

    _FEE_WALLET = "0x" + "88" * 20
    monkeypatch.setenv("X402_FEE_WALLET", _FEE_WALLET)
    _settings_cache.cache_clear()
    try:
        account, _address, cookie = _sign_in_with_key(client)
        aid = await _seed_agent(db, 20)
        hire = _create_hire(client, cookie, aid)
        accepts = hire["challenge"]["accepts"]
        assert len(accepts) == 2
        assert accepts[1]["payTo"] == _FEE_WALLET
        assert int(accepts[1]["amount"]) == int(0.03 * 10**18)

        pay = client.post(
            f"/api/hires/{hire['id']}/pay",
            cookies=_ck(cookie),
            headers={
                **_ch(cookie),
                "X-PAYMENT": signed_envelope(account, hire["challenge"], include_fee=True),
            },
        )
        assert pay.status_code == 200, pay.text
        assert pay.json()["status"] == "paid"
        assert len(fake_broadcaster.calls) == 2
        fee_call, main_call = fake_broadcaster.calls
        assert fee_call["decoded"].authorization["to"] == _FEE_WALLET
        assert main_call["decoded"].authorization["to"] == _PAY_TO
    finally:
        _settings_cache.cache_clear()


# A fee envelope sent with no fee wallet configured is rejected (409).
async def test_pay_fee_without_fee_wallet_config(
    client, db, fake_broadcaster, signed_envelope, monkeypatch
):
    import base64
    import json
    import time

    from tests.conftest import _sign_authorization

    from app.config import _settings_cache

    monkeypatch.setenv("X402_FEE_WALLET", "")
    _settings_cache.cache_clear()
    try:
        account, _address, cookie = _sign_in_with_key(client)
        aid = await _seed_agent(db, 21)
        # No fee wallet → the challenge has a single accept, so we craft a
        # fee envelope against that accept (client misbehavior).
        hire = _create_hire(client, cookie, aid)
        challenge = hire["challenge"]
        assert len(challenge["accepts"]) == 1
        envelope = json.loads(
            base64.b64decode(signed_envelope(account, challenge)).decode("utf-8")
        )
        fee_auth, fee_sig = _sign_authorization(
            account, challenge["accepts"][0], now=int(time.time())
        )
        envelope["payload"]["fee"] = {"signature": fee_sig, "authorization": fee_auth}
        evil = base64.b64encode(json.dumps(envelope).encode("utf-8")).decode("ascii")
        pay = client.post(
            f"/api/hires/{hire['id']}/pay",
            cookies=_ck(cookie),
            headers={**_ch(cookie), "X-PAYMENT": evil},
        )
        assert pay.status_code == 409, pay.text
        assert len(fake_broadcaster.calls) == 0
    finally:
        _settings_cache.cache_clear()


# X3 — PAYMENT-SIGNATURE dialect header also accepted.
async def test_pay_via_payment_signature_header(client, db, fake_broadcaster, signed_envelope):
    account, _address, cookie = _sign_in_with_key(client)
    aid = await _seed_agent(db, 2)
    hire = _create_hire(client, cookie, aid)
    pay = client.post(
        f"/api/hires/{hire['id']}/pay",
        cookies=_ck(cookie),
        headers={**_ch(cookie), "PAYMENT-SIGNATURE": signed_envelope(account, hire["challenge"])},
    )
    assert pay.status_code == 200, pay.text
    assert pay.json()["status"] == "paid"


# H4/X6 — double-pay: 409 already_paid, no second broadcast.
async def test_pay_double_pay_409(client, db, fake_broadcaster, signed_envelope):
    account, _address, cookie = _sign_in_with_key(client)
    aid = await _seed_agent(db, 3)
    hire = _create_hire(client, cookie, aid)
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
async def test_pay_expired_challenge_409(client, db, fake_broadcaster, signed_envelope):
    account, _address, cookie = _sign_in_with_key(client)
    aid = await _seed_agent(db, 4)
    hire = _create_hire(client, cookie, aid)
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


# X2 — agent without wallet: 422 no_pay_to, no challenge issued.
async def test_create_hire_no_wallet_422(client, db):
    _account, _address, cookie = _sign_in_with_key(client)
    aid = await _seed_agent(db, 5, wallet=None)
    r = client.post("/api/hires", json={"agent_id": aid}, cookies=_ck(cookie), headers=_ch(cookie))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "no_pay_to"


# H4 — unauthenticated pay → 401.
async def test_pay_unauth_401(client):
    r = client.post("/api/hires/1/pay", headers={"X-PAYMENT": "x"})
    assert r.status_code == 401


# H4 — missing CSRF → 403.
async def test_pay_missing_csrf_403(client, db):
    _account, _address, cookie = _sign_in_with_key(client)
    aid = await _seed_agent(db, 6)
    hire = _create_hire(client, cookie, aid)
    r = client.post(
        f"/api/hires/{hire['id']}/pay",
        cookies=_ck(cookie),
        headers={"X-PAYMENT": "e30="},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


# D6 — empty facilitator key: 503 payment_gateway_unconfigured.
async def test_pay_gateway_unconfigured_503(
    client, db, monkeypatch, fake_broadcaster, signed_envelope
):
    monkeypatch.setenv("X402_FACILITATOR_KEY", "")
    from app.config import _settings_cache

    _settings_cache.cache_clear()
    account, _address, cookie = _sign_in_with_key(client)
    aid = await _seed_agent(db, 7)
    hire = _create_hire(client, cookie, aid)
    pay = client.post(
        f"/api/hires/{hire['id']}/pay",
        cookies=_ck(cookie),
        headers={**_ch(cookie), "X-PAYMENT": signed_envelope(account, hire["challenge"])},
    )
    assert pay.status_code == 503
    assert pay.json()["error"]["code"] == "payment_gateway_unconfigured"
    assert len(fake_broadcaster.calls) == 0


# H3 — lazy TTL sweep: creating a hire cancels the user's expired pendings.
async def test_ttl_sweep_cancels_expired_pending(client, db):
    _account, _address, cookie = _sign_in_with_key(client)
    aid = await _seed_agent(db, 8)
    first = _create_hire(client, cookie, aid)
    row = await db.get(HiredAgent, first["id"])
    row.challenge_expiry = datetime.now(tz=timezone.utc) - timedelta(seconds=60)
    await db.commit()

    second = _create_hire(client, cookie, aid)

    # The sweep ran in the request's session — refresh to see the new state.
    h1 = await db.get(HiredAgent, first["id"])
    await db.refresh(h1)
    h2 = await db.get(HiredAgent, second["id"])
    assert h1.status == HiredStatus.CANCELLED
    assert h2.status == HiredStatus.PENDING


# H2 — status endpoint exposes payment fields after the pay.
async def test_get_hire_status_after_pay(client, db, fake_broadcaster, signed_envelope):
    account, _address, cookie = _sign_in_with_key(client)
    aid = await _seed_agent(db, 9)
    hire = _create_hire(client, cookie, aid)
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
    assert body["pay_to"] == _PAY_TO
