"""Idempotent sync worker for AgentCache.

Two-phase sync that mirrors what the seed script does:

  1. **Discovery** — paginated `iter_agents` listing (200 per page,
     client-side BSC filter). Cheap: 1 HTTP request per page.
  2. **Enrichment** — per-token `get_agent` detail request, upsert
     into `agent_cache` via `ON CONFLICT (agent_id) DO UPDATE`.
     Heavier: 1 request per agent, captures the full ~50 column
     payload (services, raw_metadata, parse_status, quality scores,
     on-chain provenance, endpoint health).

Idempotent via the ON CONFLICT path; checkpoint progress in the
singleton `sync_state.last_token_id` row so a kill mid-walk resumes
cleanly on the next run (spec #23 S7). The checkpoint is informational
under the new flow — discovery is upstream-order, not token-order —
but the column stays for back-compat with anyone watching it.

See `sdd/marketplace-scaffold/spec/sync-worker` (#23) for the
requirements and scenarios. Design decisions D3 (generated category
+ post-pass) and D7 (FIFO cap 1000 for failed_token_ids) are enforced
here.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.agent import (
    BSC_CHAIN_ID,
    BSC_IDENTITY_REGISTRY,
    AgentCache,
    build_agent_id,
)
from app.db.models.sync_state import FAILED_TOKEN_IDS_CAP, SyncState
from app.services.categories import canonical_agent_id, compute_category
from app.services.client_8004scan import (
    Client8004Scan,
    UpstreamError,
    UpstreamRateLimit,
    UpstreamUnavailable,
)

logger = logging.getLogger(__name__)

#: Default batch size for incremental runs (spec R1). Limits how many
#: agents the worker enriches per run; full coverage still happens
#: over many runs.
DEFAULT_INCREMENTAL_BATCH: int = 100
#: Default batch size for full runs. Larger because we expect a longer,
#: lower-priority window (Dokploy cron Sunday 03:00 UTC).
DEFAULT_FULL_BATCH: int = 200
#: How often to log a progress line (records walked).
PROGRESS_EVERY: int = 100
#: Free tier is 50 rpm; sleep this long between per-agent detail
#: requests to stay under the limit. Pass 0 to disable (only safe with
#: a Pro API key).
DEFAULT_DETAIL_SLEEP_S: float = 1.5
#: Wait for the rate bucket to reset when a detail request hits a 429
#: that outlives the client's tenacity budget.
_ENRICH_RATE_LIMIT_BACKOFF_S: float = 60.0


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SyncReport:
    """Outcome of one sync run."""

    walked: int
    upserted: int
    skipped: int
    failed: int
    last_token_id: int
    duration_s: float

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return (
            f"SyncReport(walked={self.walked} upserted={self.upserted} "
            f"skipped={self.skipped} failed={self.failed} "
            f"last_token_id={self.last_token_id} duration_s={self.duration_s:.2f})"
        )


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------


async def _ensure_sync_state(session: AsyncSession) -> SyncState:
    """Return the singleton row, creating it if absent."""
    state = await session.get(SyncState, 1)
    if state is None:
        state = SyncState(id=1, last_token_id=-1)
        session.add(state)
        await session.flush()
    return state


async def _record_failure(session: AsyncSession, state: SyncState, token_id: int) -> None:
    """Append a token_id to the jsonb failure list, capped at FAILED_TOKEN_IDS_CAP (D7).

    Order is chronological (oldest first, newest last); trimming `[-CAP:]` drops
    the oldest entries when the cap is exceeded. Fix for sdd-verify W1.
    """
    state.failed_token_ids = await session.scalar(
        text(
            "SELECT COALESCE(sync_state.failed_token_ids, '[]'::jsonb) "
            "|| jsonb_build_array(:token_id) "
            "FROM sync_state WHERE id = 1"
        ).bindparams(token_id=token_id)
    ) or [token_id]
    # Trim FIFO in Python — `len(...) > cap` drops the oldest entries.
    if len(state.failed_token_ids) > FAILED_TOKEN_IDS_CAP:
        state.failed_token_ids = state.failed_token_ids[-FAILED_TOKEN_IDS_CAP:]


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------

#: Column names the sync mirrors into `agent_cache` on conflict, in
#: declaration order. Locally-owned columns (`_LOCAL_OWNED`) are filtered out
#: at runtime via `_build_set_exprs` — the probe worker and the /score API own
#: them and sync must not clobber their values (spec agent-score P7, D4).
_SET_EXPRS: tuple[str, ...] = (
    "agent_internal_id",
    "chain_id",
    "chain_type",
    "token_id",
    "contract_address",
    "registry_address",
    "is_testnet",
    "owner_id",
    "owner_address",
    "owner_ens",
    "owner_username",
    "owner_avatar_url",
    "owner_publisher_tier",
    "owner_certified_name",
    "creator_address",
    "name",
    "description",
    "agent_type",
    "image_url",
    "agent_wallet",
    "is_verified",
    "star_count",
    "watch_count",
    "tags",
    "categories",
    "services",
    "x402_supported",
    "supported_protocols",
    "supported_trust_models",
    "average_score",
    "total_score",
    "total_feedbacks",
    "total_validations",
    "successful_validations",
    "rank",
    "network_rank",
    "scores",
    "cross_chain_links",
    "cross_chain_versions",
    "created_block_number",
    "created_tx_hash",
    "is_active",
    "is_endpoint_verified",
    "endpoint_verified_at",
    "endpoint_verified_domain",
    "endpoint_verification_error",
    "endpoint_last_checked_at",
    "health_status",
    "health_score",
    "health_checked_at",
    "quality_score",
    "popularity_score",
    "activity_score",
    "wallet_score",
    "freshness_score",
    "metadata_completeness_score",
    "ens",
    "did",
    "mcp_server",
    "mcp_version",
    "a2a_endpoint",
    "a2a_version",
    "agent_url",
    "parse_status",
    "raw_metadata",
    "upstream_created_at",
    "upstream_updated_at",
    "raw",
)

#: Locally-owned `agent_cache` columns the sync MUST NOT overwrite (P7, D4).
#: The A2A probe worker and the /score API write these.
_LOCAL_OWNED: frozenset[str] = frozenset(
    {
        "health_status",
        "health_score",
        "health_checked_at",
        "endpoint_last_checked_at",
        "endpoint_verification_error",
        "endpoint_verified_domain",
        "is_endpoint_verified",
        "endpoint_verified_at",
        "activity_score",
        "scores",
    }
)


def _build_set_exprs(excluded: Any) -> dict[str, Any]:
    """Build the ON CONFLICT `set_` mapping (P7, D4).

    Every `_SET_EXPRS` column except the `_LOCAL_OWNED` set is written from
    the conflict row. `updated_at` is stamped with the database clock via
    `func.now()` (portable: `now()` on Postgres, `CURRENT_TIMESTAMP` on
    sqlite). The INSERT `row` still carries all 10 locally-owned columns so
    the conflict-target side is untouched.
    """
    exprs = {name: getattr(excluded, name) for name in _SET_EXPRS if name not in _LOCAL_OWNED}
    exprs["updated_at"] = func.now()
    return exprs


def _row_from_agent(agent: Any, category_override: str) -> dict[str, Any]:
    """Map an `AgentResponse` to a dict compatible with the agent_cache columns.

    The AgentResponse now carries the full 8004scan detail payload
    (added in commit 0b5d34c's client_8004scan expansion): ~50 fields
    including services, raw_metadata, on-chain provenance, quality
    scores and endpoint health. We map everything to its corresponding
    column; unmodelled upstream fields continue to land in `raw`.
    """
    return {
        # -- identity --
        "agent_id": agent.agent_id
        or canonical_agent_id(agent.chain_id or BSC_CHAIN_ID, agent.token_id or 0),
        "agent_internal_id": agent.id,
        "chain_id": int(agent.chain_id) if agent.chain_id is not None else BSC_CHAIN_ID,
        "chain_type": agent.chain_type,
        "token_id": int(agent.token_id) if agent.token_id is not None else 0,
        "contract_address": agent.contract_address or BSC_IDENTITY_REGISTRY,
        "registry_address": BSC_IDENTITY_REGISTRY,
        "is_testnet": bool(agent.is_testnet),
        # -- owner --
        "owner_id": agent.owner_id,
        "owner_address": agent.owner_address,
        "owner_ens": agent.owner_ens,
        "owner_username": agent.owner_username,
        "owner_avatar_url": agent.owner_avatar_url,
        "owner_publisher_tier": agent.owner_publisher_tier,
        "owner_certified_name": agent.owner_certified_name,
        "creator_address": agent.creator_address,
        # -- presentation --
        "name": agent.name,
        "description": agent.description,
        "agent_type": agent.agent_type,
        "image_url": agent.image_url,
        "agent_wallet": agent.agent_wallet,
        "is_verified": bool(agent.is_verified),
        "star_count": int(agent.star_count or 0),
        "watch_count": int(agent.watch_count or 0),
        "tags": list(agent.tags or []),
        "categories": list(agent.categories or []),
        # -- service endpoints --
        "services": dict(agent.services or {}),
        # -- protocols / payments --
        "x402_supported": bool(agent.x402_supported),
        "supported_protocols": list(agent.supported_protocols or []),
        "supported_trust_models": list(agent.supported_trust_models or []),
        # -- scores --
        "average_score": agent.average_score,
        "total_score": agent.total_score,
        "total_feedbacks": int(agent.total_feedbacks or 0),
        "total_validations": int(agent.total_validations or 0),
        "successful_validations": int(agent.successful_validations or 0),
        "rank": agent.rank,
        "network_rank": agent.network_rank,
        "scores": agent.scores,
        # -- cross-chain --
        "cross_chain_links": list(agent.cross_chain_links or []),
        "cross_chain_versions": list(agent.cross_chain_versions or []),
        # -- on-chain provenance --
        "created_block_number": agent.created_block_number,
        "created_tx_hash": agent.created_tx_hash,
        # -- endpoint health --
        "is_active": bool(agent.is_active),
        "is_endpoint_verified": bool(agent.is_endpoint_verified),
        "endpoint_verified_at": agent.endpoint_verified_at,
        "endpoint_verified_domain": agent.endpoint_verified_domain,
        "endpoint_verification_error": agent.endpoint_verification_error,
        "endpoint_last_checked_at": agent.endpoint_last_checked_at,
        "health_status": agent.health_status,
        "health_score": agent.health_score,
        "health_checked_at": agent.health_checked_at,
        # -- quality scores --
        "quality_score": agent.quality_score,
        "popularity_score": agent.popularity_score,
        "activity_score": agent.activity_score,
        "wallet_score": agent.wallet_score,
        "freshness_score": agent.freshness_score,
        "metadata_completeness_score": agent.metadata_completeness_score,
        # -- supplementary identity --
        "ens": agent.ens,
        "did": agent.did,
        "mcp_server": agent.mcp_server,
        "mcp_version": agent.mcp_version,
        "a2a_endpoint": agent.a2a_endpoint,
        "a2a_version": agent.a2a_version,
        "agent_url": agent.agent_url,
        # -- parse / metadata --
        "parse_status": agent.parse_status,
        "raw_metadata": agent.raw_metadata,
        # -- upstream timestamps --
        "upstream_created_at": agent.created_at,
        "upstream_updated_at": agent.updated_at,
        # -- catch-all (unmodelled upstream fields) --
        "raw": dict(agent.raw or {}),
    }


async def _upsert_agent(session: AsyncSession, row: dict[str, Any]) -> None:
    """Idempotent ON CONFLICT upsert keyed on agent_id.

    Touches every column the 8004scan detail endpoint can populate so
    re-running the seed or sync converges on the latest snapshot, EXCEPT the
    locally-owned columns (`_LOCAL_OWNED`) that the probe worker and the
    /score API write (spec agent-score P7, design D4).
    """
    stmt = pg_insert(AgentCache).values(**row)
    stmt = stmt.on_conflict_do_update(
        index_elements=[AgentCache.agent_id],
        set_=_build_set_exprs(stmt.excluded),
    )
    await session.execute(stmt)


async def _maybe_enrich_category(session: AsyncSession, agent: Any, agent_id: str) -> None:
    """If the rich mapping differs from the GENERATED default, UPDATE the row.

    Spec: `sdd/doc-refresh/spec` TAX-3. The classifier now receives the
    full signal set: the source-side termix category and the offchain tags
    (study §4/§5), plus `supported_protocols` and `x402_supported` (design
    D1/D2). Rich metadata is read from `raw_metadata.offchain_content`,
    falling back to the listing `tags` when the offchain content has none.
    """
    off = (agent.raw_metadata or {}).get("offchain_content") or {}
    if not isinstance(off, dict):
        off = {}
    termix = off.get("termix") or {}
    if not isinstance(termix, dict):
        termix = {}
    profile = termix.get("profile") or {}
    if not isinstance(profile, dict):
        profile = {}
    termix_category = profile.get("category")
    if not isinstance(termix_category, str):
        termix_category = None
    off_tags = off.get("tags")
    tags = off_tags if isinstance(off_tags, list) else (agent.tags or [])
    rich = compute_category(
        termix_category,
        tags,
        agent.supported_protocols or [],
        agent.x402_supported,
    )
    # If the rich mapping matches the GENERATED default (rebalancing for
    # x402/oasf rows, other otherwise), skip the UPDATE (design D3).
    if rich in {"rebalancing", "other"}:
        return
    await session.execute(
        text("UPDATE agent_cache SET category = :cat WHERE agent_id = :aid"),
        {"cat": rich, "aid": agent_id},
    )


# ---------------------------------------------------------------------------
# Two-phase sync
# ---------------------------------------------------------------------------


async def _discover_bsc_token_ids(
    client: Client8004Scan, limit: int, page_size: int, page_delay: float
) -> tuple[list[int], int, int]:
    """Phase 1: walk the paginated listing to collect BSC token_ids.

    Returns (token_ids, fetched_total, skipped_wrong_chain).
    The 8004scan /agents endpoint isn't strict about server-side
    `chain_id` filtering, so we filter client-side (BSC = 56).
    `page_delay` paces the listing requests (free tier is 50 rpm).
    """
    token_ids: list[int] = []
    fetched = 0
    skipped_wrong_chain = 0
    async for agent in client.iter_agents(
        chain_id=BSC_CHAIN_ID, page_size=page_size, page_delay=page_delay
    ):
        fetched += 1
        if agent.chain_id is not None and int(agent.chain_id) != BSC_CHAIN_ID:
            skipped_wrong_chain += 1
            continue
        if agent.token_id is None:
            continue
        token_ids.append(int(agent.token_id))
        if len(token_ids) >= limit:
            break
    return token_ids, fetched, skipped_wrong_chain


async def _enrich_and_upsert(
    session: AsyncSession,
    client: Client8004Scan,
    token_ids: list[int],
    detail_sleep_s: float,
) -> tuple[int, int, int, int]:
    """Phase 2: per-token get_agent + upsert.

    Returns (upserted, failed, last_token_id, walked). Sleeps
    `detail_sleep_s` between requests to stay under the free
    tier 50 rpm rate limit.
    """
    upserted = 0
    failed = 0
    last_token_id = -1

    for idx, token_id in enumerate(token_ids, start=1):
        try:
            agent = await client.get_agent(BSC_CHAIN_ID, token_id)
        except UpstreamRateLimit:
            # The whole batch will keep hitting 429; wait for the bucket
            # to reset before the next agent instead of burning strikes.
            logger.warning(
                "rate limited at token_id=%s; waiting %ss before continuing",
                token_id,
                _ENRICH_RATE_LIMIT_BACKOFF_S,
            )
            await asyncio.sleep(_ENRICH_RATE_LIMIT_BACKOFF_S)
            failed += 1
            await _record_failure(session, await _ensure_sync_state(session), token_id)
            continue
        except UpstreamUnavailable as exc:
            logger.error("Upstream unavailable at token_id=%s: %s", token_id, exc)
            failed += 1
            await _record_failure(session, await _ensure_sync_state(session), token_id)
            continue
        except UpstreamError as exc:
            logger.warning("Upstream error at token_id=%s: %s", token_id, exc)
            failed += 1
            await _record_failure(session, await _ensure_sync_state(session), token_id)
            continue
        if agent is None:
            # 404 — gap, skip but record.
            failed += 1
            await _record_failure(session, await _ensure_sync_state(session), token_id)
            continue
        if agent.chain_id is not None and int(agent.chain_id) != BSC_CHAIN_ID:
            # Defense in depth: the listing should have filtered, but
            # the token_id could in theory have been re-minted on a
            # different chain.
            continue

        # Force a consistent agent_id even if the upstream supplies one.
        if not agent.agent_id or not str(agent.agent_id).startswith(f"{BSC_CHAIN_ID}:"):
            agent.agent_id = build_agent_id(BSC_CHAIN_ID, BSC_IDENTITY_REGISTRY, token_id)
        elif ":" not in str(agent.agent_id):
            agent.agent_id = f"{BSC_CHAIN_ID}:{BSC_IDENTITY_REGISTRY}:{agent.agent_id}"

        row = _row_from_agent(agent, category_override="")
        await _upsert_agent(session, row)
        await _maybe_enrich_category(session, agent, row["agent_id"])
        upserted += 1
        last_token_id = max(last_token_id, token_id)

        if upserted % PROGRESS_EVERY == 0 or idx == len(token_ids):
            await session.commit()
            logger.info(
                "sync: progress=%s/%s upserted=%s failed=%s last_token_id=%s",
                idx,
                len(token_ids),
                upserted,
                failed,
                last_token_id,
            )

        # Stay under the 50 rpm free tier (1.2 s/request).
        if idx < len(token_ids) and detail_sleep_s > 0:
            await asyncio.sleep(detail_sleep_s)

    return upserted, failed, last_token_id, len(token_ids)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


async def sync_incremental(
    batch: int = DEFAULT_INCREMENTAL_BATCH,
    detail_sleep_s: float = DEFAULT_DETAIL_SLEEP_S,
) -> SyncReport:
    """Discover `batch` BSC agents via iter_agents, then enrich them
    via get_agent + upsert. Idempotent — safe to re-run any time."""
    return await _run_sync(batch=batch, label="incremental", detail_sleep_s=detail_sleep_s)


async def sync_full(
    batch: int = DEFAULT_FULL_BATCH,
    detail_sleep_s: float = DEFAULT_DETAIL_SLEEP_S,
) -> SyncReport:
    """Same as sync_incremental but with the full-batch default. Re-runs
    are no-ops on existing rows thanks to ON CONFLICT (agent_id) DO UPDATE."""
    return await _run_sync(batch=batch, label="full", detail_sleep_s=detail_sleep_s)


async def _run_sync(batch: int, label: str, detail_sleep_s: float) -> SyncReport:
    from app.db.session import AsyncSessionLocal

    started = time.monotonic()
    logger.info("sync %s: start batch=%s detail_sleep_s=%s", label, batch, detail_sleep_s)

    async with AsyncSessionLocal() as session, Client8004Scan() as client:
        token_ids, fetched, skipped_wrong_chain = await _discover_bsc_token_ids(
            client,
            batch,
            page_size=200,
            page_delay=detail_sleep_s,
        )
        logger.info(
            "sync %s: discover fetched=%s bsc_candidates=%s skipped_wrong_chain=%s",
            label,
            fetched,
            len(token_ids),
            skipped_wrong_chain,
        )

        upserted, failed, last_token_id, walked = await _enrich_and_upsert(
            session,
            client,
            token_ids,
            detail_sleep_s,
        )

        # Update checkpoint + last_sync_at. last_token_id is the max
        # token_id seen in this batch (informational under the new
        # upstream-order flow; the column stays for back-compat).
        state = await _ensure_sync_state(session)
        state.last_token_id = last_token_id
        state.last_sync_at = datetime.now(tz=timezone.utc)
        await session.commit()

    duration = time.monotonic() - started
    report = SyncReport(
        walked=walked,
        upserted=upserted,
        skipped=skipped_wrong_chain,
        failed=failed,
        last_token_id=last_token_id,
        duration_s=duration,
    )
    logger.info("sync %s: %s", label, report)
    return report


# Re-export for the CLI.
__all__ = [
    "Client8004Scan",
    "DEFAULT_DETAIL_SLEEP_S",
    "DEFAULT_FULL_BATCH",
    "DEFAULT_INCREMENTAL_BATCH",
    "SyncReport",
    "sync_full",
    "sync_incremental",
]
