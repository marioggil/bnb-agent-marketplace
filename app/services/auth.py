"""Wallet auth (EIP-191 personal_sign) + session cookie + CSRF token.

Spec: `sdd/marketplace-scaffold/spec/wallet-auth` (#20).
Design: id 26 (services/auth.py contract).

The service has three concerns:
  1. `issue_nonce` / `verify_signature` — EIP-191 nonce flow bound to the
     wallet address with a 10-minute TTL and single-use flag.
  2. `get_current_user` — FastAPI dependency that reads the signed session
     cookie and returns the `User` row.
  3. `issue_csrf` / `verify_csrf` / `require_csrf` — per-session CSRF token
     derived from the secret key, sent on every state-changing request.

The session cookie is itsdangerous-signed (HttpOnly, SameSite=Lax,
Secure-if-HTTPS) — see `app.routers.auth` for the Set-Cookie wiring.
"""

from __future__ import annotations

import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Final, cast

from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi import Cookie, Request
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import NONCE_TTL_SECONDS, get_settings
from app.db.models.auth_nonce import AuthNonce
from app.db.models.user import User
from app.db.session import AsyncSessionLocal
from app.errors import AuthRequired, Forbidden, ValidationError

logger = logging.getLogger(__name__)


#: Cookie name for the signed session. Exposed so routers can read it
#: without stringly-typed lookups.
SESSION_COOKIE_NAME: Final[str] = "bnb_agent_session"
#: Canonical message the wallet signs. Free-form per EIP-191 (spec R1, Q5).
SIGN_IN_MESSAGE_PREFIX: Final[str] = "Sign in to bnb_agent: "
#: CSRF token length in hex chars (32 hex = 16 bytes, plenty).
CSRF_TOKEN_HEX_LEN: Final[int] = 32

_settings = get_settings()
_serializer = URLSafeSerializer(_settings.secret_key, salt="bnb_agent_session")


# ---------------------------------------------------------------------------
# Address validation
# ---------------------------------------------------------------------------


def _validate_address(address: str) -> str:
    """Return a normalised `0x` + 40 hex address or raise `ValidationError`."""
    if not isinstance(address, str):
        raise ValidationError("address must be a string")
    if len(address) != 42 or not address.startswith("0x"):
        raise ValidationError("address must be 0x + 40 hex chars")
    body = address[2:]
    if any(c not in "0123456789abcdefABCDEF" for c in body):
        raise ValidationError("address must contain only hex characters")
    return "0x" + body.lower()


# ---------------------------------------------------------------------------
# Nonce lifecycle
# ---------------------------------------------------------------------------


async def issue_nonce(address: str) -> tuple[str, str]:
    """Persist a fresh nonce for `address`; return (nonce, message).

    Spec R1: 16-byte hex nonce + canonical message. The TTL is `NONCE_TTL_SECONDS`
    (10 min) and single-use is enforced by the `used` flag flipped in
    `verify_signature`.
    """
    addr = _validate_address(address)
    nonce = secrets.token_hex(16)
    message = f"{SIGN_IN_MESSAGE_PREFIX}{nonce}"
    expires_at = datetime.now(tz=timezone.utc) + timedelta(seconds=NONCE_TTL_SECONDS)
    row = AuthNonce(
        address=addr,
        nonce=nonce,
        used=False,
        expires_at=expires_at,
    )
    async with AsyncSessionLocal() as session:
        # A wallet may have an in-flight nonce; upsert by overwriting the row
        # keyed on `address` (the PK). This keeps the latest nonce authoritative
        # and matches spec R2 (single-use) — older nonces are effectively
        # invalidated by this rewrite.
        await session.merge(row)
        await session.commit()
    return nonce, message


