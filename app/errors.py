"""Domain exception hierarchy + FastAPI handler registration.

Spec: `sdd/marketplace-scaffold/spec/wallet-auth` (#20) R7 + design id 26 D4.

The `AppError` hierarchy is the single source of truth for every domain
exception the API surface understands. `register_error_handlers(app)`
maps each subclass to the right HTTP/JSON/HTMX shape:

  - HTMX callers (request with `HX-Request: true`) get:
      * 401 → `HX-Redirect: /auth` (spec R7).
      * 4xx → `HX-Retarget: #errors` + `HX-Reswap: innerHTML` with a
              generic JSON body that the client-side `error_toast`
              partial renders.
      * 5xx → `HX-Retarget: #errors` with a generic message; detail
              is logged server-side and never leaked.
  - Non-HTMX callers get either a JSON envelope `{error: {code, message}}`
    for 4xx and 5xx, or a 401 with `WWW-Authenticate: Bearer` for
    auth_required.

The base file (hierarchy) was introduced in 5.1; this commit (6.1)
adds the handler + the JSON/HTMX switching logic + the `to_envelope`
helper that test_auth and the test suite can assert on.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, cast

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hierarchy
# ---------------------------------------------------------------------------


class AppError(Exception):
    """Base class for every domain exception the API surface understands.

    Subclasses MUST set `status_code` and `code` so the registered handler
    (PR-C 6.1) can map to the right HTTP/JSON/HTMX shape uniformly.
    """

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str = "") -> None:
        super().__init__(message or self.__class__.__name__)
        self.message = message or self.__class__.__name__


class ValidationError(AppError):
    """Bad request payload. Maps to 422."""

    status_code = 422
    code = "validation_error"


class AuthRequired(AppError):
    """Missing or invalid session cookie. Maps to 401 + HX-Redirect: /auth."""

    status_code = 401
    code = "auth_required"


class Forbidden(AppError):
    """CSRF mismatch or other authorisation failure. Maps to 403."""

    status_code = 403
    code = "forbidden"


class NotFound(AppError):
    """Resource not found. Maps to 404."""

    status_code = 404
    code = "not_found"


class Conflict(AppError):
    """Resource already exists or is in the wrong state. Maps to 409."""

    status_code = 409
    code = "conflict"


class UpstreamRateLimit(AppError):
    """Upstream rate-limited us. Maps to 429 + Retry-After."""

    status_code = 429
    code = "upstream_rate_limit"


class UpstreamUnavailable(AppError):
    """Upstream is down or failed past its retry budget. Maps to 503."""

    status_code = 503
    code = "upstream_unavailable"


# ---------------------------------------------------------------------------
# x402 payment tree (FU-2, design id 52 D6)
# ---------------------------------------------------------------------------


class PaymentError(AppError):
    """Base for x402 payment-domain failures (design id 52, D6)."""

    status_code = 500
    code = "payment_error"


class NoPayTo(PaymentError):
    """Hire for an agent without a wallet → 422 (Q6, no owner fallback)."""

    status_code = 422
    code = "no_pay_to"


class InvalidEnvelope(PaymentError):
    """X-PAYMENT is not base64 JSON or misses required fields (X3)."""

    status_code = 400
    code = "invalid_payment_envelope"


class UnsupportedRail(PaymentError):
    """Envelope uses a rail v1 does not support (permit2; Q2)."""

    status_code = 400
    code = "unsupported_rail"


class WrongChain(PaymentError):
    """Envelope targets a different chain, or a token not offered."""

    status_code = 403
    code = "payment_wrong_chain"


class AmountMismatch(PaymentError):
    """Envelope amount differs from the quoted challenge amount."""

    status_code = 403
    code = "payment_amount_mismatch"


class PayToMismatch(PaymentError):
    """Authorization pays a different recipient than the hire's pay_to."""

    status_code = 403
    code = "payment_pay_to_mismatch"


class SignatureMismatch(PaymentError):
    """EIP-712 recovery does not match the payer (X4)."""

    status_code = 403
    code = "signature_mismatch"


class ChallengeExpired(PaymentError):
    """Authorization validity window passed (X7)."""

    status_code = 409
    code = "challenge_expired"


class AlreadyPaid(PaymentError):
    """Hire is not pending (paid/failed/cancelled) (X6/H4)."""

    status_code = 409
    code = "already_paid"


class BroadcastFailed(PaymentError):
    """RPC error, timeout, or onchain revert while settling; hire → failed."""

    status_code = 503
    code = "payment_broadcast_failed"


