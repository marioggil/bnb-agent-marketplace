"""OFAC flagged-address feature tests: sync service, endpoint, badges, page.

Spec: DESIGN.md T2 (wallet risk flags). The upstream lists (public GitHub
raw JSON) are mocked with respx; the endpoint auth tests mirror
test_api_sync.py's key expectations (401 missing/wrong key, 503 unconfigured).
"""

from __future__ import annotations

import pytest

from app.db.models.flagged_address import FlaggedAddress
from app.services.flagged_sync import FLAGGED_SOURCES, refresh_flagged_addresses

BSC_URL = FLAGGED_SOURCES["ofac-bsc"]
ETH_URL = FLAGGED_SOURCES["ofac-eth"]

API_HEADERS = {"X-API-Key": "test-sync-api-key"}


@pytest.fixture
def sync_key(monkeypatch):
    """Set SYNC_API_KEY before the app fixture re-instantiates Settings."""
    monkeypatch.setenv("SYNC_API_KEY", "test-sync-api-key")
    return "test-sync-api-key"


# ---------------------------------------------------------------------------
# refresh_flagged_addresses — fetch, normalize, REPLACE-per-source
# ---------------------------------------------------------------------------


async def _seed_flagged(db, address: str, source: str = "ofac-bsc") -> None:
    db.add(FlaggedAddress(address=address, source=source))
    await db.commit()


async def _read_sources(db) -> dict[str, set[str]]:
    from sqlalchemy import select

    rows = (await db.scalars(select(FlaggedAddress))).all()
    out: dict[str, set[str]] = {}
    for row in rows:
        out.setdefault(row.source, set()).add(row.address)
    return out


async def test_refresh_inserts_both_sources(respx_mock, db):
    respx_mock.get(BSC_URL).respond(200, json=["0x" + "aa" * 20, "0x" + "bb" * 20])
    respx_mock.get(ETH_URL).respond(200, json=["0x" + "cc" * 20])

    report = await refresh_flagged_addresses()

    assert report.total_fetched == 3 and report.total_inserted == 3
    assert report.sources[0].source == "ofac-bsc" and report.sources[0].fetched == 2
    sources = await _read_sources(db)
    assert sources["ofac-bsc"] == {"0x" + "aa" * 20, "0x" + "bb" * 20}
    assert sources["ofac-eth"] == {"0x" + "cc" * 20}


async def test_refresh_normalizes_case_and_dedupes(respx_mock, db):
    respx_mock.get(BSC_URL).respond(
        200, json=["0x" + "AB" * 20, " 0x" + "cd" * 20 + " ", "0x" + "ab" * 20]
    )
    respx_mock.get(ETH_URL).respond(200, json=[])

    report = await refresh_flagged_addresses()

    assert report.sources[0].fetched == 3 and report.sources[0].inserted == 2
    sources = await _read_sources(db)
    assert sources["ofac-bsc"] == {"0x" + "ab" * 20, "0x" + "cd" * 20}


async def test_refresh_replaces_source_on_rerun(respx_mock, db):
    import httpx

    route = respx_mock.get(BSC_URL)
    route.side_effect = [
        httpx.Response(200, json=["0x" + "aa" * 20, "0x" + "bb" * 20]),
        httpx.Response(200, json=["0x" + "bb" * 20]),
    ]
    respx_mock.get(ETH_URL).respond(200, json=["0x" + "cc" * 20])
    await refresh_flagged_addresses()

    # Second run: one BSC address dropped upstream → gone here; ETH untouched.
    report = await refresh_flagged_addresses()

    assert report.total_fetched == 2 and report.total_inserted == 2
    sources = await _read_sources(db)
    assert sources["ofac-bsc"] == {"0x" + "bb" * 20}
    assert sources["ofac-eth"] == {"0x" + "cc" * 20}


async def test_refresh_same_address_in_both_sources_ok(respx_mock, db):
    """The real OFAC lists overlap (0x4f47… is on both BSC and ETH): the
    composite (address, source) PK must accept one row per source."""
    shared = "0x" + "4f" * 20
    respx_mock.get(BSC_URL).respond(200, json=[shared, "0x" + "aa" * 20])
    respx_mock.get(ETH_URL).respond(200, json=[shared, "0x" + "cc" * 20])

    report = await refresh_flagged_addresses()

    assert report.total_inserted == 4  # dedupe is per-source; shared stays in both
    sources = await _read_sources(db)
    assert sources["ofac-bsc"] == {shared, "0x" + "aa" * 20}
    assert sources["ofac-eth"] == {shared, "0x" + "cc" * 20}


# ---------------------------------------------------------------------------
# POST /api/sync/flagged — key auth (mirrors test_api_sync.py) + persistence
# ---------------------------------------------------------------------------


def test_sync_flagged_without_key_returns_401(sync_key, client):
    response = client.post("/api/sync/flagged")
    assert response.status_code == 401


