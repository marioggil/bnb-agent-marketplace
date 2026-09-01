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
import importlib
import json
import os
import secrets
import tempfile
import time
from datetime import datetime, timezone

# 1. Env preflight — must precede every `app.*` import.
os.environ.setdefault("SECRET_KEY", "test-secret-key-min-32-bytes-long-12345")
# Point the production lazy engine at the SAME shared file as _TEST_ENGINE
# (not :memory:), so any module the TestClient thread uses without our
# monkeypatch still reads the seeded tables.
_TEST_DB_PATH = os.path.join(tempfile.gettempdir(), "bnb_agent_test.sqlite3")
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TEST_DB_PATH}")
os.environ.setdefault("8004SCAN_BASE", "https://8004scan.io/api/v1/public")
# Disable on-chain indexer during tests — the real RPC key must not cause
# background TCP connections in the test suite.
os.environ.pop("ALCHEMY_API_KEY", None)

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
from sqlalchemy import (  # noqa: E402
    JSON,
    DateTime,
    DefaultClause,
    Integer,
    String,
    Table,
    event,
    text,
)
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.types import TypeDecorator  # noqa: E402

import app.main as main_module  # noqa: E402
from app.db import session as session_module  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.models.agent import AgentCache  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import create_app  # noqa: E402
from app.routers.hires import get_broadcaster  # noqa: E402
from app.services.auth import issue_csrf  # noqa: E402
from app.services.payment import FakeBroadcaster  # noqa: E402

# 2. aiosqlite engine. Use a fixed-path temp sqlite (not :memory:) so the
#    TestClient thread and the async fixtures see the SAME database even if
#    the conftest is imported twice (once per thread). The file is removed
#    at session start (in _create_schema), NOT at import time — a second
#    import (TestClient thread) must not wipe the file the first import set
#    up.
_TEST_ENGINE = create_async_engine(
    f"sqlite+aiosqlite:///{_TEST_DB_PATH}",
    connect_args={"check_same_thread": False},
    # sqlite autocommit + a real pool (NOT StaticPool): the async `db`
    # fixture and the sync TestClient thread use separate connections to
    # the shared file, and every committed statement is visible to both.
    isolation_level="AUTOCOMMIT",
)


@event.listens_for(_TEST_ENGINE.sync_engine, "connect")
def _enable_sqlite_fk(dbapi_conn, _record):
    """sqlite disables FK enforcement by default; enable it per connection
    so ON DELETE CASCADE semantics match Postgres."""
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


_TestSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    _TEST_ENGINE,
    expire_on_commit=False,
    class_=AsyncSession,
)


def _index_sql(index: Table) -> str:
    """Render an index's expressions to text (postgres-only ops like
    `DESC NULLS LAST` are not portable to sqlite)."""
    try:
        return " ".join(str(e) for e in index.expressions)
    except Exception:
        return ""


#: Indexes defined with postgres-only SQL that sqlite cannot create.
_POSTGRES_ONLY_INDEXES = {"ix_agent_cache_average_score_desc"}


#: sqlite cannot store tz-aware datetimes; this decorator marks results as
#: UTC on read so app comparisons (datetime.now(tz=utc)) behave like Postgres.
class _UtcAwareDateTime(TypeDecorator):
    impl = DateTime
    cache_ok = True

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