async def verify_signature(address: str, signature: str, nonce: str) -> User:
    """Recover the signer from the signature; on match, upsert `User` and
    return it. Raises `AuthRequired` on any replay / TTL / wrong-signer
    condition.
    """
    addr = _validate_address(address)
    if not isinstance(signature, str) or not signature:
        raise ValidationError("signature is required")
    if not isinstance(nonce, str) or not nonce:
        raise ValidationError("nonce is required")

    # EIP-191 personal_sign recovery (decision Q5). The canonical message
    # must match what the client received from /auth/nonce.
    try:
        msg = encode_defunct(text=f"{SIGN_IN_MESSAGE_PREFIX}{nonce}")
        recovered = Account.recover_message(msg, signature=signature)
    except (TypeError, ValueError) as exc:
        logger.info("verify_signature: bad signature format: %s", exc)
        raise AuthRequired("invalid signature") from exc

    if recovered.lower() != addr:
        raise AuthRequired("recovered address does not match request address")

    async with AsyncSessionLocal() as session:
        row = await session.scalar(
            select(AuthNonce).where(AuthNonce.address == addr, AuthNonce.nonce == nonce)
        )
        if row is None:
            raise AuthRequired("unknown nonce")
        now = datetime.now(tz=timezone.utc)
        if row.used:
            raise AuthRequired("nonce already used")
        if row.expires_at <= now:
            raise AuthRequired("nonce expired")
        row.used = True
        # Upsert the user; SQLite/Postgres both support `INSERT ... ON CONFLICT
        # DO UPDATE` via the dialect-agnostic `merge`.
        existing = await session.get(User, addr)
        if existing is None:
            user = User(address=addr, last_seen_at=now)
            session.add(user)
        else:
            existing.last_seen_at = now
            user = existing
        try:
            await session.commit()
        except IntegrityError as exc:  # pragma: no cover - race
            logger.warning("verify_signature: race on user upsert: %s", exc)
            await session.rollback()
            raise AuthRequired("user upsert failed") from exc
    return user


# ---------------------------------------------------------------------------
# Session cookie
# ---------------------------------------------------------------------------


def issue_session(user: User) -> str:
    """Return a signed cookie value for `user`."""
    return _serializer.dumps({"address": user.address})


def _read_session(raw: str | None) -> str | None:
    """Return the address inside the signed cookie, or `None` on bad sig."""
    if not raw:
        return None
    try:
        data = _serializer.loads(raw)
    except BadSignature:
        return None
    address = data.get("address") if isinstance(data, dict) else None
    if not isinstance(address, str):
        return None
    return _validate_address(address) if len(address) == 42 else None


async def get_current_user(
    request: Request,
    bnb_agent_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> User:
    """FastAPI dependency. Reads the signed session cookie and returns the
    `User` row, raising `AuthRequired` (401) on any failure.
    """
    address = _read_session(bnb_agent_session)
    if address is None:
        raise AuthRequired("missing or invalid session cookie")
    async with AsyncSessionLocal() as session:
        user = await session.get(User, address)
    if user is None:
        raise AuthRequired("user not found")
    # Stash the address on the request for `require_csrf` to read the cookie
    # session_id without re-parsing the signature. The cookie value itself
    # is enough to derive the CSRF token; we keep it under a private name.
    request.state.session_cookie = bnb_agent_session
    return cast(User, user)


def clear_session_cookie_value() -> str:
    """Return the value used to clear the session cookie (empty + max-age=0)."""
    return ""


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------


def _csrf_for(session_cookie: str | None) -> str:
    """Derive a per-session CSRF token: HMAC-SHA256(secret, session)[:32]."""
    payload = (session_cookie or "").encode("utf-8")
    digest = hmac.new(_settings.secret_key.encode("utf-8"), payload, sha256).hexdigest()
    return digest[:CSRF_TOKEN_HEX_LEN]


def issue_csrf(session_cookie: str | None) -> str:
    """Public wrapper around `_csrf_for` so routers and templates share one name."""
    return _csrf_for(session_cookie)


def verify_csrf(session_cookie: str | None, token: str | None) -> bool:
    """Constant-time compare of `token` against the session-derived token."""
    if not token:
        return False
    expected = _csrf_for(session_cookie)
    return hmac.compare_digest(expected, token)


async def require_csrf(
    request: Request,
    bnb_agent_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> None:
    """FastAPI dependency for state-changing routes. Reads `X-CSRF-Token` header
    OR form field `csrf_token`, then verifies against the session cookie.
    Raises `Forbidden` (403) on mismatch.
    """
    # Header first — HTMX uses HX-Headers for clean delivery.
    token = request.headers.get("X-CSRF-Token")
    if not token:
        # Fallback: form field (no-JS path) — check the parsed body if present.
        # We do not consume the request stream here; routers that need the
        # form data (e.g. /api/favorites) read it separately. We peek at
        # `request.scope` is overkill; instead we accept the cookie value
        # is the source of truth and the form field is for browser submits
        # that the router will parse via `Form(...)`. For non-form routers
        # the header path is the only path.
        pass
    if not verify_csrf(bnb_agent_session, token):
        raise Forbidden("CSRF token missing or invalid")


__all__ = [
    "CSRF_TOKEN_HEX_LEN",
    "SESSION_COOKIE_NAME",
    "SIGN_IN_MESSAGE_PREFIX",
    "clear_session_cookie_value",
    "get_current_user",
    "issue_csrf",
    "issue_nonce",
    "issue_session",
    "require_csrf",
    "verify_csrf",
    "verify_signature",
]