def test_sync_flagged_with_wrong_key_returns_401(sync_key, client):
    response = client.post("/api/sync/flagged", headers={"X-API-Key": "wrong-key"})
    assert response.status_code == 401


def test_sync_flagged_unconfigured_key_returns_503(client, monkeypatch):
    monkeypatch.delenv("SYNC_API_KEY", raising=False)
    from app.config import _settings_cache

    _settings_cache.cache_clear()
    response = client.post("/api/sync/flagged")
    assert response.status_code == 503


async def test_sync_flagged_with_key_persists_rows(sync_key, client, db, respx_mock):
    respx_mock.get(BSC_URL).respond(200, json=["0x" + "aa" * 20])
    respx_mock.get(ETH_URL).respond(200, json=["0x" + "bb" * 20])

    response = client.post("/api/sync/flagged", headers=API_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["sources"]["ofac-bsc"] == {"fetched": 1, "inserted": 1}
    assert body["sources"]["ofac-eth"] == {"fetched": 1, "inserted": 1}
    assert body["total_fetched"] == 2 and body["total_inserted"] == 2
    # Rows persisted: the public page (same request loop) renders them.
    page = client.get("/flagged").text
    assert "0x" + "aa" * 20 in page and "0x" + "bb" * 20 in page


# ---------------------------------------------------------------------------
# Risk badges — card (home) + detail (T2)
# ---------------------------------------------------------------------------


FLAGGED = "0x" + "ab" * 20


async def _seed_agent(
    db,
    token_id: int = 1,
    owner_address: str | None = None,
    agent_wallet: str | None = None,
    x402_supported: bool = False,
) -> str:
    from app.db.models.agent import (
        BSC_CHAIN_ID,
        BSC_IDENTITY_REGISTRY,
        AgentCache,
        build_agent_id,
    )
    from tests.conftest import _now

    aid = build_agent_id(56, BSC_IDENTITY_REGISTRY, token_id)
    db.add(
        AgentCache(
            agent_id=aid,
            chain_id=BSC_CHAIN_ID,
            token_id=token_id,
            registry_address=BSC_IDENTITY_REGISTRY,
            name=f"Agent {token_id}",
            owner_address=owner_address,
            agent_wallet=agent_wallet,
            x402_supported=x402_supported,
            supported_protocols=[],
            cross_chain_versions=[],
            raw={},
            created_at=_now(),
            updated_at=_now(),
            tags=[],
            categories=[],
        )
    )
    await db.commit()
    return aid


async def test_card_shows_badge_for_flagged_owner(client, db):
    await _seed_flagged(db, FLAGGED)
    # Mixed-case upstream owner must match the lowercase mirror.
    await _seed_agent(db, 1, owner_address=FLAGGED.upper())
    body = client.get("/").text
    assert "flagged wallet" in body


async def test_card_no_badge_for_clean_owner(client, db):
    await _seed_agent(db, 1, owner_address="0x" + "cd" * 20)
    body = client.get("/").text
    assert "flagged wallet" not in body


async def test_detail_shows_badges_for_flagged_creator_and_payment_wallet(client, db):
    await _seed_flagged(db, FLAGGED)
    await _seed_flagged(db, "0x" + "dd" * 20, source="ofac-eth")
    await _seed_agent(
        db,
        1,
        owner_address="0x" + "DD" * 20,
        agent_wallet=FLAGGED,
        x402_supported=True,
    )
    body = client.get("/agents/56/1").text
    assert body.count("flagged wallet") == 2


async def test_detail_no_badges_for_clean_agent(client, db):
    await _seed_agent(
        db,
        1,
        owner_address="0x" + "cd" * 20,
        agent_wallet="0x" + "ef" * 20,
        x402_supported=True,
    )
    body = client.get("/agents/56/1").text
    assert "flagged wallet" not in body


# ---------------------------------------------------------------------------
# GET /flagged — public page
# ---------------------------------------------------------------------------


async def test_flagged_page_lists_addresses_and_sources(client, db):
    await _seed_flagged(db, "0x" + "aa" * 20, source="ofac-bsc")
    await _seed_flagged(db, "0x" + "bb" * 20, source="ofac-eth")
    body = client.get("/flagged").text
    assert "Flagged wallets" in body
    assert "0x" + "aa" * 20 in body
    assert "0x" + "bb" * 20 in body
    assert "OFAC BSC" in body and "OFAC ETH" in body
    assert "ofac-sanctioned-digital-currency-addresses" in body
    assert 'href="/flagged"' in body  # nav link


async def test_flagged_page_empty_state(client, db):
    body = client.get("/flagged").text
    assert "No flagged wallets" in body


async def test_flagged_page_is_public(client, db):
    response = client.get("/flagged")
    assert response.status_code == 200
    assert "<html" in response.text