class PaymentGatewayUnconfigured(PaymentError):
    """Empty facilitator key — payments disabled."""

    status_code = 503
    code = "payment_gateway_unconfigured"


__all__ = [
    "AlreadyPaid",
    "AmountMismatch",
    "AppError",
    "AuthRequired",
    "BroadcastFailed",
    "ChallengeExpired",
    "Conflict",
    "Forbidden",
    "InvalidEnvelope",
    "NoPayTo",
    "NotFound",
    "PayToMismatch",
    "PaymentError",
    "PaymentGatewayUnconfigured",
    "SignatureMismatch",
    "UnsupportedRail",
    "UpstreamRateLimit",
    "UpstreamUnavailable",
    "ValidationError",
    "WrongChain",
    "is_htmx_request",
    "register_error_handlers",
    "to_envelope",
]


# ---------------------------------------------------------------------------
# Envelope + HTMX detection
# ---------------------------------------------------------------------------


def to_envelope(err: AppError) -> dict[str, Any]:
    """Return the canonical JSON envelope for `err`."""
    return {"error": {"code": err.code, "message": err.message}}


def is_htmx_request(request: Request) -> bool:
    """True when the request was made by HTMX (HX-Request header set)."""
    return request.headers.get("HX-Request", "").lower() == "true"


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


def _htmx_redirect(path: str) -> Response:
    """Build a minimal HTMX redirect response (no body, HX-Redirect header)."""
    return Response(status_code=200, headers={"HX-Redirect": path})


def _json_error(err: AppError) -> JSONResponse:
    return JSONResponse(status_code=err.status_code, content=to_envelope(err))


def _htmx_error(err: AppError) -> Response:
    """Map `err` to a header-only HTMX response.

    401: HX-Redirect to /auth (spec R7). Non-redirectable (no body) so the
         client triggers a full page navigation.
    4xx: HX-Retarget + HX-Reswap; body is a tiny JSON envelope so the
         client-side `error_toast.html` partial can render it.
    5xx: same shape as 4xx but with a generic message — detail stays
         server-side.
    """
    headers: dict[str, str] = {}
    if err.status_code == 401:
        return _htmx_redirect("/auth")
    # Render into the #errors region. The body is a JSON envelope that the
    # client partial decodes; we set Content-Type accordingly.
    headers["HX-Retarget"] = "#errors"
    headers["HX-Reswap"] = "innerHTML"
    body = to_envelope(err if err.status_code < 500 else AppError("internal error"))
    return Response(
        status_code=err.status_code,
        headers=headers,
        content=str(body).replace("'", '"'),
        media_type="application/json",
    )


def _handle_app_error(request: Request, err: AppError) -> Response:
    if isinstance(err, AuthRequired):
        # Special case: even non-HTMX 401 should be JSON for API callers
        # but the API consumers can read the WWW-Authenticate header.
        if is_htmx_request(request):
            return _htmx_redirect("/auth")
        return JSONResponse(
            status_code=err.status_code,
            content=to_envelope(err),
            headers={"WWW-Authenticate": "Bearer"},
        )
    if is_htmx_request(request):
        return _htmx_error(err)
    return _json_error(err)


def register_error_handlers(app: FastAPI) -> None:
    """Register the AppError handler on the FastAPI app.

    Called once from `app/main.create_app`. The handler is installed for the
    concrete subclasses so FastAPI's exception resolution picks the most
    specific one. We also register `Exception` as a safety net.
    """
    for cls in (
        ValidationError,
        AuthRequired,
        Forbidden,
        NotFound,
        Conflict,
        UpstreamRateLimit,
        UpstreamUnavailable,
        PaymentError,
        NoPayTo,
        InvalidEnvelope,
        UnsupportedRail,
        WrongChain,
        AmountMismatch,
        PayToMismatch,
        SignatureMismatch,
        ChallengeExpired,
        AlreadyPaid,
        BroadcastFailed,
        PaymentGatewayUnconfigured,
    ):
        app.add_exception_handler(
            cls,
            cast(Callable[[Request, Exception], Response], _handle_app_error),
        )

    @app.exception_handler(Exception)
    async def _fallback(request: Request, exc: Exception) -> Response:  # pragma: no cover
        logger.exception("unhandled exception: %s", exc)
        wrapped = AppError("internal error")
        if is_htmx_request(request):
            return _htmx_error(wrapped)
        return _json_error(wrapped)
