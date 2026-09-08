"""ERC-8183 escrow hire router (buyer-side, BSC mainnet).

Flow:
    1. POST /api/hires/escrow                -> create PENDING hire + return
       pre-armed calldata for approve/createJob/fund.
    2. POST /api/hires/escrow/{id}/submit    -> record the on-chain jobId
       once the browser's createJob tx has mined; flip status to FUNDED.
    3. GET  /api/hires/escrow/{id}           -> return the hire + (eventually)
       on-chain state.

The browser pays its own gas. The backend only builds calldata and records
audit data — it never holds custody of $U or signs txs.

Spec: docs/category-study.md §ERC-8183 (buyer-side).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models.agent import AgentCache
from app.db.models.hired_agent import HiredAgent, HiredStatus
from app.db.models.user import User
from app.db.session import get_db
from app.errors import (
    AgentOfferUnavailable,
    AlreadyPaid,
    Conflict,
    Forbidden,
    NotFound,
    UpstreamUnavailable,
)
from app.schemas.escrow import (
    HireEscrowCall,
    HireEscrowCreate,
    HireEscrowCreateOut,
    HireEscrowOut,
    HireEscrowSubmit,
)
from app.services.altana_jobs import (
    build_approve_calldata,
    build_create_job_calldata,
    build_fund_calldata,
)
from app.services.auth import get_current_user, require_csrf

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/hires", tags=["hires-escrow"])


def _require_escrow_enabled() -> None:
    """503 when the feature flag is off; lets the UI render a 'not available'
    state instead of silently accepting a hire that can't be processed."""
    if not get_settings().erc8183_enabled:
        raise UpstreamUnavailable("ERC-8183 escrow flow is disabled (ERC8183_ENABLED=false)")


async def _load_owned_hire(
    db: AsyncSession, hire_id: int, user: User
) -> HiredAgent:
    """Load a hire by id and verify the caller owns it."""
    hire = await db.get(HiredAgent, hire_id)
    if hire is None:
        raise NotFound(f"hire {hire_id} not found")
    if hire.address.lower() != user.address.lower():
        raise Forbidden("not your hire")
    return hire


# ---------------------------------------------------------------------------
# POST /api/hires/escrow
# ---------------------------------------------------------------------------


@router.post("/escrow", response_model=HireEscrowCreateOut, status_code=status.HTTP_201_CREATED)
async def create_escrow_hire(
    payload: HireEscrowCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _csrf: Annotated[None, Depends(require_csrf)] = None,
) -> HireEscrowCreateOut:
    """Create a PENDING ERC-8183 hire and return pre-armed calldata.

    The browser signs three transactions in order:
      1. `approve($U, commerce, budget)` — authorise the proxy to pull $U.
      2. `createJob(provider, router, expiredAt, task, router)` — opens the
         on-chain job, returns the jobId (browser reads from the receipt).
      3. `fund(jobId, budget)` — actually escrows the $U.
    The jobId is reported back via POST /escrow/{id}/submit so the DB row
    can link to the on-chain job.
    """
    _require_escrow_enabled()

    settings = get_settings()
    # Resolve agent — must exist + must have an agent_wallet to act as the
    # ERC-8183 provider. No wallet -> no escrow possible.
    agent = await db.scalar(
        select(AgentCache).where(AgentCache.agent_id == payload.agent_id)
    )
    if agent is None:
        raise NotFound(f"agent {payload.agent_id!r} not cached")
    if not agent.agent_wallet or int(agent.agent_wallet, 16) == 0:
        raise AgentOfferUnavailable(
            "agent has no payment wallet (agent_wallet) — cannot escrow"
        )

    provider = agent.agent_wallet
    evaluator = settings.erc8183_router_address  # router doubles as evaluator
    hook = settings.erc8183_router_address  # and as hook
    expired_at = (
        int(datetime.now(tz=timezone.utc).timestamp())
        + settings.erc8183_job_expiry_seconds
    )

    # 1. Persist a PENDING row first so the audit trail survives even if the
    # browser never sends the createJob tx. job_id and tx_hash stay NULL until
    # POST /escrow/{id}/submit.
    row = HiredAgent(
        address=user.address,
        agent_id=payload.agent_id,
        status=HiredStatus.PENDING,
        rail="erc8183",
        chain_id=settings.erc8183_chain_id,
        provider_address=provider,
        budget_wei=payload.budget_wei,
        # x402 payment fields stay NULL — they're only meaningful for the
        # eip3009 rail.
    )
    db.add(row)
    await db.flush()

    # 2. Build the three pre-armed calldata. The `fund` call references the
    # jobId that will be returned by createJob; the placeholder value `1`
    # works as long as the browser always extracts the real jobId from the
    # createJob receipt before signing the fund tx (the handler does this).
    create_job_data = build_create_job_calldata(
        provider=provider,
        evaluator=evaluator,
        expired_at=expired_at,
        description=payload.task,
        hook=hook,
    )
    approve_data = build_approve_calldata(
        spender=settings.erc8183_commerce_address,
        amount=payload.budget_wei,
    )
    # jobId is unknown at this point; placeholder 1 + comment in the data field.
    fund_data = build_fund_calldata(
        job_id=1,  # placeholder; the browser must rebuild this after createJob mines
        expected_budget=payload.budget_wei,
    )

    await db.commit()
    await db.refresh(row)

    return HireEscrowCreateOut(
        hire_id=row.id,
        status=row.status,
        chain_id=row.chain_id,
        provider_address=row.provider_address,
        commerce_address=settings.erc8183_commerce_address,
        u_token_address=settings.erc8183_u_token_address,
        router_address=settings.erc8183_router_address,
        policy_address=settings.erc8183_policy_address,
        approve=HireEscrowCall(
            to=settings.erc8183_u_token_address,
            data=approve_data,
            value="0x0",
            description="approve",
        ),
        create_job=HireEscrowCall(
            to=settings.erc8183_commerce_address,
            data=create_job_data,
            value="0x0",
            description="createJob",
        ),
        fund=HireEscrowCall(
            to=settings.erc8183_commerce_address,
            data=fund_data,
            value="0x0",
            description="fund",
        ),
        job_expiry_unix=expired_at,
    )