def _patch_metadata_for_sqlite() -> None:
    """Drop GENERATED col, swap JSONB → JSON, strip pg-only indexes."""
    # `category` is a GENERATED (Computed) column in the model. Mutating the
    # existing column (clearing `.computed` + a plain default) instead of
    # removing/re-adding keeps the ORM constructor consistent — removing the
    # column regenerates `__init__` and duplicates keyword arguments.
    cat_col = AgentCache.__table__.c.get("category")
    if cat_col is not None:
        cat_col.computed = None  # type: ignore[attr-defined]
        cat_col.server_default = DefaultClause("'other'")
    for tbl in Base.metadata.tables.values():
        for col in list(tbl.columns):
            if type(col.type).__name__ == "JSONB":
                col.type = JSON()
                # Postgres casts in server defaults (`'[]'::jsonb`) are
                # invalid sqlite. Replace with a DefaultClause wrapping a
                # raw string: sqlite emits `DEFAULT '[]'` and SQLAlchemy's
                # `_insert_cols_as_none` can bool() it (a TextClause would
                # raise "Boolean value of this clause is not defined").
                sd = col.server_default
                if sd is not None and hasattr(sd, "arg"):
                    raw = getattr(sd.arg, "text", None)
                    if raw and "::jsonb" in raw:
                        # Keep a JSON-valid literal without the sqlite
                        # quoting: DefaultClause quotes strings itself, so
                        # `[]`/`{}` become DEFAULT '[]' / DEFAULT '{}' and
                        # the stored value parses back as JSON.
                        literal = raw.replace("::jsonb", "").strip().strip("'")
                        col.server_default = DefaultClause(literal)
            # Postgres-native UUID type has no portable sqlite DDL; store
            # the string form instead (values are hex UUIDs either way).
            if type(col.type).__name__ == "UUID":
                col.type = String(36)
            # Postgres `now()` has no sqlite function. Replace the default
            # with a literal naive ISO timestamp (quoted by DefaultClause);
            # rows inserted without created_at/updated_at (e.g. auth_nonce
            # from the auth service) still get a parseable timestamp.
            sd = col.server_default
            if sd is not None and hasattr(sd, "arg"):
                raw = getattr(sd.arg, "text", None)
                if raw and raw.strip().lower() == "now()":
                    col.server_default = DefaultClause("2026-01-01T00:00:00")
            # sqlite has no tz support: DateTime(timezone=True) columns read
            # back as naive datetimes while the app compares with
            # timezone-aware now(). Convert them to a UTC-aware DateTime so
            # comparisons work like on Postgres.
            if type(col.type).__name__ == "DateTime" and getattr(col.type, "timezone", False):
                col.type = _UtcAwareDateTime()
            # sqlite autoincrements only INTEGER PRIMARY KEY, not BIGINT;
            # shrink BigInteger PKs so inserts work without explicit ids.
            if (
                col.primary_key
                and type(col.type).__name__ == "BigInteger"
                and getattr(col, "autoincrement", False)
            ):
                col.type = Integer()
        kept = [
            i
            for i in list(tbl.indexes)
            if not (getattr(i, "dialect_options", None) or {}).get("postgresql")
            and i.name not in _POSTGRES_ONLY_INDEXES
        ]
        for i in kept:
            # SQLAlchemy re-derives indexes from the model on create_all;
            # deleting them from `tbl._indexes` is not enough. Instead,
            # neutralize postgres-only expressions in place: strip the
            # `NULLS LAST` suffix so sqlite accepts the DDL.
            try:
                exprs = list(i.expressions)
                for j, e in enumerate(exprs):
                    s = str(e)
                    if "NULLS LAST" in s:
                        exprs[j] = text(s.replace(" DESC NULLS LAST", ""))
                i.expressions = exprs
            except Exception:
                pass
        tbl._indexes = tuple(kept)  # type: ignore[attr-defined]


_patch_metadata_for_sqlite()
session_module.engine = _TEST_ENGINE
session_module.AsyncSessionLocal = _TestSessionLocal
main_module.engine = _TEST_ENGINE
main_module.AsyncSessionLocal = _TestSessionLocal
# Force the lazy engine factory to return the test engine for every module
# that calls make_engine()/get_engine()/get_sessionmaker() at runtime
# (auth, hires, pages, sync, ...). Without this, modules imported before
# the reassignment above keep the production lazy engine and the TestClient
# thread reads an empty database.
session_module.make_engine = lambda url=None: _TEST_ENGINE  # type: ignore[assignment]
session_module.get_engine = lambda: _TEST_ENGINE  # type: ignore[assignment]
session_module.get_sessionmaker = lambda: _TestSessionLocal  # type: ignore[assignment]


@pytest.fixture(autouse=True)
def _skip_postgres_without_env(request):
    """Skip @pytest.mark.postgres tests unless RUN_POSTGRES_TESTS=1.

    The default suite runs on aiosqlite (zero infra); tests that need real
    Postgres features (GENERATED columns, trigram, ON CONFLICT, FK cascade
    semantics) are skipped by default and opt-in via the env var.
    """
    if request.node.get_closest_marker("postgres") and os.environ.get("RUN_POSTGRES_TESTS") != "1":
        pytest.skip("requires real Postgres; set RUN_POSTGRES_TESTS=1 with a DSN")


