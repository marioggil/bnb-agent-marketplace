"""x402 agent-offer probe client (x402-agent-hire PR 1).

Leaf service module: no DB, no router imports, no FastAPI deps — unit-testable
against an httpx mock. Probes an agent's A2A/web endpoint with `GET`, parses a
`402 Payment Required` + `PAYMENT-REQUIRED` header into an `AgentOffer`, and
returns `None` for every other outcome (non-402, timeout, malformed, blocked).

Hard constraints (carried from the spec, not relitigated):
  - `probe_agent_offer()` NEVER raises — every failure path returns `None`.
  - SSRF guard: http/https schemes only; literal-IP and resolved-host checks
    reject private/loopback/link-local/reserved/multicast/unspecified ranges;
    `follow_redirects=False` on the client.
  - `price_usd` is derived as `amount_wei / 10**18`. `_WEI_PER_UNIT` is
    duplicated locally (same value as `hires._WEI_PER_UNIT`) because the
    service layer must not import from a router.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import re
import socket
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

#: $U has 18 decimals; the probed `amount` is in wei. Local copy of the
#: `hires._WEI_PER_UNIT` constant (services must not import from routers).
_WEI_PER_UNIT = Decimal(10**18)

#: Shape guards mirror the `payment.py` validators (exact 0x + 40 hex, numeric).
_PAYTO_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_AMOUNT_RE = re.compile(r"^\d+$")

#: Address classes the probe refuses to request (SSRF guard, R8).
_BLOCKED_IP = (
    lambda addr: (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )
)


@dataclass(frozen=True)
class AgentOffer:
    """The agent's real x402 offer parsed from `accepts[0]` of its challenge."""

    #: accepts[0].payTo — 0x address receiving the hire payment.
    pay_to: str
    #: accepts[0].amount — wei, validated numeric.
    amount_wei: int
    #: accepts[0].asset — token address (case-preserved).
    asset: str
    #: accepts[0].network — "eip155:<chain_id>".
    network: str
    #: amount_wei / 10**18 — $U units (18 decimals).
    price_usd: Decimal


def _validate_probe_url(url: str) -> httpx.URL:
    """SSRF guard (R8): return a validated `httpx.URL` or raise `ValueError`.

    Rules:
      1. scheme must be http or https;
      2. host must be non-empty;
      3. literal IPs are rejected when private/loopback/link-local/reserved/
         multicast/unspecified;
      4. hostnames are resolved via `socket.getaddrinfo` and rejected when ANY
         resolved address is in a blocked class; resolution failure raises
         (fail-closed).
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("empty probe url")
    u = httpx.URL(url)
    if u.scheme not in ("http", "https"):
        raise ValueError(f"unsupported scheme {u.scheme!r}")
    host = u.host
    if not host:
        raise ValueError("empty probe host")

    def _blocked(addr_str: str) -> bool:
        addr_str = addr_str.split("%", 1)[0]  # strip IPv6 zone id (fe80::1%eth0)
        try:
            addr = ipaddress.ip_address(addr_str)
        except ValueError:
            return True  # unparseable → fail closed
        return _BLOCKED_IP(addr)

    try:
        candidate = ipaddress.ip_address(host)
    except ValueError:
        candidate = None
    if candidate is not None:
        if _blocked(host):
            raise ValueError(f"blocked address {host!r}")
        return u

    # Hostname: resolve and reject if ANY result is blocked (fail-closed).
    try:
        infos = socket.getaddrinfo(host, u.port or 443, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise ValueError(f"dns resolution failed for {host!r}: {exc}") from exc
    if not infos:
        raise ValueError(f"no addresses for {host!r}")
    for family, _socktype, _proto, _canonname, sockaddr in infos:
        if _blocked(str(sockaddr[0])):
            raise ValueError(f"blocked resolved address for {host!r}")
    return u


async def probe_agent_offer(url: str, *, timeout_s: float = 2.0) -> AgentOffer | None:
    """Probe one agent endpoint; return its `AgentOffer` or `None`.

    Strategy (production-verified against clawdmint-api.vercel.app/a2a, agent
    2468):
      1. `GET` the declared endpoint first.
      2. If it answers `405 Method Not Allowed` — the common A2A shape that
         only accepts POST — retry with an A2A `tasks/send` JSON-RPC body.
      3. In both paths, a `402 Payment Required` + `payment-required` header
         is parsed into `AgentOffer` (accepts[0]).

    Never raises: every failure path (blocked URL, transport error, timeout,
    non-402, missing/malformed `payment-required` header) returns `None`.
    """
    try:
        target = _validate_probe_url(url)
        async with httpx.AsyncClient(
            timeout=timeout_s, follow_redirects=False
        ) as client:
            resp = await client.get(target)
    except (httpx.HTTPError, ValueError, OSError):
        return None
    # A2A agents commonly reject GET with 405 and require a POST tasks/send.
    # Reuse the ALREADY-OPEN client for the retry, then parse the 402.
    if resp.status_code == 405:
        try:
            async with httpx.AsyncClient(
                timeout=timeout_s, follow_redirects=False
            ) as client:
                resp = await client.post(
                    target,
                    json=_A2A_TASKS_SEND_BODY,
                    headers={"content-type": "application/json"},
                )
        except (httpx.HTTPError, ValueError, OSError):
            return None
    return _parse_offer_response(resp)


#: A2A `tasks/send` minimal request body — used for the 405→POST retry.
_A2A_TASKS_SEND_BODY: dict[str, Any] = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tasks/send",
    "params": {
        "id": "marketplace-probe",
        "message": {"role": "user", "parts": [{"text": "ping"}]},
    },
}


def _parse_offer_response(resp: httpx.Response) -> AgentOffer | None:
    """Parse a `402 + payment-required` response into `AgentOffer`; else None.

    Shared by the GET and POST probe paths so both surfaces behave the same:
    non-402 → None (no false positive), malformed header → None (never raise).
    """
    if resp.status_code != 402:
        return None  # R4: non-402 → no offer, no false positive
    header = resp.headers.get("payment-required")
    if not header:
        return None
    try:
        payload = json.loads(base64.b64decode(header, validate=True).decode("utf-8"))
        accept = payload["accepts"][0]  # only accepts[0]; the fee is ours
        pay_to, amount, asset, network = (
            accept["payTo"],
            accept["amount"],
            accept["asset"],
            accept["network"],
        )
    except (
        KeyError,
        IndexError,
        TypeError,
        ValueError,
        binascii.Error,
        json.JSONDecodeError,
        UnicodeDecodeError,
    ):
        return None
    if not _PAYTO_RE.match(pay_to) or not _AMOUNT_RE.match(str(amount)):
        return None
    return AgentOffer(
        pay_to=pay_to,
        amount_wei=int(amount),
        asset=asset,
        network=network,
        price_usd=Decimal(amount) / _WEI_PER_UNIT,
    )


def is_supported_offer(offer: AgentOffer, settings: Any) -> bool:
    """True when the offer's asset+network match the marketplace rail (R3/D-6).

    Single rail, v1 (multi-asset is out of scope): asset must be the pinned $U
    for the configured chain AND network must be `eip155:<configured chain id>`.
    Asset comparison is case-insensitive (checksummed either way).
    """
    return (
        offer.network == f"eip155:{settings.x402_chain_id}"
        and offer.asset.lower() == settings.x402_u_token_address.lower()
    )


__all__ = [
    "AgentOffer",
    "is_supported_offer",
    "probe_agent_offer",
    "_validate_probe_url",
    "_parse_offer_response",
]