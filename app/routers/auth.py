"""Auth router — nonce issuance, signature verification, logout.

Spec: `sdd/marketplace-scaffold/spec/wallet-auth` (#20) R1-R7.
Design: id 26 (routers/auth.py contract).

Endpoints:
  GET  /auth/nonce?address=0x…      → {nonce, message}; 422 on bad address.
  POST /auth/verify                 → 200 + Set-Cookie; 401 on bad signature.
                                     HTMX callers get HX-Redirect: /.
  POST /auth/logout                 → 204 + cleared cookie.

The router does not import any template — it is purely JSON for HTMX and
mobile. The HTMX redirect story lives in the error handlers (commit 4)
and pages.py (commit 6).
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, Field

from app.db.models.user import User
from app.errors import ValidationError
from app.services.auth import (
    SESSION_COOKIE_NAME,
    get_current_user,
    issue_nonce,
    issue_session,
    verify_signature,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class NonceResponse(BaseModel):
    nonce: str = Field(..., description="Hex nonce; the wallet must sign `message`.")
    message: str = Field(..., description="Canonical EIP-191 message string.")


class VerifyRequest(BaseModel):
    address: str = Field(..., description="0x + 40 hex wallet address.")
    signature: str = Field(..., description="0x-prefixed signature from personal_sign.")
    nonce: str = Field(..., description="Nonce previously returned by /auth/nonce.")


class VerifyResponse(BaseModel):
    address: str
    created_at: str


# ---------------------------------------------------------------------------
# Cookie helper
# ---------------------------------------------------------------------------


def _set_session_cookie(response: Response, value: str) -> None:
    """Set the signed session cookie. `Secure` follows the request scheme."""
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=value,
        httponly=True,
        samesite="lax",
        secure=False,  # toggled on by the reverse proxy in prod
        max_age=60 * 60,  # refreshed on /auth/verify
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/nonce", response_model=NonceResponse)
async def get_nonce(
    address: Annotated[str, Query(pattern=r"^0x[a-fA-F0-9]{40}$")],
) -> NonceResponse:
    """Issue a fresh nonce for `address` (spec R1, R2)."""
    if not address:
        raise ValidationError("address query parameter is required")
    nonce, message = await issue_nonce(address)
    return NonceResponse(nonce=nonce, message=message)


@router.post("/verify", response_model=VerifyResponse)
async def post_verify(
    payload: VerifyRequest,
    request: Request,
    response: Response,
) -> VerifyResponse:
    """Recover the signer, upsert the user, set the session cookie (spec R3).

    For HTMX callers (`HX-Request: true`), the 200 response carries an
    `HX-Redirect: /` so the client navigates to the home page instead of
    swapping the JSON body. The 401 path is handled by the error handler
    in `app/errors.py`, which already emits `HX-Redirect: /auth` for HTMX
    callers. Fix for sdd-verify W3 residual.
    """
    if request.headers.get("HX-Request", "").lower() == "true":
        response.headers["HX-Redirect"] = "/"
    user = await verify_signature(payload.address, payload.signature, payload.nonce)
    _set_session_cookie(response, issue_session(user))
    return VerifyResponse(
        address=user.address,
        created_at=user.created_at.isoformat(),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def post_logout(
    response: Response,
    # We accept the dep so logout is a no-op for anonymous callers — a
    # missing cookie is not an error here, just an idempotent clear.
    _user: Annotated[User | None, Depends(get_current_user)] = None,  # type: ignore[assignment]
) -> Response:
    _clear_session_cookie(response)
    # Return the SAME response object so the Set-Cookie (Max-Age=0) header
    # survives; building a new Response here would drop it. Set the status
    # explicitly because the injected response defaults to 200.
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


# Re-export for main.py wiring.
__all__ = ["router"]
