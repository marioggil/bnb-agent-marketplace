"""B402 merchant core: challenge builder, envelope decode, verify, broadcasters.

Spec: `sdd/x402-real-payment/spec` (x402-payments + hires-x402) · Design id 52
(D3 contract, D4 frozen fixture, D7 validity). B402 has no formal spec — the
wire is pinned to @altananetwork/x402-server (challenge.ts / decode.ts /
verify.ts / settle.ts). Frozen shapes:

    challenge: {x402Version: 2, error: "payment required", resource: {url},
                accepts: [{scheme: "exact", network: "eip155:<chain>", asset,
                           payTo, amount: "<wei>", maxTimeoutSeconds,
                           extra: {name, version, assetTransferMethod: "eip3009"}}]}
    envelope:  base64 JSON {x402Version, scheme, network, resource, accepted,
                payload: {signature, authorization: {from, to, value,
                          validAfter, validBefore, nonce}}}
    settle:    transferWithAuthorization(from, to, value, validAfter,
                           validBefore, nonce, signature) on the $U contract,
                           broadcast by the facilitator EOA, receipt "0x1" ⇒ paid.

Offline-testable: eth-account keypair signs locally; broadcasters sit behind a
Protocol (FakeBroadcaster in tests, OnchainBroadcaster vs the RPC).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, cast

import httpx
from eth_abi.abi import encode as eth_abi_encode
from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils.crypto import keccak

from app.config import U_TOKEN_NAME, U_TOKEN_VERSION, get_settings
from app.errors import (
    AmountMismatch,
    BroadcastFailed,
    ChallengeExpired,
    InvalidEnvelope,
    PayToMismatch,
    SignatureMismatch,
    UnsupportedRail,
    ValidationError,
    WrongChain,
)

#: Rail name on the hire row + challenge `extra` (Q2). eip3009 only in v1.
EIP3009_RAIL: str = "eip3009"
#: Studio buyers refuse windows >600s and backdate validAfter by 120s (D7);
#: default 300s, cap 480s (challenge.ts).
MAX_TIMEOUT_SECONDS: int = 480
DEFAULT_TIMEOUT_SECONDS: int = 300
#: Decode-time placeholder when the buyer omits `accepted.asset` (decode.ts);
#: verify() then resolves the configured token.
ZERO_ADDRESS: str = "0x0000000000000000000000000000000000000000"

#: FiatTokenV2_2-style EIP-3009 with a `bytes` signature (settle.ts).
_TRANSFER_WITH_AUTH_SELECTOR: bytes = keccak(
    b"transferWithAuthorization(address,address,uint256,uint256,uint256,bytes32,bytes)"
)[:4]

_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_HEX_RE = re.compile(r"^0x[0-9a-fA-F]+$")
_NUMERIC_RE = re.compile(r"^\d+$")


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TokenConfig:
    """EIP-712 domain + settlement target of the accepted token ($U)."""

    address: str
    name: str
    version: str


@dataclass(frozen=True)
class DecodedPayment:
    """Normalized payment extracted from an X-PAYMENT envelope (decode.ts)."""

    rail: str
    payer: str
    amount: int
    token: str
    chain_id: int
    authorization: dict[str, Any]
    signature: str
    raw: dict[str, Any]
    #: Optional marketplace-fee payment (same payer, fee wallet recipient).
    #: Present when the challenge carried a second accept and the client
    #: signed it into `payload.fee` (model-A commission).
    fee: "DecodedPayment | None" = None


@dataclass(frozen=True)
class BroadcastResult:
    """Outcome of a settlement broadcast."""

    tx_hash: str


# ---------------------------------------------------------------------------
# Validators (mirror decode.ts asAddress/asNumeric/asHex)
# ---------------------------------------------------------------------------


def _validate_address(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not _ADDRESS_RE.match(value):
        raise ValidationError(f"{field_name} must be a 0x address (0x + 40 hex)")
    return value


def _as_address(value: Any, field: str) -> str:
    if isinstance(value, str) and _ADDRESS_RE.match(value):
        return value
    raise InvalidEnvelope(f"missing/invalid address in {field}")


def _as_numeric(value: Any, field: str) -> int:
    s = str(value) if isinstance(value, int) else value
    if isinstance(s, str) and _NUMERIC_RE.match(s):
        return int(s)
    raise InvalidEnvelope(f"missing/invalid integer in {field}")


def _as_hex(value: Any, field: str) -> str:
    if isinstance(value, str) and _HEX_RE.match(value):
        return value
    raise InvalidEnvelope(f"missing/invalid hex in {field}")


def _chain_id_of(envelope: dict[str, Any]) -> int | None:
    """Chain id from `accepted.network`/`envelope.network` (CAIP-2) or the
    bare `chainId` fallbacks — mirrors decode.ts `chainIdOf`."""
    accepted = envelope.get("accepted") if isinstance(envelope.get("accepted"), dict) else None
    for net in (accepted.get("network") if accepted else None, envelope.get("network")):
        if isinstance(net, str):
            match = re.match(r"^eip155:(\d+)$", net)
            if match:
                return int(match.group(1))
    for holder in (accepted, envelope):
        if not isinstance(holder, dict):
            continue
        cid = holder.get("chainId")
        if cid is not None and _NUMERIC_RE.match(str(cid)):
            return int(cid)
    return None


# ---------------------------------------------------------------------------
# Challenge builder (T2.1 — D4 fixture)
# ---------------------------------------------------------------------------


def get_token_config(settings: Any, chain_id: int) -> TokenConfig:
    """$U config for the chain: pinned address (D2) + EIP-712 domain."""
    return TokenConfig(
        address=settings.x402_u_token_address,
        name=U_TOKEN_NAME,
        version=U_TOKEN_VERSION,
    )


def build_challenge(
    pay_to: str,
    resource_url: str,
    *,
    amount_wei: int,
    timeout_s: int = DEFAULT_TIMEOUT_SECONDS,
    chain_id: int,
    fee_pay_to: str | None = None,
    fee_amount_wei: int | None = None,
) -> dict[str, Any]:
    """402 challenge body, frozen to D4: exact scheme, eip155:<chain_id>,
    $U/eip3009, payTo, amount wei str, window 1..480 (default 300).

    When `fee_pay_to` + `fee_amount_wei` are given, a second accept is
    appended for the marketplace fee (model-A commission) so the client
    signs both authorizations.
    """
    _validate_address(pay_to, "pay_to")
    if not isinstance(amount_wei, int) or amount_wei < 0:
        raise ValidationError("amount_wei must be a non-negative integer")
    if not 1 <= timeout_s <= MAX_TIMEOUT_SECONDS:
        raise ValidationError(f"maxTimeoutSeconds must be in 1..{MAX_TIMEOUT_SECONDS}")
    settings = get_settings()

    def _accept(to: str, amount: int) -> dict[str, Any]:
        return {
            "scheme": "exact",
            "network": f"eip155:{chain_id}",
            "asset": settings.x402_u_token_address,
            "payTo": to,
            "amount": str(amount),
            "maxTimeoutSeconds": timeout_s,
            "extra": {
                "name": U_TOKEN_NAME,
                "version": U_TOKEN_VERSION,
                "assetTransferMethod": EIP3009_RAIL,
            },
        }

    accepts = [_accept(pay_to, amount_wei)]
    if fee_pay_to and fee_amount_wei is not None:
        if fee_amount_wei < 0:
            raise ValidationError("fee_amount_wei must be a non-negative integer")
        _validate_address(fee_pay_to, "fee_pay_to")
        accepts.append(_accept(fee_pay_to, fee_amount_wei))

    return {
        "x402Version": 2,
        "error": "payment required",
        "resource": {"url": resource_url},
        "accepts": accepts,
    }


# ---------------------------------------------------------------------------
# Envelope decode (T2.2 — decode.ts, X3)
# ---------------------------------------------------------------------------


def decode_envelope(header: str | None) -> DecodedPayment:
    """Decode an X-PAYMENT / PAYMENT-SIGNATURE envelope into DecodedPayment.

    Accepts the D4 base64 JSON shape. Raises `InvalidEnvelope` (400) on
    malformed input and `UnsupportedRail` (400) for permit2 rails (v1 is
    eip3009-only, Q2).
    """
    if not isinstance(header, str) or not header.strip():
        raise InvalidEnvelope("missing X-PAYMENT header")
    try:
        payload_bytes = base64.b64decode(header.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidEnvelope("envelope is not valid base64") from exc
    try:
        envelope = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidEnvelope("envelope is not valid JSON") from exc
    if not isinstance(envelope, dict):
        raise InvalidEnvelope("envelope must be a JSON object")

    if envelope.get("x402Version") not in (1, 2):
        raise InvalidEnvelope("envelope must carry x402Version 1 or 2")
    if "resource" not in envelope:
        raise InvalidEnvelope("envelope must echo the challenge resource")
    if not isinstance(envelope.get("network"), str):
        raise InvalidEnvelope("envelope must carry network eip155:<chainId>")

    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        raise InvalidEnvelope("envelope payload is missing")
    if "permit2Authorization" in payload or "permit" in payload:
        raise UnsupportedRail("permit2 rail is not supported in v1 (eip3009 only)")

    authorization = payload.get("authorization")
    if not isinstance(authorization, dict):
        raise InvalidEnvelope("payload must carry an eip3009 authorization")
    try:
        auth: dict[str, Any] = {
            "from": _as_address(authorization["from"], "authorization.from"),
            "to": _as_address(authorization["to"], "authorization.to"),
            "value": _as_numeric(authorization["value"], "authorization.value"),
            "validAfter": _as_numeric(authorization["validAfter"], "authorization.validAfter"),
            "validBefore": _as_numeric(authorization["validBefore"], "authorization.validBefore"),
            "nonce": _as_hex(authorization["nonce"], "authorization.nonce"),
        }
    except KeyError as exc:
        raise InvalidEnvelope(f"authorization field {exc.args[0]!r} is missing") from exc

    signature = payload.get("signature")
    if not isinstance(signature, str) or not _HEX_RE.match(signature):
        raise InvalidEnvelope("payload.signature must be a hex string")

    chain_id = _chain_id_of(envelope)
    if chain_id is None:
        raise InvalidEnvelope("envelope must carry network eip155:<chainId>")

    accepted = envelope.get("accepted")
    asset = accepted.get("asset") if isinstance(accepted, dict) else None
    token = _as_address(asset, "accepted.asset") if asset is not None else ZERO_ADDRESS

    # Optional marketplace-fee payment signed into payload.fee (model A).
    fee: DecodedPayment | None = None
    fee_payload = payload.get("fee")
    if fee_payload is not None:
        if not isinstance(fee_payload, dict) or not isinstance(
            fee_payload.get("authorization"), dict
        ):
            raise InvalidEnvelope("payload.fee must carry an eip3009 authorization")
        fee_auth_payload = fee_payload["authorization"]
        try:
            fee_auth: dict[str, Any] = {
                "from": _as_address(fee_auth_payload["from"], "fee.authorization.from"),
                "to": _as_address(fee_auth_payload["to"], "fee.authorization.to"),
                "value": _as_numeric(fee_auth_payload["value"], "fee.authorization.value"),
                "validAfter": _as_numeric(
                    fee_auth_payload["validAfter"], "fee.authorization.validAfter"
                ),
                "validBefore": _as_numeric(
                    fee_auth_payload["validBefore"], "fee.authorization.validBefore"
                ),
                "nonce": _as_hex(fee_auth_payload["nonce"], "fee.authorization.nonce"),
            }
        except KeyError as exc:
            raise InvalidEnvelope(f"fee authorization field {exc.args[0]!r} is missing") from exc
        fee_signature = fee_payload.get("signature")
        if not isinstance(fee_signature, str) or not _HEX_RE.match(fee_signature):
            raise InvalidEnvelope("payload.fee.signature must be a hex string")
        if fee_auth["from"].lower() != auth["from"].lower():
            raise InvalidEnvelope("fee authorization must be signed by the same payer")
        fee = DecodedPayment(
            rail=EIP3009_RAIL,
            payer=fee_auth["from"],
            amount=int(fee_auth["value"]),
            token=token,
            chain_id=chain_id,
            authorization=fee_auth,
            signature=fee_signature,
            raw=envelope,
        )

    return DecodedPayment(
        rail=EIP3009_RAIL,
        payer=auth["from"],
        amount=int(auth["value"]),
        token=token,
        chain_id=chain_id,
        authorization=auth,
        signature=signature,
        raw=envelope,
        fee=fee,
    )


# ---------------------------------------------------------------------------
# Signature verification (T2.3 — verify.ts, X4/X7)
# ---------------------------------------------------------------------------


def _recover_signer(decoded: DecodedPayment, *, chain_id: int, token_cfg: TokenConfig) -> str:
    """EIP-712 recover the payer via eth-account (TransferWithAuthorization)."""
    auth = decoded.authorization
    # eth-account >= 0.13 uses positional args for encode_typed_data;
    # keyword form (domain=/types=/message=) was removed.
    typed_data = encode_typed_data(
        {
            "name": token_cfg.name,
            "version": token_cfg.version,
            "chainId": chain_id,
            "verifyingContract": token_cfg.address,
        },
        {
            "TransferWithAuthorization": [
                {"name": "from", "type": "address"},
                {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"},
                {"name": "nonce", "type": "bytes32"},
            ]
        },
        {
            "from": auth["from"],
            "to": auth["to"],
            "value": int(auth["value"]),
            "validAfter": int(auth["validAfter"]),
            "validBefore": int(auth["validBefore"]),
            "nonce": auth["nonce"],
        },
    )
    try:
        return cast(str, Account.recover_message(typed_data, signature=decoded.signature))
    except (TypeError, ValueError) as exc:
        raise SignatureMismatch("invalid signature or typed data") from exc


def verify_payment(
    decoded: DecodedPayment,
    *,
    chain_id: int,
    token_cfg: TokenConfig,
    pay_to: str,
    amount_wei: int,
    payer: str,
    now: datetime,
) -> None:
    """Verify a decoded payment; rule order mirrors verify.ts — chain → token
    → amount → payTo → validity window → signature. Raises WrongChain /
    AmountMismatch / PayToMismatch / ChallengeExpired / SignatureMismatch."""
    now_ts = int(now.timestamp())
    if decoded.chain_id != chain_id:
        raise WrongChain(
            f"wrong chain: payment is for eip155:{decoded.chain_id}, "
            f"merchant is on eip155:{chain_id}"
        )
    if decoded.token != ZERO_ADDRESS and decoded.token.lower() != token_cfg.address.lower():
        raise WrongChain(f"token {decoded.token} is not offered on any configured rail")

    if decoded.amount != amount_wei:
        raise AmountMismatch(
            f"amount {decoded.amount} does not match the quoted price {amount_wei}"
        )

    auth = decoded.authorization
    if auth["to"].lower() != pay_to.lower():
        raise PayToMismatch(
            f"payTo mismatch: authorization pays {auth['to']}, merchant is {pay_to}"
        )
    if int(auth["validBefore"]) <= now_ts:
        raise ChallengeExpired("authorization expired (validBefore in the past)")
    if int(auth["validAfter"]) >= now_ts:
        raise ChallengeExpired("authorization not yet valid (validAfter in the future)")

    recovered = _recover_signer(decoded, chain_id=chain_id, token_cfg=token_cfg)
    if recovered.lower() != payer.lower():
        raise SignatureMismatch(f"recovered address {recovered} does not match payer {payer}")


# ---------------------------------------------------------------------------
# Broadcasters (T2.4 — settle.ts, X5)
# ---------------------------------------------------------------------------


class Broadcaster(Protocol):
    """Settlement interface — onchain impl + fake for tests."""

    async def broadcast(
        self,
        decoded: DecodedPayment,
        token_cfg: TokenConfig,
        *,
        facilitator_key: str,
        rpc_url: str,
        now: datetime,
    ) -> BroadcastResult: ...


def _transfer_with_authorization_calldata(auth: dict[str, Any], signature: str) -> str:
    """ABI-encode `transferWithAuthorization(...)` for the $U contract."""
    nonce = auth["nonce"]
    if isinstance(nonce, str) and nonce.startswith("0x"):
        nonce_bytes = bytes.fromhex(nonce[2:])
    elif isinstance(nonce, bytes):
        nonce_bytes = nonce
    else:
        nonce_bytes = int(nonce).to_bytes(32, "big")
    sig_bytes = (
        bytes.fromhex(signature[2:])
        if isinstance(signature, str) and signature.startswith("0x")
        else signature
    )
    encoded = eth_abi_encode(
        ["address", "address", "uint256", "uint256", "uint256", "bytes32", "bytes"],
        [
            auth["from"],
            auth["to"],
            int(auth["value"]),
            int(auth["validAfter"]),
            int(auth["validBefore"]),
            nonce_bytes,
            sig_bytes,
        ],
    )
    return "0x" + _TRANSFER_WITH_AUTH_SELECTOR.hex() + encoded.hex()


def _to_int(value: Any) -> int:
    """Coerce a JSON-RPC hex string (or int) to int."""
    if isinstance(value, str) and value.startswith("0x"):
        return int(value, 16)
    return int(value)


async def _rpc(client: httpx.AsyncClient, rpc_url: str, method: str, params: list[Any]) -> Any:
    """One JSON-RPC call; transport/HTTP/error responses → BroadcastFailed."""
    try:
        resp = await client.post(
            rpc_url,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        )
        resp.raise_for_status()
        body = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise BroadcastFailed(f"RPC transport error on {method}: {exc}") from exc
    if not isinstance(body, dict) or "error" in body:
        raise BroadcastFailed(f"RPC error on {method}: {body.get('error', body)!r}")
    return body.get("result")


class OnchainBroadcaster:
    """Settles via the facilitator EOA with a raw tx over JSON-RPC.

    Flow (design X5): nonce → gasPrice → estimateGas → sendRawTransaction →
    poll receipt until status "0x1". RPC error, timeout, or revert →
    `BroadcastFailed`. httpx only, no web3.py.
    """

    def __init__(
        self,
        *,
        poll_interval: float = 1.0,
        poll_attempts: int = 30,
        timeout: float = 15.0,
    ) -> None:
        self._poll_interval = poll_interval
        self._poll_attempts = poll_attempts
        self._timeout = timeout

    async def broadcast(
        self,
        decoded: DecodedPayment,
        token_cfg: TokenConfig,
        *,
        facilitator_key: str,
        rpc_url: str,
        now: datetime,
    ) -> BroadcastResult:
        if not facilitator_key:
            raise BroadcastFailed("facilitator key is not configured")
        account = Account.from_key(facilitator_key)
        calldata = _transfer_with_authorization_calldata(decoded.authorization, decoded.signature)
        tx_preview: dict[str, Any] = {
            "from": account.address,
            "to": decoded.token,
            "data": calldata,
            # Hex string, not int: strict nodes (BSC official seeds) reject
            # a numeric value in estimateGas with a JSON unmarshal error.
            "value": "0x0",
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                nonce = await _rpc(
                    client, rpc_url, "eth_getTransactionCount", [account.address, "latest"]
                )
                gas_price = await _rpc(client, rpc_url, "eth_gasPrice", [])
                gas = await _rpc(client, rpc_url, "eth_estimateGas", [tx_preview])
                signed = account.sign_transaction(
                    {
                        "to": decoded.token,
                        "value": 0,
                        "data": calldata,
                        "nonce": _to_int(nonce),
                        "gasPrice": _to_int(gas_price),
                        "gas": _to_int(gas),
                        "chainId": decoded.chain_id,
                    }
                )
                tx_hash = await _rpc(
                    client,
                    rpc_url,
                    "eth_sendRawTransaction",
                    ["0x" + signed.raw_transaction.hex()],
                )
                if not isinstance(tx_hash, str) or not tx_hash.startswith("0x"):
                    raise BroadcastFailed("eth_sendRawTransaction returned no tx hash")
                await self._wait_for_receipt(client, rpc_url, tx_hash)
            except BroadcastFailed:
                raise
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                raise BroadcastFailed(f"broadcast failure: {exc}") from exc
        return BroadcastResult(tx_hash=tx_hash)

    async def _wait_for_receipt(
        self, client: httpx.AsyncClient, rpc_url: str, tx_hash: str
    ) -> None:
        for _ in range(self._poll_attempts):
            receipt = await _rpc(client, rpc_url, "eth_getTransactionReceipt", [tx_hash])
            if receipt is not None:
                if _to_int(receipt.get("status", 0)) != 1:
                    raise BroadcastFailed("transaction reverted onchain")
                return
            await asyncio.sleep(self._poll_interval)
        raise BroadcastFailed(f"no receipt after {self._poll_attempts} polls ({tx_hash})")


class FakeBroadcaster:
    """Test double: canned tx_hash, records every call (design X5)."""

    def __init__(self, tx_hash: str = "") -> None:
        self.tx_hash = tx_hash or ("0x" + "ab" * 32)
        self.calls: list[dict[str, Any]] = []

    async def broadcast(
        self,
        decoded: DecodedPayment,
        token_cfg: TokenConfig,
        *,
        facilitator_key: str,
        rpc_url: str,
        now: datetime,
    ) -> BroadcastResult:
        self.calls.append(
            {
                "decoded": decoded,
                "token_cfg": token_cfg,
                "facilitator_key": facilitator_key,
                "rpc_url": rpc_url,
                "now": now,
            }
        )
        return BroadcastResult(tx_hash=self.tx_hash)


__all__ = [
    "BroadcastResult",
    "Broadcaster",
    "DEFAULT_TIMEOUT_SECONDS",
    "DecodedPayment",
    "EIP3009_RAIL",
    "FakeBroadcaster",
    "MAX_TIMEOUT_SECONDS",
    "OnchainBroadcaster",
    "TokenConfig",
    "ZERO_ADDRESS",
    "build_challenge",
    "decode_envelope",
    "get_token_config",
    "verify_payment",
]
