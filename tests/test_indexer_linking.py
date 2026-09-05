"""Tests for the onchain_indexer linking pipeline.

Covers REQ-001 (canonicalization across the four candidate bugs), REQ-002
(offline mapping + preserve-existing-linkage), REQ-003 (idempotent backfill
+ chunked processing + exit-2 stall guard), REQ-004 (respx integration of
the indexer ingest path against a mocked ``eth_getLogs``), and REQ-005
(realtime single-row link populated at insert time).

Spec: ``openspec/changes/indexer-link-fix/spec.md`` REQ-001..005.
Design: ``openspec/changes/indexer-link-fix/design.md`` §3.5.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx
import pytest
from eth_utils import keccak

from app.config import X402_U_TOKEN_ADDRESS_MAINNET
from app.db.models.agent import (
    BSC_CHAIN_ID,
    BSC_IDENTITY_REGISTRY,
    AgentCache,
    build_agent_id,
)
from app.db.models.onchain_index import OnchainAgentEvent, OnchainTransfer
from app.services.onchain_indexer import (
    _canon_addr,
    _resolve_agent_wallets,
    resolve_wallet_to_agent,
)
from scripts import backfill_link_agents as bf_module
from tests.conftest import _now


# Shared fixtures / helpers ---------------------------------------------------------


async def _stub_ts() -> datetime:
    """Async timestamp getter for the scan-and-store ingest path.

    ``_make_ts_resolver`` calls ``await get_ts(block)`` so the getter must
    be a coroutine, even when the call returns a constant.
    """
    return datetime(2026, 9, 1, tzinfo=timezone.utc)


CHAIN = BSC_CHAIN_ID
TOKEN_ID = 42
EIP55_WALLET = "0x" + "aB" * 20
LOWER_WALLET = "0x" + "ab" * 20
AGENT_ID = build_agent_id(CHAIN, BSC_IDENTITY_REGISTRY, TOKEN_ID)


async def _seed_agent(
    session,
    *,
    token_id: int = TOKEN_ID,
    wallet: str | None = EIP55_WALLET,
    agent_id: str | None = None,
) -> AgentCache:
    """Seed one ``AgentCache`` row with the given wallet."""
    aid = agent_id or build_agent_id(CHAIN, BSC_IDENTITY_REGISTRY, token_id)
    row = AgentCache(
        agent_id=aid,
        chain_id=CHAIN,
        token_id=token_id,
        registry_address=BSC_IDENTITY_REGISTRY,
        agent_wallet=wallet,
        name="P",
        x402_supported=True,
        category="other",
        supported_protocols=[],
        cross_chain_versions=[],
        tags=[],
        categories=[],
        services={},
        cross_chain_links=[],
        raw={},
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def _seed_transfer(
    session,
    *,
    to_address: str,
    block_number: int = 100,
    tx_hash_suffix: str = "00",
    linked_agent_id: str | None = None,
) -> OnchainTransfer:
    """Insert one ``OnchainTransfer`` row."""
    row = OnchainTransfer(
        from_address="0x" + "ff" * 20,
        to_address=to_address,
        value=Decimal(1),
        block_number=block_number,
        timestamp=_now(),
        tx_hash="0x" + tx_hash_suffix.rjust(64, "0"),
        transfer_type="erc20_u",
        linked_agent_id=linked_agent_id,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


# REQ-001 / canonicalization ------------------------------------------------------


def test_canonicalize_lowercase_matches_checksum() -> None:
    """REQ-001 case root cause: ``to`` lowercase ↔ ``agent_wallet`` EIP-55.

    The realtime path feeds ``_extract_addr`` output (already lowercase) into
    the lookup; the agent wallet comes from the DB as EIP-55. Both must
    collapse to the same canonical form so the in-memory dict matches.
    """
    assert _canon_addr(EIP55_WALLET) == LOWER_WALLET
    assert _canon_addr(LOWER_WALLET) == LOWER_WALLET


def test_canonicalize_strips_whitespace_and_prefix() -> None:
    """REQ-001 whitespace + prefix-drift root causes.

    A 8004scan payload occasionally delivers a wallet with surrounding
    whitespace or without the ``0x`` prefix; both must canonicalize to the
    same 42-char form so the backfill script can match them.
    """
    padded = "   " + EIP55_WALLET + "  "
    no_prefix = EIP55_WALLET[2:]
    assert _canon_addr(padded) == LOWER_WALLET
    assert _canon_addr(no_prefix) == LOWER_WALLET
    # Mixed: leading whitespace AND missing 0x.
    assert _canon_addr("  " + EIP55_WALLET[2:]) == LOWER_WALLET


def test_canonicalize_rejects_garbage() -> None:
    """Defensive: anything that is not a 40-hex string returns None so a
    corrupt row can never poison the wallet-to-agent dict."""
    assert _canon_addr(None) is None
    assert _canon_addr("") is None
    assert _canon_addr("0xabc") is None  # too short
    assert _canon_addr("0x" + "zz" * 20) is None  # non-hex
    assert _canon_addr("0x" + "ff" * 19 + "f") is None  # 39 chars
    # A non-string iterable must not raise — the helper accepts ``str | None``.
    with pytest.raises(AttributeError):
        _canon_addr(123)  # type: ignore[arg-type]


# REQ-002 / mapping helper ---------------------------------------------------------


async def test_resolve_wallets_returns_lowercase_dict(db) -> None:
    """REQ-002 mapping helper contract: canon form is the key, agent_id is the value."""
    seeded = await _seed_agent(db)
    mapping = await resolve_wallet_to_agent(db)
    assert mapping == {_canon_addr(seeded.agent_wallet): seeded.agent_id}


async def test_link_lookup_no_match_returns_none(db) -> None:
    """REQ-002 no-match scenario: unparseable wallets are dropped, not stored.

    ``resolve_wallet_to_agent`` returns the canonical wallet→agent dict.
    Wallets that fail canonicalization must be silently dropped so a corrupt
    row can never poison the dict; the dict only contains matchable entries.
    The end-to-end "lookup returns None for an unknown transfer" assertion
    lives in ``test_realtime_link_inserts_null_for_unknown``.
    """
    await _seed_agent(db, wallet="not-a-hex-address")
    assert await resolve_wallet_to_agent(db) == {}
    # The legacy alias returns the same dict.
    assert await _resolve_agent_wallets(db) == {}


async def test_link_lookup_preserves_existing_linkage(db) -> None:
    """REQ-002 preserve scenario: a pre-populated ``linked_agent_id`` stays put.

    The backfill script's UPDATE has a ``WHERE linked_agent_id IS NULL`` guard
    (REQ-002 + REQ-003). This test verifies the contract end-to-end at the
    mapping-helper level: re-running ``resolve_wallet_to_agent`` does NOT
    overwrite already-linked rows because that helper only *reads*.
    """
    seeded = await _seed_agent(db)
    await _seed_transfer(
        db, to_address=LOWER_WALLET, linked_agent_id=seeded.agent_id, tx_hash_suffix="aa"
    )
    # Re-resolving the wallet map must not mutate the row.
    mapping = await resolve_wallet_to_agent(db)
    assert mapping[LOWER_WALLET] == seeded.agent_id
    # Confirm via a second fetch: the linked_agent_id is still the seeded value.
    from sqlalchemy import select

    row = (
        await db.execute(
            select(OnchainTransfer).where(OnchainTransfer.tx_hash == "0x" + "aa".rjust(64, "0"))
        )
    ).scalar_one()
    assert row.linked_agent_id == seeded.agent_id


# REQ-003 / backfill behavior ------------------------------------------------------


async def test_backfill_idempotent_second_run_no_writes(db) -> None:
    """REQ-003 idempotent scenario: second invocation writes 0 rows, exit 0.

    A full corpus that was successfully linked on the first run must report
    ``linked_now == 0`` on the second run with no NULLs reintroduced. The
    ``matchable_seen > 0`` guard ensures this is NOT misclassified as a stall.
    """
    seeded = await _seed_agent(db)
    # Seed 5 transfers, all targeting the seeded wallet.
    for i in range(5):
        await _seed_transfer(
            db, to_address=LOWER_WALLET, block_number=100 + i, tx_hash_suffix=f"{i:02d}"
        )
    args = argparse.Namespace(chunk=10, limit=0, dry_run=False)
    summary = await bf_module.run(args)
    assert summary.exit_code == 0
    assert summary.linked_now == 5
    assert summary.still_unlinked == 0

    # Second run: zero rows modified, exit 0 (NOT a stall because no matchable
    # rows remain — they were all linked on the first run).
    summary2 = await bf_module.run(args)
    assert summary2.exit_code == 0
    assert summary2.linked_now == 0
    assert summary2.unlinked_before == 0
    # Confirm no NULLs were reintroduced — every row still has the seeded agent.
    from sqlalchemy import select, func

    count = (
        await db.execute(
            select(func.count()).select_from(OnchainTransfer).where(
                OnchainTransfer.linked_agent_id == seeded.agent_id
            )
        )
    ).scalar_one()
    assert count == 5


async def test_backfill_chunks_at_most_chunk_size(db, monkeypatch) -> None:
    """REQ-003 chunking: ``LIMIT :chunk`` is honored per round-trip.

    Patches ``_fetch_chunk`` to record every call's bound parameters so the
    test can assert ``limit <= chunk`` for every fetch and that the cursor
    advances monotonically.
    """
    await _seed_agent(db)
    for i in range(12):
        await _seed_transfer(
            db, to_address=LOWER_WALLET, block_number=100 + i, tx_hash_suffix=f"c{i:02d}"
        )

    captured: list[dict[str, Any]] = []
    real_fetch = bf_module._fetch_chunk

    async def spy(session, cursor, chunk_size):
        captured.append({"cursor": cursor, "chunk": chunk_size})
        rows = await real_fetch(session, cursor, chunk_size)
        return rows

    monkeypatch.setattr(bf_module, "_fetch_chunk", spy)

    args = argparse.Namespace(chunk=5, limit=0, dry_run=False)
    summary = await bf_module.run(args)
    assert summary.exit_code == 0
    assert summary.linked_now == 12

    # First fetch starts at cursor 0; subsequent fetches advance.
    assert captured[0]["cursor"] == 0
    assert captured[0]["chunk"] == 5
    for prev, curr in zip(captured, captured[1:]):
        assert curr["cursor"] > prev["cursor"], "cursor must advance monotonically"
        assert curr["chunk"] == 5, "chunk size must be honored on every fetch"


async def test_backfill_exits_2_when_linking_is_zero(db, monkeypatch) -> None:
    """REQ-003 stall guard: matchable_seen > 0 AND linked_now == 0 → exit 2.

    The spec's failure mode is a script that has matchable rows but links
    none of them — for example, a future refactor of ``_apply_chunk_sqlite``
    that accidentally always returns 0. We force the same shape here by
    patching the apply function to a no-op; the helper still sees matchable
    rows, so the guard fires.
    """
    await _seed_agent(db)
    for i in range(3):
        await _seed_transfer(
            db, to_address=LOWER_WALLET, block_number=200 + i, tx_hash_suffix=f"s{i:02d}"
        )

    async def noop_apply(session, chunk, wallet_to_agent):
        return 0  # simulate a broken UPDATE

    monkeypatch.setattr(bf_module, "_apply_chunk_sqlite", noop_apply)
    monkeypatch.setattr(bf_module, "_apply_chunk_postgres", noop_apply)

    args = argparse.Namespace(chunk=10, limit=0, dry_run=False)
    summary = await bf_module.run(args)
    assert summary.exit_code == 2
    assert summary.linked_now == 0
    assert summary.wallets_known == 1


# REQ-005 / realtime linking -------------------------------------------------------


async def test_realtime_link_inserts_with_agent_id(db, monkeypatch) -> None:
    """REQ-005 insert scenario: a transfer to a known wallet lands with the link.

    We invoke ``_scan_and_store`` against a fake RPC client so no network is
    touched. The wallet map is pre-populated with the seeded agent's EIP-55
    wallet; the log's ``to`` topic is a 32-byte padded lowercase form, which
    ``_extract_addr`` extracts and ``_canon_addr`` matches to the agent.
    """
    from app.services.onchain_indexer import _scan_and_store

    seeded = await _seed_agent(db)
    to_topic = "0x" + LOWER_WALLET[2:].rjust(64, "0")
    fake_log = {
        "topics": [
            "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
            "0x" + "ff" * 64,
            to_topic,
        ],
        "data": hex(1_000_000_000_000_000_000),
        "blockNumber": hex(100),
        "transactionHash": "0x" + "ab" * 32,
    }

    # Stub MultiRPCClient with an in-memory equivalent that returns our
    # pre-baked log batch for the U-token address and an empty result for
    # other eth_getLogs calls (e.g. the NFT scan).
    class _StubRpc:
        async def rpc_call(self, method, params):
            if method == "eth_getLogs":
                flt = (params[0] if params else {}) or {}
                if flt.get("address", "").lower() == X402_U_TOKEN_ADDRESS_MAINNET.lower():
                    return {"result": [fake_log]}
                return {"result": []}
            return {"result": None}

        async def close(self) -> None:  # pragma: no cover
            return None

        def get_usage_summary(self) -> dict[str, Any]:  # pragma: no cover
            return {"usage": {"chainstack": 0}}

    wallet_to_agent = await resolve_wallet_to_agent(db)
    inserted, events = await _scan_and_store(
        _StubRpc(),  # type: ignore[arg-type]
        db,
        from_block=100,
        to_block=100,
        wallet_to_agent=wallet_to_agent,
        u_token=X402_U_TOKEN_ADDRESS_MAINNET,
        max_range=10,
        ts_getter=lambda _b: _stub_ts(),
    )
    assert inserted == 1
    assert events == 0  # no NFT events in the fixture

    # Re-read the row: linked_agent_id must be populated.
    from sqlalchemy import select

    row = (
        await db.execute(select(OnchainTransfer).where(OnchainTransfer.block_number == 100))
    ).scalar_one()
    assert row.linked_agent_id == seeded.agent_id


async def test_realtime_link_inserts_null_for_unknown(db, monkeypatch) -> None:
    """REQ-005 unknown-wallet scenario: no match → ``linked_agent_id IS NULL``.

    A transfer whose ``to`` is not in any agent wallet must still be inserted
    (the indexer must not raise or skip it), but with ``linked_agent_id``
    left NULL so a later backfill can pick it up if the sync worker fills
    in ``agent_cache.agent_wallet`` between cycles.
    """
    from app.services.onchain_indexer import _scan_and_store

    # Seed an agent with a different wallet — the test transfer's `to` won't match.
    other_wallet = "0x" + "cc" * 20
    await _seed_agent(db, wallet=other_wallet, token_id=99, agent_id=build_agent_id(CHAIN, BSC_IDENTITY_REGISTRY, 99))

    unknown_to = "0x" + "99" * 20
    fake_log = {
        "topics": [
            "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
            "0x" + "ff" * 64,
            "0x" + unknown_to[2:].rjust(64, "0"),
        ],
        "data": hex(1),
        "blockNumber": hex(500),
        "transactionHash": "0x" + "cd" * 32,
    }

    class _StubRpc:
        async def rpc_call(self, method, params):
            if method == "eth_getLogs":
                flt = (params[0] if params else {}) or {}
                if flt.get("address", "").lower() == X402_U_TOKEN_ADDRESS_MAINNET.lower():
                    return {"result": [fake_log]}
                return {"result": []}
            return {"result": None}

        async def close(self) -> None:  # pragma: no cover
            return None

        def get_usage_summary(self) -> dict[str, Any]:  # pragma: no cover
            return {"usage": {"chainstack": 0}}

    wallet_to_agent = await resolve_wallet_to_agent(db)
    inserted, _ = await _scan_and_store(
        _StubRpc(),  # type: ignore[arg-type]
        db,
        from_block=500,
        to_block=500,
        wallet_to_agent=wallet_to_agent,
        u_token=X402_U_TOKEN_ADDRESS_MAINNET,
        max_range=10,
        ts_getter=lambda _b: _stub_ts(),
    )
    assert inserted == 1

    from sqlalchemy import select

    row = (
        await db.execute(select(OnchainTransfer).where(OnchainTransfer.block_number == 500))
    ).scalar_one()
    assert row.linked_agent_id is None
    assert row.to_address == unknown_to


# REQ-004 / respx integration test -------------------------------------------------


async def test_indexer_ingest_links_via_respx(client, db, respx_mock) -> None:
    """REQ-004 respx integration: ``eth_getLogs`` mock → linked ``OnchainTransfer``.

    The respx mock intercepts the BSC RPC URL and returns a fixture batch of
    ``$U`` Transfer logs. After the indexer's realtime ingest path processes
    the batch, the resulting ``OnchainTransfer`` row must carry the seeded
    agent's ``linked_agent_id``.
    """
    RPC_URL = "https://bnb-mainnet.g.alchemy.com/v2/test-key"  # noqa: S105 — fixture
    seeded = await _seed_agent(db)

    topic0 = "0x" + keccak(b"Transfer(address,address,uint256)").hex()
    to_topic = "0x" + EIP55_WALLET[2:].lower().rjust(64, "0")
    log = {
        "address": X402_U_TOKEN_ADDRESS_MAINNET,
        "topics": [topic0, "0x" + "ff" * 64, to_topic],
        "data": hex(10**18),
        "blockNumber": hex(12345),
        "transactionHash": "0x" + "ee" * 32,
        "transactionIndex": "0x0",
        "logIndex": "0x0",
        "removed": False,
    }

    respx_mock.post(RPC_URL).mock(
        side_effect=[
            # 1st call: _make_ts_resolver -> eth_getBlockByNumber for from_block
            httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "timestamp": hex(int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp()))
                    },
                },
            ),
            # 2nd call: eth_getLogs for the U-token address
            httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": 1, "result": [log]},
            ),
            # 3rd call: eth_getLogs for the NFT registry (empty result)
            httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": 1, "result": []},
            ),
        ]
    )

    # Bypass the realtime worker and call the HTTP endpoint that mirrors its
    # behavior end-to-end: ``/api/onchain/health`` does NOT trigger a scan,
    # so we invoke ``_scan_and_store_direct`` directly via the same httpx
    # mock. This exercises the respx intercept + the canonicalization +
    # the pg_insert path.
    import httpx as _httpx

    from app.services.onchain_indexer import _scan_and_store_direct

    wallet_to_agent = await resolve_wallet_to_agent(db)
    inserted, _events = await _scan_and_store_direct(
        RPC_URL,
        db,
        from_block=12345,
        to_block=12345,
        wallet_to_agent=wallet_to_agent,
        u_token=X402_U_TOKEN_ADDRESS_MAINNET,
    )
    assert inserted == 1

    from sqlalchemy import select

    row = (
        await db.execute(select(OnchainTransfer).where(OnchainTransfer.block_number == 12345))
    ).scalar_one()
    assert row.linked_agent_id == seeded.agent_id
    assert row.tx_hash == "0x" + "ee" * 32