# ---------------------------------------------------------------------------
# POST /api/hires/escrow/{id}/submit
# ---------------------------------------------------------------------------


@router.post("/escrow/{hire_id}/submit", response_model=HireEscrowOut)
async def submit_escrow_hire(
    hire_id: int,
    payload: HireEscrowSubmit,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _csrf: Annotated[None, Depends(require_csrf)] = None,
) -> HireEscrowOut:
    """Record the on-chain jobId + createJob txHash for the hire.

    Idempotent on (hire_id, job_id) — re-submitting the same jobId is a
    no-op. Submitting a *different* jobId for an already-recorded hire
    answers 409 to surface accidental overwrites.
    """
    _require_escrow_enabled()

    hire = await _load_owned_hire(db, hire_id, user)
    if hire.rail != "erc8183":
        raise Conflict(f"hire {hire_id} is on rail {hire.rail!r}, not erc8183")
    if hire.status not in (HiredStatus.PENDING, HiredStatus.PAID):
        raise AlreadyPaid(f"hire is {hire.status.value}, cannot resubmit jobId")
    if hire.job_id is not None and hire.job_id != payload.job_id:
        raise Conflict(
            f"hire already linked to jobId {hire.job_id}; refusing to overwrite"
        )

    now = datetime.now(tz=timezone.utc)
    hire.job_id = payload.job_id
    hire.tx_hash = payload.tx_hash
    hire.status = HiredStatus.PAID
    hire.updated_at = now
    await db.commit()
    await db.refresh(hire)
    return HireEscrowOut.model_validate(hire)


# ---------------------------------------------------------------------------
# GET /api/hires/escrow/{id}
# ---------------------------------------------------------------------------


@router.get("/escrow/{hire_id}", response_model=HireEscrowOut)
async def get_escrow_hire(
    hire_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> HireEscrowOut:
    """Return the hire row + (optionally) the on-chain JobState.

    On-chain fields (`onchain_status`, `onchain_expired_at`, ...) are
    populated only after the browser has reported `job_id` via /submit AND
    the polling helper has fetched the live state. For v1 we expose only
    the DB row — the browser polls the contract directly via ethers' read
    provider for the live state. The shape is kept extensible so the
    backend-side polling helper can be added later without a schema break.
    """
    hire = await _load_owned_hire(db, hire_id, user)
    return HireEscrowOut.model_validate(hire)


__all__ = ["router"]
