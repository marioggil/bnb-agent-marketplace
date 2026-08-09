"""Idempotent sync worker for AgentCache.

Walks BSC `token_id` and upserts into `agent_cache` via
`ON CONFLICT (agent_id) DO UPDATE`. Checkpoints progress in the singleton
`sync_state` row so a kill mid-walk resumes cleanly (spec #23 S7).

See `sdd/marketplace-scaffold/spec/sync-worker` (#23) for the requirements
and scenarios. Design decisions D3 (generated category + post-pass) and D7
(FIFO cap 1000 for failed_token_ids) are enforced here.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.agent import (
    AgentCache,
    BSC_CHAIN_ID,
    BSC_IDENTITY_REGISTRY,
    build_agent_id,
)
from app.db.models.sync_state import FAILED_TOKEN_IDS_CAP, SyncState
from app.services.categories import canonical_agent_id, compute_category
from app.services.client_8004scan import (
    Client8004Scan,
    UpstreamError,
    UpstreamUnavailable,
)

logger = logging.getLogger(__name__)

#: Default batch size for incremental runs (spec R1).
DEFAULT_INCREMENTAL_BATCH: int = 100
#: Default batch size for full runs. Larger because we expect a longer,
#: lower-priority window (Dokploy cron Sunday 03:00 UTC).
DEFAULT_FULL_BATCH: int = 200
#: How often to log a progress line (records walked).
PROGRESS_EVERY: int = 100


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
        state = SyncState(id=1, last_token_id=-1)  # type: ignore[call-arg]
        session.add(state)
        await session.flush()
    return state


async def _record_failure(
    session: AsyncSession, state: SyncState, token_id: int
) -> None:
    """Append a token_id to the jsonb failure list, capped at FAILED_TOKEN_IDS_CAP (D7).

    Order is chronological (oldest first, newest last); trimming `[-CAP:]` drops
    the oldest entries when the cap is exceeded. Fix for sdd-verify W1.
    """
    state.failed_token_ids = await session.scalar(
        text(
            "SELECT COALESCE(sync_state.failed_token_ids, '[]'::jsonb) || jsonb_build_array(:token_id) "
            "FROM sync_state WHERE id = 1"
        ).bindparams(token_id=token_id)
    ) or [token_id]
    # Trim FIFO in Python — `len(...) > cap` drops the oldest entries.
    if len(state.failed_token_ids) > FAILED_TOKEN_IDS_CAP:
        state.failed_token_ids = state.failed_token_ids[-FAILED_TOKEN_IDS_CAP:]


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------


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
        "agent_id": agent.agent_id or canonical_agent_id(agent.chain_id or BSC_CHAIN_ID, agent.token_id or 0),
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


async def _upsert_agent(
    session: AsyncSession, row: dict[str, Any]
) -> None:
    """Idempotent ON CONFLICT upsert keyed on agent_id.

    Touches every column the 8004scan detail endpoint can populate so
    re-running the seed or sync converges on the latest snapshot.
    """
    stmt = pg_insert(AgentCache).values(**row)
    excluded = stmt.excluded
    stmt = stmt.on_conflict_do_update(
        index_elements=[AgentCache.agent_id],
        set_={
            # -- identity --
            "agent_internal_id": excluded.agent_internal_id,
            "chain_id": excluded.chain_id,
            "chain_type": excluded.chain_type,
            "token_id": excluded.token_id,
            "contract_address": excluded.contract_address,
            "registry_address": excluded.registry_address,
            "is_testnet": excluded.is_testnet,
            # -- owner --
            "owner_id": excluded.owner_id,
            "owner_address": excluded.owner_address,
            "owner_ens": excluded.owner_ens,
            "owner_username": excluded.owner_username,
            "owner_avatar_url": excluded.owner_avatar_url,
            "owner_publisher_tier": excluded.owner_publisher_tier,
            "owner_certified_name": excluded.owner_certified_name,
            "creator_address": excluded.creator_address,
            # -- presentation --
            "name": excluded.name,
            "description": excluded.description,
            "agent_type": excluded.agent_type,
            "image_url": excluded.image_url,
            "agent_wallet": excluded.agent_wallet,
            "is_verified": excluded.is_verified,
            "star_count": excluded.star_count,
            "watch_count": excluded.watch_count,
            "tags": excluded.tags,
            "categories": excluded.categories,
            # -- service endpoints --
            "services": excluded.services,
            # -- protocols / payments --
            "x402_supported": excluded.x402_supported,
            "supported_protocols": excluded.supported_protocols,
            "supported_trust_models": excluded.supported_trust_models,
            # -- scores --
            "average_score": excluded.average_score,
            "total_score": excluded.total_score,
            "total_feedbacks": excluded.total_feedbacks,
            "total_validations": excluded.total_validations,
            "successful_validations": excluded.successful_validations,
            "rank": excluded.rank,
            "network_rank": excluded.network_rank,
            "scores": excluded.scores,
            # -- cross-chain --
            "cross_chain_links": excluded.cross_chain_links,
            "cross_chain_versions": excluded.cross_chain_versions,
            # -- on-chain provenance --
            "created_block_number": excluded.created_block_number,
            "created_tx_hash": excluded.created_tx_hash,
            # -- endpoint health --
            "is_active": excluded.is_active,
            "is_endpoint_verified": excluded.is_endpoint_verified,
            "endpoint_verified_at": excluded.endpoint_verified_at,
            "endpoint_verified_domain": excluded.endpoint_verified_domain,
            "endpoint_verification_error": excluded.endpoint_verification_error,
            "endpoint_last_checked_at": excluded.endpoint_last_checked_at,
            "health_status": excluded.health_status,
            "health_score": excluded.health_score,
            "health_checked_at": excluded.health_checked_at,
            # -- quality scores --
            "quality_score": excluded.quality_score,
            "popularity_score": excluded.popularity_score,
            "activity_score": excluded.activity_score,
            "wallet_score": excluded.wallet_score,
            "freshness_score": excluded.freshness_score,
            "metadata_completeness_score": excluded.metadata_completeness_score,
            # -- supplementary identity --
            "ens": excluded.ens,
            "did": excluded.did,
            "mcp_server": excluded.mcp_server,
            "mcp_version": excluded.mcp_version,
            "a2a_endpoint": excluded.a2a_endpoint,
            "a2a_version": excluded.a2a_version,
            "agent_url": excluded.agent_url,
            # -- parse / metadata --
            "parse_status": excluded.parse_status,
            "raw_metadata": excluded.raw_metadata,
            # -- upstream timestamps --
            "upstream_created_at": excluded.upstream_created_at,
            "upstream_updated_at": excluded.upstream_updated_at,
            # -- catch-all --
            "raw": excluded.raw,
            "updated_at": text("now()"),
        },
    )
    await session.execute(stmt)


async def _maybe_enrich_category(
    session: AsyncSession, agent: Any, agent_id: str
) -> None:
    """If the rich mapping differs from the GENERATED default, UPDATE the row.

    Spec: the worker post-pass refines `category` for rows with oasf skills
    or sub-protocols (design D3).
    """
    rich = compute_category(agent.supported_protocols or [], agent.x402_supported)
    # If the rich mapping matches the GENERATED default, skip the UPDATE.
    if rich in {"rebalancing", "other"} and (
        agent.x402_supported or "oasf" in (agent.supported_protocols or [])
    ):
        return
    if rich in {"rebalancing", "other"}:
        return
    await session.execute(
        text("UPDATE agent_cache SET category = :cat WHERE agent_id = :aid"),
        {"cat": rich, "aid": agent_id},
    )


# ---------------------------------------------------------------------------
# Walk
# ---------------------------------------------------------------------------


async def _walk(
    session: AsyncSession,
    client: Client8004Scan,
    start: int,
    end: int,
) -> tuple[int, int, int, int, int]:
    """Walk token_ids in [start, end). Returns (last_seen, upserted, skipped, failed, last_token_id)."""
    upserted = 0
    skipped = 0
    failed = 0
    last_seen = start - 1

    for token_id in range(start, end):
        last_seen = token_id
        try:
            agent = await client.get_agent(BSC_CHAIN_ID, token_id)
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
            skipped += 1
            await _record_failure(session, await _ensure_sync_state(session), token_id)
            continue
        # Chain filter (defense in depth — client already filters).
        if agent.chain_id is not None and int(agent.chain_id) != BSC_CHAIN_ID:
            skipped += 1
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

        if token_id % PROGRESS_EVERY == 0:
            await session.commit()
            logger.info("sync: walked=%s upserted=%s skipped=%s failed=%s", token_id, upserted, skipped, failed)

    await session.commit()
    return last_seen, upserted, skipped, failed, end - 1


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


async def sync_incremental(batch: int = DEFAULT_INCREMENTAL_BATCH) -> SyncReport:
    """Resume from `last_token_id + 1` and walk `batch` more records."""
    return await _run_sync(start_offset=1, batch=batch, label="incremental")


async def sync_full(batch: int = DEFAULT_FULL_BATCH) -> SyncReport:
    """Re-walk from token_id 0. Idempotent via ON CONFLICT."""
    return await _run_sync(start_offset=0, batch=batch, label="full")


async def _run_sync(start_offset: int, batch: int, label: str) -> SyncReport:
    from app.db.session import AsyncSessionLocal

    started = time.monotonic()
    logger.info("sync %s: start_offset=%s batch=%s", label, start_offset, batch)

    async with AsyncSessionLocal() as session, Client8004Scan() as client:
        state = await _ensure_sync_state(session)
        if start_offset == 0:
            start = 0
        else:
            start = int(state.last_token_id) + 1
        end = start + batch

        last_seen, upserted, skipped, failed, last_id = await _walk(
            session, client, start, end
        )

        # Update checkpoint + last_sync_at.
        state = await _ensure_sync_state(session)
        state.last_token_id = last_id
        state.last_sync_at = datetime.now(tz=timezone.utc)
        await session.commit()

    duration = time.monotonic() - started
    report = SyncReport(
        walked=max(0, last_seen - start + 1),
        upserted=upserted,
        skipped=skipped,
        failed=failed,
        last_token_id=last_id,
        duration_s=duration,
    )
    logger.info("sync %s: %s", label, report)
    return report


# Re-export for the CLI.
__all__ = [
    "Client8004Scan",
    "SyncReport",
    "sync_full",
    "sync_incremental",
]
