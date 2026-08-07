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
from typing import Any

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


__all__ = [
    "AppError",
    "AuthRequired",
    "Conflict",
    "Forbidden",
    "NotFound",
    "UpstreamRateLimit",
    "UpstreamUnavailable",
    "ValidationError",
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
    ):
        app.add_exception_handler(cls, _handle_app_error)

    @app.exception_handler(Exception)
    async def _fallback(request: Request, exc: Exception) -> Response:  # pragma: no cover
        logger.exception("unhandled exception: %s", exc)
        wrapped = AppError("internal error")
        if is_htmx_request(request):
            return _htmx_error(wrapped)
        return _json_error(wrapped)
