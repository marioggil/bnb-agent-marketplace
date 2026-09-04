"""Individual agent feedback sync (agent-feedbacks).

Mirrors the 8004scan `/api/v1/feedbacks` endpoint — the plain v1 namespace,
NOT `/api/v1/public` (verified against live traffic) — into the local
`agent_feedbacks` table, one row per upstream `feedback_id` (e.g.
"56:137:0x…:1"). The endpoint is paginated with `limit`/`offset` (no API
key: ~180 req/min, 20,000/day) and returns
`{"items": [...], "total": N, "limit": L, "offset": O}`.

Idempotent via ON CONFLICT (feedback_id) DO UPDATE, so re-runs converge on
the latest snapshot instead of duplicating rows. The upsert is
dialect-aware (pg_insert on Postgres, sqlite_insert on the test harness)
with the same shape as `sync_worker._upsert_agent` / `routers.favorites`.

The caller owns the transaction: `sync_agent_feedbacks` executes statements
but never commits, so the sync worker batches feedback rows into its own
commit cycle (a feedback failure mid-batch rolls back with the agent row).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.models.agent_feedback import AgentFeedback

logger = logging.getLogger(__name__)

#: Upstream endpoint for individual agent feedbacks (plain v1, not /public).
FEEDBACKS_API: str = "https://8004scan.io/api/v1/feedbacks"
#: Page size for the paginated walk (offset steps by this).
_PAGE_LIMIT: int = 100
#: Per-request timeout (connect/read/write/pool).
_TIMEOUT: httpx.Timeout = httpx.Timeout(15.0)
#: Retries on 429/5xx/transport errors beyond the first attempt.
_MAX_RETRIES: int = 2
#: Statuses treated as transient (retried with backoff).
_RETRYABLE_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})
#: Base backoff between retries (grows linearly: 1s, 2s). Tests monkeypatch
#: this to 0 to keep the suite fast.
_RETRY_BACKOFF_S: float = 1.0

#: Columns rewritten on conflict. Everything except the PK (feedback_id) is
#: refreshed from the latest upstream snapshot; updated_at is stamped by the
#: database clock (portable: `now()` on Postgres, `CURRENT_TIMESTAMP` on
#: sqlite).
_SET_COLS: tuple[str, ...] = (
    "agent_id",
    "chain_id",
    "token_id",
    "user_address",
    "score",
    "comment",
    "tag1",
    "tag2",
    "tx_hash",
    "block_number",
    "submitted_at",
    "is_revoked",
)


def _normalize_item(
    item: dict[str, Any], agent_id: str, chain_id: int, token_id: int
) -> dict[str, Any]:
    """Map one upstream feedback payload to an agent_feedbacks row dict.

    `score` is `int(value)` when `value_decimals == 0` and None otherwise —
    a decimal score cannot map to an int losslessly. `submitted_at` is
    parsed as a timezone-aware UTC datetime (the upstream emits ISO 8601
    with a Z suffix).
    """
    value = item.get("value")
    value_decimals = item.get("value_decimals")
    try:
        score = int(value) if value is not None and str(value_decimals) == "0" else None
    except (TypeError, ValueError):
        score = None

    submitted_at: datetime | None = None
    raw_submitted = item.get("submitted_at")
    if raw_submitted:
        try:
            parsed = datetime.fromisoformat(str(raw_submitted).replace("Z", "+00:00"))
            submitted_at = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            submitted_at = None

    user = item.get("user")
    user_address = item.get("user_address")
    if not user_address and isinstance(user, dict):
        user_address = user.get("address")

    return {
        "feedback_id": item["feedback_id"],
        "agent_id": agent_id,
        "chain_id": chain_id,
        "token_id": token_id,
        "user_address": user_address,
        "score": score,
        "comment": item.get("comment"),
        "tag1": item.get("tag1"),
        "tag2": item.get("tag2"),
        "tx_hash": item.get("transaction_hash"),
        "block_number": item.get("block_number"),
        "submitted_at": submitted_at,
        "is_revoked": bool(item.get("is_revoked")),
    }


def _unwrap_feedbacks(data: Any) -> tuple[list[Any], int]:
    """Extract (items, total) from the response, accepting the documented
    `{"items": [...], "total": N}` shape, the historical `{"success", "data"}`
    envelope, and a bare list."""
    if isinstance(data, list):
        return data, len(data)
    if isinstance(data, dict):
        inner = data.get("data")
        if isinstance(inner, dict) and isinstance(inner.get("items"), list):
            return inner["items"], int(inner.get("total") or 0)
        if isinstance(data.get("items"), list):
            return data["items"], int(data.get("total") or 0)
    return [], 0


async def _fetch_page(
    client: httpx.AsyncClient, chain_id: int, token_id: int, offset: int
) -> tuple[list[Any], int]:
    """GET one page of feedbacks; returns (items, total).

    404 is treated as "no feedbacks" (upstream gap or agent without
    reviews): `([], 0)`. 429/5xx/transport errors retry up to
    `_MAX_RETRIES` times with linear backoff, then raise the last error —
    callers decide whether to fail or degrade (the sync worker degrades).
    """
    params = {
        "chain_id": int(chain_id),
        "agent_token_id": int(token_id),
        "limit": _PAGE_LIMIT,
        "offset": int(offset),
        "sort_by": "submitted_at",
        "sort_order": "desc",
        "is_testnet": "false",
    }
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = await client.get(FEEDBACKS_API, params=params)
        except httpx.HTTPError as exc:
            last_exc = exc
        else:
            if resp.status_code == 404:
                return [], 0
            if resp.status_code in _RETRYABLE_STATUS:
                last_exc = httpx.HTTPStatusError(
                    f"feedback sync HTTP {resp.status_code}", request=resp.request, response=resp
                )
            else:
                resp.raise_for_status()
                return _unwrap_feedbacks(resp.json())
        if attempt < _MAX_RETRIES:
            await asyncio.sleep(_RETRY_BACKOFF_S * (attempt + 1))
    assert last_exc is not None
    raise last_exc


async def _upsert_feedback(session: Any, row: dict[str, Any]) -> None:
    """Idempotent ON CONFLICT (feedback_id) DO UPDATE upsert.

    Dialect-aware like `routers.favorites.add_favorite`: `pg_insert` on
    Postgres, `sqlite_insert` on the aiosqlite test harness. `updated_at`
    is stamped with the database clock via `func.now()` (portable).
    """
    dialect = session.get_bind().dialect.name
    insert_fn = pg_insert if dialect == "postgresql" else sqlite_insert
    stmt = insert_fn(AgentFeedback).values(**row)
    stmt = stmt.on_conflict_do_update(
        index_elements=[AgentFeedback.feedback_id],
        set_={name: getattr(stmt.excluded, name) for name in _SET_COLS}
        | {"updated_at": func.now()},
    )
    await session.execute(stmt)


async def sync_agent_feedbacks(
    session: Any,
    agent_id: str,
    chain_id: int,
    token_id: int,
    *,
    max_pages: int = 20,
) -> int:
    """Walk the paginated /feedbacks endpoint and upsert every review.

    Stops when a page returns 0 items, when `offset >= total`, or after
    `max_pages` (a hard cap: 100 per page × 20 = 2000 reviews per agent).
    Returns the number of upserted rows. Does NOT commit — the caller owns
    the transaction (the sync worker batches feedbacks into its commit
    cycle; direct callers commit themselves).
    """
    upserted = 0
    offset = 0
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for _page in range(max_pages):
            items, total = await _fetch_page(client, chain_id, token_id, offset)
            if not items:
                break
            for item in items:
                if isinstance(item, dict) and item.get("feedback_id"):
                    await _upsert_feedback(
                        session, _normalize_item(item, agent_id, chain_id, token_id)
                    )
            upserted += len(items)
            offset += _PAGE_LIMIT
            if total and offset >= total:
                break
    return upserted


__all__ = ["FEEDBACKS_API", "sync_agent_feedbacks"]
