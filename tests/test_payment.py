"""x402 payment unit tests: challenge shape, envelope decode, verify rules.

Spec: `sdd/x402-real-payment/spec` x402-payments (X1-X7) · design id 52 (D4
fixture, D7 validity). Fully offline: eth-account keypair + the frozen
`tests/fixtures/b402_challenge.json`.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.config import U_TOKEN_NAME, U_TOKEN_VERSION, X402_U_TOKEN_ADDRESS_TESTNET, get_settings
from app.errors import (
    AmountMismatch,
    ChallengeExpired,
    InvalidEnvelope,
    PayToMismatch,
    SignatureMismatch,
    UnsupportedRail,
    ValidationError,
    WrongChain,
)
from app.services.payment import (
    MAX_TIMEOUT_SECONDS,
    build_challenge,
    decode_envelope,
    get_token_config,
    verify_payment,
)

FIXTURE = json.loads(
    Path(__file__).parent.joinpath("fixtures", "b402_challenge.json").read_text(encoding="utf-8")
)
_AMOUNT_WEI = int(FIXTURE["accepts"][0]["amount"])
_WEI_UNIT = 10**18


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _build_challenge() -> dict:
    accept = FIXTURE["accepts"][0]
    return build_challenge(
        accept["payTo"],
        FIXTURE["resource"]["url"],
        amount_wei=_AMOUNT_WEI,
        timeout_s=accept["maxTimeoutSeconds"],
        chain_id=97,
    )


# ---------------------------------------------------------------------------
# X1 — challenge shape (frozen to D4)
# ---------------------------------------------------------------------------


def test_challenge_matches_frozen_fixture():
    assert _build_challenge() == FIXTURE


def test_challenge_uses_testnet_token_and_timeout():
    challenge = _build_challenge()
    accept = challenge["accepts"][0]
    assert challenge["x402Version"] == 2
    assert challenge["error"] == "payment required"
    assert accept["scheme"] == "exact" and accept["network"] == "eip155:97"
    assert accept["asset"] == X402_U_TOKEN_ADDRESS_TESTNET
    assert accept["maxTimeoutSeconds"] == 300
    assert accept["extra"] == {
        "name": U_TOKEN_NAME,
        "version": U_TOKEN_VERSION,
        "assetTransferMethod": "eip3009",
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        {"pay_to": "not-an-address"},
        {"timeout_s": 0},
        {"timeout_s": MAX_TIMEOUT_SECONDS + 1},
        {"amount_wei": -1},
    ],
)
def test_challenge_rejects_invalid_inputs(kwargs):
    base = {
        "pay_to": FIXTURE["accepts"][0]["payTo"],
        "resource_url": FIXTURE["resource"]["url"],
        "amount_wei": _AMOUNT_WEI,
        "chain_id": 97,
    }
    base.update(kwargs)
    with pytest.raises(ValidationError):
        build_challenge(**base)


# Model-A marketplace fee: a second accept is appended with the fee amount.
def test_challenge_with_fee_two_accepts():
    fee_wallet = "0x" + "88" * 20
    challenge = build_challenge(
        FIXTURE["accepts"][0]["payTo"],
        FIXTURE["resource"]["url"],
        amount_wei=_AMOUNT_WEI,
        chain_id=97,
        fee_pay_to=fee_wallet,
        fee_amount_wei=3 * 10**16,
    )
    assert len(challenge["accepts"]) == 2
    agent_accept, fee_accept = challenge["accepts"]
    assert agent_accept["payTo"] == FIXTURE["accepts"][0]["payTo"]
    assert int(agent_accept["amount"]) == _AMOUNT_WEI
    assert fee_accept["payTo"] == fee_wallet
    assert int(fee_accept["amount"]) == 3 * 10**16
    # Same network/asset/window as the hire payment.
    assert fee_accept["network"] == agent_accept["network"]
    assert fee_accept["asset"] == agent_accept["asset"]
    assert fee_accept["maxTimeoutSeconds"] == agent_accept["maxTimeoutSeconds"]


def test_challenge_fee_rejects_bad_wallet_or_amount():
    base = {
        "pay_to": FIXTURE["accepts"][0]["payTo"],
        "resource_url": FIXTURE["resource"]["url"],
        "amount_wei": _AMOUNT_WEI,
        "chain_id": 97,
        "fee_pay_to": "not-an-address",
        "fee_amount_wei": 3 * 10**16,
    }
    with pytest.raises(ValidationError):
        build_challenge(**base)
    base["fee_pay_to"] = "0x" + "88" * 20
    base["fee_amount_wei"] = -1
    with pytest.raises(ValidationError):
        build_challenge(**base)


# Decode: payload.fee is normalized into DecodedPayment.fee.
def test_decode_envelope_with_fee(payer, signed_envelope):
    fee_wallet = "0x" + "88" * 20
    challenge = build_challenge(
        FIXTURE["accepts"][0]["payTo"],
        FIXTURE["resource"]["url"],
        amount_wei=_AMOUNT_WEI,
        chain_id=97,
        fee_pay_to=fee_wallet,
        fee_amount_wei=3 * 10**16,
    )
    decoded = decode_envelope(signed_envelope(payer, challenge, include_fee=True))
    assert decoded.fee is not None
    assert decoded.fee.payer == payer.address
    assert decoded.fee.amount == 3 * 10**16
    assert decoded.fee.authorization["to"] == fee_wallet
    assert decoded.fee.chain_id == 97


# Decode: a fee signed by a different payer is rejected.
def test_decode_envelope_fee_wrong_payer_rejected(payer, signed_envelope):
    import base64
    import json
    import time

    from eth_account import Account as EthAccount

    from tests.conftest import _sign_authorization

    fee_wallet = "0x" + "88" * 20
    challenge = build_challenge(
        FIXTURE["accepts"][0]["payTo"],
        FIXTURE["resource"]["url"],
        amount_wei=_AMOUNT_WEI,
        chain_id=97,
        fee_pay_to=fee_wallet,
        fee_amount_wei=3 * 10**16,
    )
    envelope = json.loads(
        base64.b64decode(signed_envelope(payer, challenge, include_fee=True)).decode("utf-8")
    )
    other = EthAccount.create()
    fee_auth, fee_sig = _sign_authorization(
        other, challenge["accepts"][1], now=int(time.time())
    )
    envelope["payload"]["fee"] = {"signature": fee_sig, "authorization": fee_auth}
    evil = base64.b64encode(json.dumps(envelope).encode("utf-8")).decode("ascii")
    from app.errors import InvalidEnvelope

    with pytest.raises(InvalidEnvelope):
        decode_envelope(evil)


# ---------------------------------------------------------------------------
# X3 — envelope decode (X-PAYMENT / PAYMENT-SIGNATURE dialects)
# ---------------------------------------------------------------------------


def test_decode_x_payment_envelope(payer, signed_envelope):
    envelope = signed_envelope(payer, _build_challenge())
    decoded = decode_envelope(envelope)
    assert decoded.rail == "eip3009"
    assert decoded.payer == payer.address
    assert decoded.amount == _AMOUNT_WEI
    assert decoded.token == X402_U_TOKEN_ADDRESS_TESTNET
    assert decoded.chain_id == 97
    assert decoded.authorization["from"] == payer.address
    assert decoded.authorization["nonce"].startswith("0x")
    assert decoded.signature.startswith("0x")


def test_decode_accepts_payment_signature_dialect(payer, signed_envelope):
    # PAYMENT-SIGNATURE is the same envelope shape; the router picks the
    # header name (X3) — decode is header-agnostic.
    decoded = decode_envelope(signed_envelope(payer, _build_challenge()))
    assert decoded.payer == payer.address


def test_decode_missing_asset_defaults_to_zero_address(payer, signed_envelope):
    challenge = _build_challenge()
    accept = dict(challenge["accepts"][0])
    del accept["asset"]
    challenge["accepts"] = [accept]
    decoded = decode_envelope(signed_envelope(payer, challenge))
    assert decoded.token == "0x0000000000000000000000000000000000000000"


@pytest.mark.parametrize(
    "mutation",
    [
        ("not base64!!", None),
        ("{}", None),  # valid base64, not JSON
        ("[]", None),  # JSON but not an object
    ],
)
def test_decode_rejects_malformed_envelopes(mutation):
    payload, _ = mutation
    if payload == "not base64!!":
        header = payload
    else:
        header = base64.b64encode(payload.encode()).decode()
    with pytest.raises(InvalidEnvelope):
        decode_envelope(header)


def _envelope_json(payer, challenge) -> dict:
    accept = challenge["accepts"][0]
    now = int(datetime.now(tz=timezone.utc).timestamp())
    return {
        "x402Version": 2,
        "scheme": accept["scheme"],
        "network": accept["network"],
        "resource": challenge["resource"],
        "accepted": accept,
        "payload": {
            "signature": "0x" + "ab" * 65,
            "authorization": {
                "from": payer.address,
                "to": accept["payTo"],
                "value": str(_AMOUNT_WEI),
                "validAfter": str(now - 120),
                "validBefore": str(now + 300),
                "nonce": "0x" + "cd" * 32,
            },
        },
    }


@pytest.mark.parametrize("drop", ["resource", "payload", "authorization", "network"])
def test_decode_rejects_missing_fields(payer, drop):
    env = _envelope_json(payer, _build_challenge())
    if drop == "authorization":
        del env["payload"]["authorization"]
    else:
        del env[drop]
    header = base64.b64encode(json.dumps(env).encode()).decode()
    with pytest.raises(InvalidEnvelope):
        decode_envelope(header)


def test_decode_rejects_permit2_rail(payer):
    env = _envelope_json(payer, _build_challenge())
    env["payload"]["permit2Authorization"] = {"permitted": {}, "signature": "0x"}
    header = base64.b64encode(json.dumps(env).encode()).decode()
    with pytest.raises(UnsupportedRail):
        decode_envelope(header)


# ---------------------------------------------------------------------------
# X4/X7 — signature verification (verify.ts rule order)
# ---------------------------------------------------------------------------


def test_verify_happy_path(payer, signed_envelope):
    challenge = _build_challenge()
    decoded = decode_envelope(signed_envelope(payer, challenge))
    verify_payment(
        decoded,
        chain_id=97,
        token_cfg=get_token_config(get_settings(), 97),
        pay_to=challenge["accepts"][0]["payTo"],
        amount_wei=_AMOUNT_WEI,
        payer=payer.address,
        now=_now(),
    )


def test_verify_wrong_signer_rejected(payer, signed_envelope):
    from eth_account import Account as _Account

    from tests.conftest import _new_address_and_key

    stranger = _Account.from_key(_new_address_and_key()[1])
    challenge = _build_challenge()
    decoded = decode_envelope(signed_envelope(stranger, challenge))
    with pytest.raises(SignatureMismatch):
        verify_payment(
            decoded,
            chain_id=97,
            token_cfg=get_token_config(get_settings(), 97),
            pay_to=challenge["accepts"][0]["payTo"],
            amount_wei=_AMOUNT_WEI,
            payer=payer.address,
            now=_now(),
        )


def test_verify_expired_authorization(payer, signed_envelope):
    challenge = _build_challenge()
    decoded = decode_envelope(
        signed_envelope(payer, challenge, valid_before=int(_now().timestamp()) - 10)
    )
    with pytest.raises(ChallengeExpired):
        verify_payment(
            decoded,
            chain_id=97,
            token_cfg=get_token_config(get_settings(), 97),
            pay_to=challenge["accepts"][0]["payTo"],
            amount_wei=_AMOUNT_WEI,
            payer=payer.address,
            now=_now(),
        )


def test_verify_not_yet_valid_authorization(payer, signed_envelope):
    challenge = _build_challenge()
    decoded = decode_envelope(
        signed_envelope(payer, challenge, valid_after=int(_now().timestamp()) + 60)
    )
    with pytest.raises(ChallengeExpired):
        verify_payment(
            decoded,
            chain_id=97,
            token_cfg=get_token_config(get_settings(), 97),
            pay_to=challenge["accepts"][0]["payTo"],
            amount_wei=_AMOUNT_WEI,
            payer=payer.address,
            now=_now(),
        )


def test_verify_amount_mismatch(payer, signed_envelope):
    challenge = _build_challenge()
    decoded = decode_envelope(signed_envelope(payer, challenge, value=_AMOUNT_WEI + 1))
    with pytest.raises(AmountMismatch):
        verify_payment(
            decoded,
            chain_id=97,
            token_cfg=get_token_config(get_settings(), 97),
            pay_to=challenge["accepts"][0]["payTo"],
            amount_wei=_AMOUNT_WEI,
            payer=payer.address,
            now=_now(),
        )


def test_verify_pay_to_mismatch(payer, signed_envelope):
    challenge = _build_challenge()
    other_to = "0x" + "11" * 20
    decoded = decode_envelope(signed_envelope(payer, challenge, to=other_to))
    with pytest.raises(PayToMismatch):
        verify_payment(
            decoded,
            chain_id=97,
            token_cfg=get_token_config(get_settings(), 97),
            pay_to=challenge["accepts"][0]["payTo"],
            amount_wei=_AMOUNT_WEI,
            payer=payer.address,
            now=_now(),
        )


def test_verify_wrong_chain(payer, signed_envelope):
    challenge = _build_challenge()
    decoded = decode_envelope(signed_envelope(payer, challenge, network="eip155:56"))
    with pytest.raises(WrongChain):
        verify_payment(
            decoded,
            chain_id=97,
            token_cfg=get_token_config(get_settings(), 97),
            pay_to=challenge["accepts"][0]["payTo"],
            amount_wei=_AMOUNT_WEI,
            payer=payer.address,
            now=_now(),
        )


def test_verify_wrong_token(payer, signed_envelope):
    challenge = _build_challenge()
    decoded = decode_envelope(signed_envelope(payer, challenge, token="0x" + "22" * 20))
    with pytest.raises(WrongChain):
        verify_payment(
            decoded,
            chain_id=97,
            token_cfg=get_token_config(get_settings(), 97),
            pay_to=challenge["accepts"][0]["payTo"],
            amount_wei=_AMOUNT_WEI,
            payer=payer.address,
            now=_now(),
        )


def test_verify_survives_a_round_trip_within_window(payer, signed_envelope):
    """The signed envelope must still verify a minute later (no race)."""
    challenge = _build_challenge()
    envelope = signed_envelope(payer, challenge, valid_before=int(_now().timestamp()) + 300 + 60)
    decoded = decode_envelope(envelope)
    verify_payment(
        decoded,
        chain_id=97,
        token_cfg=get_token_config(get_settings(), 97),
        pay_to=challenge["accepts"][0]["payTo"],
        amount_wei=_AMOUNT_WEI,
        payer=payer.address,
        now=_now() + timedelta(seconds=60),
    )
