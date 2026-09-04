"""Hire router: challenge-backed create, pay, and status (FU-2).

Spec: `sdd/x402-real-payment/spec` — hires-x402 (H1-H4) + x402-payments.
Design: id 52 router contracts; Q6 (no owner fallback), X6 (idempotent by
hire id), H3 (lazy TTL sweep on create).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models.agent import AgentCache
from app.db.models.hired_agent import HiredAgent, HiredStatus
from app.db.models.user import User
from app.db.session import get_db
from app.errors import (
    AlreadyPaid,
    BroadcastFailed,
    ChallengeExpired,
    Forbidden,
    NoPayTo,
    NotFound,
    PaymentGatewayUnconfigured,
)
from app.schemas.hired import HireCreate, HireCreateOut, HireOut, HirePayOut
from app.services.auth import get_current_user, require_csrf
from app.services.payment import (
    DEFAULT_TIMEOUT_SECONDS,
    EIP3009_RAIL,
    Broadcaster,
    FakeBroadcaster,
    OnchainBroadcaster,
    build_challenge,
    decode_envelope,
    get_token_config,
    verify_payment,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/hires", tags=["hires"])

#: $U has 18 decimals; amount column stores $U units, challenge needs wei.
_WEI_PER_UNIT = Decimal(10**18)


def get_broadcaster() -> Broadcaster:
    """DI hook for the settlement interface (design X5).

    Prod uses OnchainBroadcaster; the fake is only reachable through
    dependency override or when the facilitator key is empty — and pay
    gates on the key before broadcasting, answering 503.
    """
    if get_settings().x402_payments_configured:
        return OnchainBroadcaster()
    return FakeBroadcaster()


async def sweep_expired(db: AsyncSession, user: User) -> int:
    """Cancel the user's pending hires past `challenge_expiry` (lazy TTL,
    spec H3; no cron in v1). Returns the number cancelled."""
    now = datetime.now(tz=timezone.utc)
    result = await db.execute(
        update(HiredAgent)
        .where(
            HiredAgent.address == user.address,
            HiredAgent.status == HiredStatus.PENDING,
            HiredAgent.challenge_expiry.is_not(None),
            HiredAgent.challenge_expiry < now,
        )
        .values(status=HiredStatus.CANCELLED, updated_at=now)
    )
    return max(0, cast(CursorResult[Any], result).rowcount or 0)


@router.post("", response_model=HireCreateOut, status_code=status.HTTP_201_CREATED)
async def create_hire(
    payload: HireCreate,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _csrf: Annotated[None, Depends(require_csrf)] = None,
) -> HireCreateOut:
    """Create a pending hire + B402 challenge (spec X1/X2, H1)."""
    agent = await db.scalar(select(AgentCache).where(AgentCache.agent_id == payload.agent_id))
    if agent is None:
        raise NotFound(f"agent {payload.agent_id!r} not cached")
    pay_to = agent.agent_wallet
    if not pay_to:
        raise NoPayTo("agent has no payment wallet configured (no owner fallback)")

    settings = get_settings()
    amount = settings.x402_default_price_usd
    amount_wei = int(amount * _WEI_PER_UNIT)
    fee_wallet = (settings.x402_fee_wallet or "").strip()
    fee_wei = (
        int(settings.x402_fee_amount_usd * _WEI_PER_UNIT) if fee_wallet else None
    )
    now = datetime.now(tz=timezone.utc)

    await sweep_expired(db, user)
    row = HiredAgent(
        address=user.address,
        agent_id=payload.agent_id,
        status=HiredStatus.PENDING,
        amount=amount,
        token=settings.x402_u_token_address,
        rail=EIP3009_RAIL,
        pay_to=pay_to,
        challenge_expiry=now + timedelta(seconds=DEFAULT_TIMEOUT_SECONDS),
    )
    db.add(row)
    await db.flush()
    resource_url = f"{str(request.base_url).rstrip('/')}/api/hires/{row.id}"
    challenge = build_challenge(
        pay_to,
        resource_url,
        amount_wei=amount_wei,
        timeout_s=DEFAULT_TIMEOUT_SECONDS,
        chain_id=settings.x402_chain_id,
        fee_pay_to=fee_wallet or None,
        fee_amount_wei=fee_wei,
    )
    await db.commit()
    await db.refresh(row)
    out = HireCreateOut.model_validate(row)
    out.challenge = challenge
    return out


@router.post("/{hire_id}/pay", response_model=HirePayOut)
async def pay_hire(
    hire_id: int,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    broadcaster: Annotated[Broadcaster, Depends(get_broadcaster)],
    _csrf: Annotated[None, Depends(require_csrf)] = None,
) -> HirePayOut:
    """Decode + verify + broadcast the settlement (spec H1/H4, X4-X7).

    Idempotent by hire id: a non-pending hire answers 409 before any
    broadcast; a failed broadcast flips the hire to `failed` and re-raises
    503 — never 200 on error.
    """
    hire = await db.get(HiredAgent, hire_id)
    if hire is None:
        raise NotFound(f"hire {hire_id} not found")
    if hire.address != user.address:
        raise Forbidden("not your hire")
    if hire.status != HiredStatus.PENDING:
        raise AlreadyPaid(f"hire is {hire.status.value}, not pending")

    settings = get_settings()
    if not settings.x402_payments_configured:
        raise PaymentGatewayUnconfigured("facilitator key not configured")
    now = datetime.now(tz=timezone.utc)
    if hire.challenge_expiry is not None and hire.challenge_expiry < now:
        raise ChallengeExpired("challenge expired")
    if hire.amount is None or not hire.pay_to:
        raise ChallengeExpired("hire has no payment data")

    decoded = decode_envelope(
        request.headers.get("X-PAYMENT") or request.headers.get("PAYMENT-SIGNATURE")
    )
    token_cfg = get_token_config(settings, settings.x402_chain_id)
    verify_payment(
        decoded,
        chain_id=settings.x402_chain_id,
        token_cfg=token_cfg,
        pay_to=hire.pay_to,
        amount_wei=int(hire.amount * _WEI_PER_UNIT),
        payer=user.address,
        now=now,
    )
    # Marketplace fee (model A): the same payer signs a second authorization
    # to the configured fee wallet; both are verified before any broadcast.
    fee_wei = None
    if decoded.fee is not None:
        fee_wallet = (settings.x402_fee_wallet or "").strip()
        if not fee_wallet:
            raise ChallengeExpired("fee payment sent but no fee wallet configured")
        fee_wei = int(settings.x402_fee_amount_usd * _WEI_PER_UNIT)
        verify_payment(
            decoded.fee,
            chain_id=settings.x402_chain_id,
            token_cfg=token_cfg,
            pay_to=fee_wallet,
            amount_wei=fee_wei,
            payer=user.address,
            now=now,
        )

    try:
        # Fee first: if it cannot settle, the hire payment is never sent.
        # The principal settles last so the receipt hash is the hire itself.
        if decoded.fee is not None:
            await broadcaster.broadcast(
                decoded.fee,
                token_cfg,
                facilitator_key=settings.x402_facilitator_key,
                rpc_url=settings.x402_rpc_url_resolved,
                now=now,
            )
        result = await broadcaster.broadcast(
            decoded,
            token_cfg,
            facilitator_key=settings.x402_facilitator_key,
            rpc_url=settings.x402_rpc_url_resolved,
            now=now,
        )
    except BroadcastFailed:
        hire.status = HiredStatus.FAILED
        hire.updated_at = now
        await db.commit()
        raise

    hire.status = HiredStatus.PAID
    hire.tx_hash = result.tx_hash
    hire.updated_at = now
    await db.commit()
    await db.refresh(hire)
    return HirePayOut(id=hire.id, status=hire.status, tx_hash=hire.tx_hash)


@router.get("/{hire_id}", response_model=HireOut)
async def get_hire_status(
    hire_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> HireOut:
    """Hire status + tx_hash (spec H2): 401 unauth, 403 not owner, 404."""
    hire = await db.get(HiredAgent, hire_id)
    if hire is None:
        raise NotFound(f"hire {hire_id} not found")
    if hire.address != user.address:
        raise Forbidden("not your hire")
    return HireOut.model_validate(hire)


__all__ = ["get_broadcaster", "router", "sweep_expired"]
