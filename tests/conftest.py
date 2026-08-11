"""Smoke-suite conftest: env preflight, aiosqlite engine, fixtures.

Order (spec R1-R4):
  1. env vars set BEFORE any `app.*` import.
  2. metadata patched: GENERATED col → plain Text; JSONB → JSON; pg-only
     indexes dropped (so `create_all` works on sqlite).
  3. `app.db.session.engine` / `AsyncSessionLocal` swapped for the test engine.
  4. `app.main.engine` / `AsyncSessionLocal` swapped too (lifespan reads
     from app.main's bound names).
  5. `get_db` overridden on each fresh `app` fixture.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import time

# 1. Env preflight — must precede every `app.*` import.
os.environ.setdefault("SECRET_KEY", "test-secret-key-min-32-bytes-long-12345")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("8004SCAN_BASE", "https://8004scan.io/api/v1/public")

# x402 (FU-2) test defaults: testnet 97, RPC never reached (offline suite),
# facilitator "configured" with a fixed test key so pay tests pass the
# gateway gate; the unconfigured path is tested by clearing the key.
TEST_FACILITATOR_KEY: str = "0x4c0883a69102937d6231471b5dbb6204fe5129617082792ae468d01a3f362318"
os.environ.setdefault("X402_CHAIN_ID", "97")
os.environ.setdefault("X402_RPC_URL", "https://rpc.example.invalid")
os.environ.setdefault("X402_FACILITATOR_KEY", TEST_FACILITATOR_KEY)

import pytest  # noqa: E402
import respx  # noqa: E402
from eth_account import Account  # noqa: E402
from eth_account.messages import encode_defunct, encode_typed_data  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import JSON, Column, Table, Text, text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession, async_sessionmaker, create_async_engine,
)
from sqlalchemy.pool import StaticPool  # noqa: E402

import app.main as main_module  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import session as session_module  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.models.agent import AgentCache  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import create_app  # noqa: E402
from app.routers.hires import get_broadcaster  # noqa: E402
from app.services.auth import issue_csrf  # noqa: E402
from app.services.payment import FakeBroadcaster  # noqa: E402

# 2. aiosqlite engine (StaticPool + check_same_thread=False).
_TEST_ENGINE = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    _TEST_ENGINE, expire_on_commit=False, class_=AsyncSession,
)


def _patch_metadata_for_sqlite() -> None:
    """Drop GENERATED col, swap JSONB → JSON, strip pg-only indexes."""
    table: Table = AgentCache.__table__
    if "category" in table.c:
        table._columns.remove(table.c["category"])  # type: ignore[attr-defined]
    table.append_column(
        Column("category", Text, nullable=False, server_default=text("'other'"))
    )
    for tbl in Base.metadata.tables.values():
        for col in list(tbl.columns):
            if type(col.type).__name__ == "JSONB":
                col.type = JSON()
        kept = [i for i in list(tbl.indexes)
                if not (getattr(i, "dialect_options", None) or {}).get("postgresql")]
        tbl._indexes = tuple(kept)  # type: ignore[attr-defined]


_patch_metadata_for_sqlite()
session_module.engine = _TEST_ENGINE
session_module.AsyncSessionLocal = _TestSessionLocal
main_module.engine = _TEST_ENGINE
main_module.AsyncSessionLocal = _TestSessionLocal


@pytest.fixture(scope="session", autouse=True)
async def _create_schema():
    async with _TEST_ENGINE.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await _TEST_ENGINE.dispose()


@pytest.fixture(autouse=True)
async def _truncate_tables():
    yield
    async with _TEST_ENGINE.begin() as conn:
        for t in reversed(Base.metadata.sorted_tables):
            await conn.execute(t.delete())


@pytest.fixture
def app():
    get_settings.cache_clear()
    application = create_app()
    async def _override_get_db():
        async with _TestSessionLocal() as session:
            yield session
    application.dependency_overrides[get_db] = _override_get_db
    return application


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
async def db():
    async with _TestSessionLocal() as session:
        yield session


# Auth helpers (R3) — re-used by every test file.
def _new_address_and_key() -> tuple[str, bytes]:
    pk = secrets.token_bytes(32)
    return Account.from_key(pk).address, pk


def _sign_message(pk: bytes, nonce: str) -> tuple[str, str]:
    account = Account.from_key(pk)
    sig = account.sign_message(
        encode_defunct(text=f"Sign in to bnb_agent: {nonce}")
    ).signature.hex()
    return account.address, "0x" + sig if not sig.startswith("0x") else sig


def _sign_in(client) -> tuple[str, str]:
    """Issue nonce, sign, verify → (address, session_cookie_value)."""
    address, pk = _new_address_and_key()
    nonce = client.get(f"/auth/nonce?address={address}").json()["nonce"]
    _, signature = _sign_message(pk, nonce)
    verify = client.post(
        "/auth/verify",
        json={"address": address, "signature": signature, "nonce": nonce},
    )
    assert verify.status_code == 200, verify.text
    cookie = verify.cookies.get("bnb_agent_session")
    assert cookie is not None
    return address, cookie


def _sign_in_with_key(client) -> tuple[Account, str, str]:
    """Like `_sign_in` but also returns the `Account` for envelope signing."""
    address, pk = _new_address_and_key()
    nonce = client.get(f"/auth/nonce?address={address}").json()["nonce"]
    _, signature = _sign_message(pk, nonce)
    verify = client.post(
        "/auth/verify",
        json={"address": address, "signature": signature, "nonce": nonce},
    )
    assert verify.status_code == 200, verify.text
    cookie = verify.cookies.get("bnb_agent_session")
    assert cookie is not None
    return Account.from_key(pk), address, cookie


def build_signed_envelope(
    payer: Account,
    challenge: dict,
    *,
    from_: str | None = None,
    to: str | None = None,
    value: int | None = None,
    valid_after: int | None = None,
    valid_before: int | None = None,
    nonce: str | None = None,
    network: str | None = None,
    token: str | None = None,
) -> str:
    """Regenerable D4 X-PAYMENT envelope (base64) signed by `payer`.

    Defaults mirror the challenge's accepts[0] and D7 validity (validAfter
    now-120, validBefore now+maxTimeoutSeconds). Each call draws a fresh
    random nonce — no static signatures in the suite.
    """
    accept = challenge["accepts"][0]
    now = int(time.time())
    authorization = {
        "from": from_ or payer.address,
        "to": to or accept["payTo"],
        "value": str(value if value is not None else int(accept["amount"])),
        "validAfter": str(valid_after if valid_after is not None else now - 120),
        "validBefore": str(
            valid_before if valid_before is not None else now + int(accept["maxTimeoutSeconds"])
        ),
        "nonce": nonce or "0x" + secrets.token_hex(32),
    }
    chain_id = int((network or accept["network"]).split(":")[1])
    token_addr = token or accept["asset"]
    typed = encode_typed_data(
        domain={
            "name": accept["extra"]["name"],
            "version": accept["extra"]["version"],
            "chainId": chain_id,
            "verifyingContract": token_addr,
        },
        types={
            "TransferWithAuthorization": [
                {"name": "from", "type": "address"},
                {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"},
                {"name": "nonce", "type": "bytes32"},
            ]
        },
        message={
            "from": authorization["from"],
            "to": authorization["to"],
            "value": int(authorization["value"]),
            "validAfter": int(authorization["validAfter"]),
            "validBefore": int(authorization["validBefore"]),
            "nonce": authorization["nonce"],
        },
    )
    signature = payer.sign_message(typed).signature.hex()
    if not signature.startswith("0x"):
        signature = "0x" + signature
    envelope = {
        "x402Version": challenge["x402Version"],
        "scheme": accept["scheme"],
        "network": network or accept["network"],
        "resource": challenge["resource"],
        "accepted": accept,
        "payload": {
            "signature": signature,
            "authorization": authorization,
        },
    }
    return base64.b64encode(json.dumps(envelope).encode("utf-8")).decode("ascii")


@pytest.fixture
def seeded_user(client):
    return _sign_in(client)


@pytest.fixture
def csrf_token_for_session():
    return lambda cookie: issue_csrf(cookie)


@pytest.fixture
def respx_mock():
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
def mock_8004scan(respx_mock):
    respx_mock.get("https://8004scan.io/api/v1/public/stats").respond(
        200, json={"total_agents": 0, "total_feedbacks": 0, "total_chains": 1},
    )
    yield respx_mock


# ---------------------------------------------------------------------------
# x402 (FU-2) fixtures — payer + regenerable signed envelopes + fake broadcaster
# ---------------------------------------------------------------------------


@pytest.fixture
def payer() -> Account:
    """A random EOA the tests sign EIP-712 envelopes with."""
    return Account.from_key(secrets.token_bytes(32))


@pytest.fixture
def signed_envelope():
    """Factory fixture → `build_signed_envelope(payer, challenge, **kw)`."""
    return build_signed_envelope


@pytest.fixture
def fake_broadcaster(client) -> FakeBroadcaster:
    """Override the pay route's broadcaster so tests stay offline (design X5)."""
    fb = FakeBroadcaster()
    client.app.dependency_overrides[get_broadcaster] = lambda: fb
    return fb
