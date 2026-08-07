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

import os
import secrets

# 1. Env preflight — must precede every `app.*` import.
os.environ.setdefault("SECRET_KEY", "test-secret-key-min-32-bytes-long-12345")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("8004SCAN_BASE", "https://8004scan.io/api/v1/public")

import pytest  # noqa: E402
import respx  # noqa: E402
from eth_account import Account  # noqa: E402
from eth_account.messages import encode_defunct  # noqa: E402
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
from app.services.auth import issue_csrf  # noqa: E402

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
