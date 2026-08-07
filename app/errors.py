"""Domain exception hierarchy for the marketplace.

All routers and services raise `AppError` subclasses; `register_error_handlers`
(PR-C, 6.1) maps each one to the right HTTP/JSON/HTMX shape. Defining the
hierarchy in this commit (5.1 partial) lets the auth service (5.2) raise
`AuthRequired` / `Forbidden` / `ValidationError` without depending on commit 6.1.
"""
from __future__ import annotations


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
]
