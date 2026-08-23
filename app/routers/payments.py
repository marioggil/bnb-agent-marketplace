"""GET /api/agents/{chain_id}/{token_id}/payments — onchain $U payment history.

Trace: `CO1.BDOS.2063185 -> CO1.REQ.2121688` (docs/traceability.md).

Public read-only endpoint: the local `AgentCache` only supplies the recipient
wallet; the transfer list is read live from the configured BSC RPC
(`eth_getLogs` on the $U contract). Unlike `app/routers/agents.py` this router
DOES call an upstream — but never 8004scan, and only for agents cached
locally.

Decision: an agent without `agent_wallet` answers `200` with `payments: []`
and `wallet: null` (friendly for consumers) instead of 422 — there is nothing
wrong with the request, the agent simply has no payment surface yet.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import X402_CHAIN_MAINNET, get_settings
from app.db.models.agent import AgentCache
from app.db.session import get_db
from app.errors import NotFound
from app.services.agent_payments import fetch_agent_payments

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("/{chain_id}/{token_id}/payments")
async def get_agent_payments(
    chain_id: int,
    token_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(
        default=50,
        ge=1,
        le=200,
        description="Max payments to return (newest first).",
    ),
    from_block: int | None = Query(
        default=None,
        ge=0,
        description="Start block; default is latest - ~50000 (last ~24-48h).",
    ),
) -> dict[str, Any]:
    """$U transfers received by the agent's wallet, newest first (public)."""
    row = await db.scalar(
        select(AgentCache).where(AgentCache.chain_id == chain_id, AgentCache.token_id == token_id)
    )
    if row is None:
        raise NotFound(f"agent {chain_id}:{token_id} not cached")

    settings = get_settings()
    # Token address follows the AGENT's chain (path param), not the
    # settings' chain switch — a testnet agent keeps the testnet $U address.
    token_address = (
        settings.x402_u_token_address_56
        if chain_id == X402_CHAIN_MAINNET
        else settings.x402_u_token_address_97
    )
    if not row.agent_wallet:
        payments: list[dict[str, Any]] = []
    else:
        payments = await fetch_agent_payments(
            row.agent_wallet,
            token_address,
            settings.x402_rpc_url_resolved,
            chain_id,
            limit=limit,
            from_block=from_block,
        )
    return {
        "agent_id": row.agent_id,
        "chain_id": chain_id,
        "token_id": token_id,
        "wallet": row.agent_wallet,
        "token": token_address,
        "payments": payments,
    }


__all__ = ["router"]
