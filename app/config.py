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

from decimal import Decimal
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

#: BSC chain ids the marketplace can settle on. Testnet (97) is the default;
#: mainnet (56) is a demo-day override (design id 52, D5).
X402_CHAIN_TESTNET: Final[int] = 97
X402_CHAIN_MAINNET: Final[int] = 56

#: $U (United Stables) pinned per chain from @altananetwork/x402-server
#: `tokens.ts` at design time (design id 52, D2) — both verified against the
#: live `DOMAIN_SEPARATOR()`. Public addresses, not secrets.
X402_U_TOKEN_ADDRESS_MAINNET: Final[str] = "0xcE24439F2D9C6a2289F741120FE202248B666666"
X402_U_TOKEN_ADDRESS_TESTNET: Final[str] = "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565"

#: EIP-712 domain facts of $U (design id 52, D2/D4). `extra` in the challenge
#: and the typed-data domain both use these.
U_TOKEN_NAME: Final[str] = "United Stables"
U_TOKEN_VERSION: Final[str] = "1"

#: Default public RPC per chain; `X402_RPC_URL` overrides.
_X402_RPC_DEFAULTS: Final[dict[int, str]] = {
    X402_CHAIN_TESTNET: "https://bsc-testnet-rpc.publicnode.com",
    X402_CHAIN_MAINNET: "https://bsc-rpc.publicnode.com",
}


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

    # ---- x402 payments (FU-2, design id 52 D5) ---------------------------
    # BSC chain payments settle on: 97 (testnet, default) or 56 (mainnet,
    # demo-day override). The $U address and default RPC follow the chain.
    x402_chain_id: int = Field(
        default=X402_CHAIN_TESTNET,
        alias="X402_CHAIN_ID",
        ge=1,
        description="BSC chain id for payments (97 testnet default, 56 demo override).",
    )
    # Facilitator EOA private key (hex, with or without 0x). The facilitator
    # only ever pays gas — funds are recipient-bound inside the signed
    # envelope. Empty string ⇒ pay endpoint answers 503
    # `payment_gateway_unconfigured`. Never committed; `.env.example` only
    # ships a placeholder (spec B3).
    x402_facilitator_key: str = Field(
        default="",
        alias="X402_FACILITATOR_KEY",
        description="Facilitator EOA key for gas only; empty disables payments.",
    )
    # Optional RPC override. When empty, `x402_rpc_url_resolved` falls back
    # to the per-chain public node.
    x402_rpc_url: str = Field(
        default="",
        alias="X402_RPC_URL",
        description="Optional BSC RPC override; per-chain default otherwise.",
    )
    # Flat hire price in USD (Q1). Stored on the hire as $U units (18
    # decimals) and converted to wei for the challenge amount.
    x402_default_price_usd: Decimal = Field(
        default=Decimal("1.00"),
        alias="X402_DEFAULT_PRICE_USD",
        ge=Decimal("0"),
        description="Flat price of a hire in USD; converted to $U wei.",
    )
    # Pinned $U addresses per chain (D2). Public constants — overridable for
    # mirror/test deployments.
    x402_u_token_address_56: str = Field(
        default=X402_U_TOKEN_ADDRESS_MAINNET,
        alias="X402_U_TOKEN_ADDRESS_56",
        description="Pinned $U address on BSC mainnet (56).",
    )
    x402_u_token_address_97: str = Field(
        default=X402_U_TOKEN_ADDRESS_TESTNET,
        alias="X402_U_TOKEN_ADDRESS_97",
        description="Pinned $U address on BSC testnet (97).",
    )
    # Permit2 is declared for future rails (permit2-exact USDT) but is
    # unconsumed in v1 (Q2).
    x402_permit2_address: str = Field(
        default="0x000000000022D473030F116dDEE9F6B43aC78BA3",
        alias="X402_PERMIT2_ADDRESS",
        description="Permit2 address; reserved for future rails (unused in v1).",
    )

    # ---- On-chain indexer (multi-RPC) ------------------------------------
    alchemy_api_key: str = Field(
        default="",
        alias="ALCHEMY_API_KEY",
        description="Alchemy API key for BSC RPC. Fallback when Chainstack is unavailable.",
    )
    chainstack_api_key: str = Field(
        default="",
        alias="CHAINSTACK_API_KEY",
        description="Chainstack API key for BSC RPC. Primary provider (cheaper for eth_getLogs).",
    )

    @field_validator("secret_key")
    @classmethod
    def _secret_key_not_empty(cls, v: str) -> str:
        # Reject the literal placeholder that ships in .env.example to keep
        # the dev experience loud: if you forgot to copy .env.example → .env
        # the app refuses to start instead of silently signing with "change-me".
        stripped = v.strip()
        if not stripped or stripped.startswith("change-me"):
            raise ValueError("SECRET_KEY must be set to a real random value (see .env.example)")
        return v

    @field_validator("log_level")
    @classmethod
    def _log_level_upper(cls, v: str) -> str:
        return v.upper()

    # ------------------------------------------------------------------
    # Derived helpers — resolve the per-chain values the payment service
    # consumes. Kept as properties (not fields) so the env surface stays
    # flat and the chain switch is a single `X402_CHAIN_ID` flip (Q3).
    # ------------------------------------------------------------------

    @property
    def x402_rpc_url_resolved(self) -> str:
        """Effective RPC URL: `X402_RPC_URL` override, else the chain default."""
        if self.x402_rpc_url:
            return self.x402_rpc_url
        return _X402_RPC_DEFAULTS.get(self.x402_chain_id, _X402_RPC_DEFAULTS[X402_CHAIN_TESTNET])

    @property
    def x402_u_token_address(self) -> str:
        """$U address pinned for the configured chain (D2)."""
        if self.x402_chain_id == X402_CHAIN_MAINNET:
            return self.x402_u_token_address_56
        return self.x402_u_token_address_97

    @property
    def x402_payments_configured(self) -> bool:
        """True when a facilitator key is present (pay endpoint usable)."""
        return bool(self.x402_facilitator_key.strip())


def get_settings() -> Settings:
    """Return the cached `Settings` singleton."""
    return _settings_cache()


@lru_cache(maxsize=1)
def _settings_cache() -> Settings:
    return Settings()


# Module-level singleton for ergonomic imports. Tests should patch via
# `get_settings.cache_clear()` + re-instantiation, not by mutating this.
settings: Final[Settings] = _settings_cache()