@pytest.fixture(scope="session", autouse=True)
async def _create_schema():
    # Fresh DB per session: remove any leftover file, then let the lazy
    # per-connection listener build the schema on the next connect.
    if os.path.exists(_TEST_DB_PATH):
        os.remove(_TEST_DB_PATH)
    # `Base.metadata.create_all` re-derives the postgres-only `DESC NULLS
    # LAST` index from the model and fails on sqlite. Compile the DDL for
    # sqlite explicitly, skipping that index, and register it as a lazy
    # per-connection init so every thread/loop (including the TestClient's
    # own loop) sees the tables in the shared file.
    from sqlalchemy import create_engine as _sync_engine_factory
    from sqlalchemy.event import listen
    from sqlalchemy.schema import CreateIndex, CreateTable

    sqlite_dialect = _sync_engine_factory("sqlite://").dialect
    statements: list[str] = []
    for tbl in Base.metadata.sorted_tables:
        statements.append(str(CreateTable(tbl).compile(dialect=sqlite_dialect)))
        for idx in tbl.indexes:
            sql = str(CreateIndex(idx).compile(dialect=sqlite_dialect))
            if "NULLS LAST" not in sql:
                statements.append(sql)

    def _ensure_schema(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        try:
            cur.execute("SELECT count(*) FROM sqlite_master WHERE type='table'")
            if cur.fetchone()[0] == 0:
                for stmt in statements:
                    cur.execute(stmt)
        finally:
            cur.close()

    listen(_TEST_ENGINE.sync_engine, "connect", _ensure_schema)
    # Touch the engine once so the session-scope schema is ready before the
    # first test (and so any non-TestClient async fixtures see it).
    async with _TEST_ENGINE.begin():
        pass
    yield
    await _TEST_ENGINE.dispose()


@pytest.fixture(autouse=True)
async def _truncate_tables():
    yield
    async with _TEST_ENGINE.begin() as conn:
        for t in reversed(Base.metadata.sorted_tables):
            try:
                await conn.execute(t.delete())
            except Exception:
                # Table may not exist in this connection yet (sync
                # TestClient tests run in a different loop/thread than the
                # session-scoped schema fixture). Truncation is best-effort.
                pass


def clear_settings_cache() -> None:
    """Reset the Settings singleton (get_settings wraps _settings_cache)."""
    from app.config import _settings_cache

    _settings_cache.cache_clear()


def reset_onchain_client() -> None:
    """Reset the AlchemyOnchainClient singleton so httpx connections are closed."""
    import app.services.client_bscscan as mod

    if mod._onchain_client is not None and mod._onchain_client._client is not None:
        # In a sync teardown, the async client's transport may linger.
        # Mark it closed so __del__ won't warn about an unclosed socket.
        try:
            mod._onchain_client._client._transport = None  # type: ignore[union-attr]
        except Exception:
            pass
    mod._onchain_client = None


def _now() -> datetime:
    """UTC datetime for explicit created_at/updated_at in seeds.

    The model's postgres `now()` server default is not evaluated by sqlite,
    so tests that insert rows must provide the timestamp explicitly.
    """
    return datetime.now(timezone.utc)


@pytest.fixture
def app():
    clear_settings_cache()
    reset_onchain_client()
    # Modules that did `from app.db.session import AsyncSessionLocal` at
    # import time captured the lazy `__getattr__` shim, not our test
    # sessionmaker. Re-bind every router/service that uses it so their
    # direct usage hits the test DB (not the production lazy engine).
    for _mod in (
        "app.routers.pages",
        "app.routers.hires",
        "app.routers.favorites",
        "app.routers.agents",
        "app.routers.healthz",
        "app.routers.sync",
        "app.services.auth",
        "app.services.sync_worker",
    ):
        try:
            _m = importlib.import_module(_mod)
            _m.AsyncSessionLocal = _TestSessionLocal
        except Exception:
            pass
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
        # Start from a clean, committed view so rows written by the
        # TestClient thread (separate connection) are visible.
        await session.rollback()
        yield session
        await session.rollback()
        await session.close()


@pytest.fixture(autouse=True)
def _expire_db_before_use(db):
    """No-op placeholder kept for clarity: the `db` fixture above is
    function-scoped and closes its session at teardown, so tests that
    interleave `client` writes and `db` reads re-open a fresh session."""
    yield db


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
    if network is not None:
        # Keep `accepted.network` in sync with the envelope network so
        # decode's `_chain_id_of` (which prefers accepted.network) sees the
        # override. Mutates the challenge's accepts entry by design.
        accept["network"] = network
    if token is not None:
        # Same for the asset: keep accepted.asset in sync so decode sees
        # the override instead of the challenge's original token.
        accept["asset"] = token
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
    token_addr = token or accept.get("asset") or "0x0000000000000000000000000000000000000000"
    # eth-account >= 0.13 uses positional args for encode_typed_data.
    typed = encode_typed_data(
        {
            "name": accept["extra"]["name"],
            "version": accept["extra"]["version"],
            "chainId": chain_id,
            "verifyingContract": token_addr,
        },
        {
            "TransferWithAuthorization": [
                {"name": "from", "type": "address"},
                {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"},
                {"name": "nonce", "type": "bytes32"},
            ]
        },
        {
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
        200,
        json={"total_agents": 0, "total_feedbacks": 0, "total_chains": 1},
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
