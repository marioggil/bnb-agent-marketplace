"""Centralised settings loaded from the environment via pydantic-settings.

Single source of truth for every env var the app reads. PR-B's `app.db.session`
already reads `DATABASE_URL` from `os.environ`; this module is the future-proof
entry point and is imported by `migrations/env.py` (PR-B already has a
fallback), the FastAPI factory (PR-C), and the auth service (PR-C).

The class is `lru_cache`-wrapped via `get_settings()` so tests can patch the
singleton with a clean override without re-importing.

Spec: `sdd/marketplace-scaffold/spec/app-bootstrap` (#24) R6.
Design: D5 (config boundary), id 26.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Final

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Re-export from the models layer so callers that only need the auth constants
# (e.g. tests) do not have to import `app.db.models` directly (D5 boundary:
# services do not import models, but other layers do).
__all__ = [
    "NONCE_TTL_SECONDS",
    "Settings",
    "get_settings",
    "settings",
]

# Importing here keeps the re-exports colocated with the config they describe.
# The `TYPE_CHECKING` guard would not help because pydantic-settings
# re-resolves types at runtime; the cost is one extra import at module load.
from app.db.models.auth_nonce import NONCE_TTL_SECONDS  # noqa: E402


class Settings(BaseSettings):
    """Strongly-typed env binding.

    Every field maps to a documented env var from `.env.example`. The model
    refuses to start if a `SECRET_KEY` is left empty — that would silently
    sign sessions with an empty key.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- Postgres / asyncpg DSN -----------------------------------------
    database_url: str = Field(
        default="postgresql+asyncpg://bnb:bnb@localhost:5432/bnb_agent",
        alias="DATABASE_URL",
        description="asyncpg DSN used by the FastAPI runtime.",
    )

    # ---- 8004scan upstream -----------------------------------------------
    scan_8004_base: str = Field(
        default="https://8004scan.io/api/v1/public",
        alias="8004SCAN_BASE",
        description="Public API base. Override only if you proxy through a mirror.",
    )
    scan_8004_api_key: str = Field(
        default="",
        alias="8004SCAN_API_KEY",
        description=(
            "Optional Pro tier key. Empty drops the worker to free-tier limits "
            "(~50 rpm) and the worker logs a [WARN] on startup (spec #23 R8)."
        ),
    )

    # ---- Session / security ---------------------------------------------
    secret_key: str = Field(
        default="change-me-32-bytes-min",
        alias="SECRET_KEY",
        min_length=16,
        description=(
            "Used to sign the session cookie and derive the CSRF token. "
            "MUST be at least 32 random bytes in production."
        ),
    )
    session_ttl_min: int = Field(
        default=60,
        alias="SESSION_TTL_MIN",
        ge=1,
        description="Session lifetime in minutes (default 60).",
    )
    sync_api_key: str | None = Field(
        default=None,
        alias="SYNC_API_KEY",
        description=(
            "Optional shared secret protecting the remote sync API "
            "(POST /api/sync, GET /api/sync/status). Empty/None disables "
            "those endpoints (they answer 503)."
        ),
    )

    # ---- Observability / worker -----------------------------------------
    log_level: str = Field(
        default="INFO",
        alias="LOG_LEVEL",
        description="Standard log level for uvicorn + app loggers.",
    )
    worker_rate_per_sec: int = Field(
        default=4,
        alias="WORKER_RATE_PER_SEC",
        ge=1,
        description="Soft cap on the rate the sync worker hits the upstream.",
    )

    @field_validator("secret_key")
    @classmethod
    def _secret_key_not_empty(cls, v: str) -> str:
        # Reject the literal placeholder that ships in .env.example to keep
        # the dev experience loud: if you forgot to copy .env.example → .env
        # the app refuses to start instead of silently signing with "change-me".
        stripped = v.strip()
        if not stripped or stripped.startswith("change-me"):
            raise ValueError(
                "SECRET_KEY must be set to a real random value (see .env.example)"
            )
        return v

    @field_validator("log_level")
    @classmethod
    def _log_level_upper(cls, v: str) -> str:
        return v.upper()


def get_settings() -> Settings:
    """Return the cached `Settings` singleton."""
    return _settings_cache()


@lru_cache(maxsize=1)
def _settings_cache() -> Settings:
    return Settings()


# Module-level singleton for ergonomic imports. Tests should patch via
# `get_settings.cache_clear()` + re-instantiation, not by mutating this.
settings: Final[Settings] = _settings_cache()
