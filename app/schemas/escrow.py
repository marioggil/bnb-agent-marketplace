"""Pydantic schemas for the ERC-8183 escrow hire flow.

Flow (browser pays its own gas):
    1. Client calls POST /api/hires/escrow with { agent_id, task, budget_wei }.
       Backend creates a HiredAgent(rail='erc8183', status=PENDING) and
       returns the pre-armed calldata + addresses for the browser to sign.
    2. Browser signs and broadcasts `createJob(...)` -> txHash_1.
    3. Browser calls POST /api/hires/escrow/{id}/submit with the on-chain
       jobId + txHash_1. Backend flips status -> FUNDED.
    4. Browser polls GET /api/hires/escrow/{id} -> status reflects the
       on-chain JobState (Open / Funded / Submitted / Completed / ...).
    5. (Seller side, out of scope for v1): seller submits deliverable on-chain.
    6. After dispute window, browser calls POST /api/hires/escrow/{id}/settle
       to release the escrow (`complete` on the contract).

Spec: docs/category-study.md §ERC-8183 (buyer-side).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.hired_agent import HiredStatus


class HireEscrowCreate(BaseModel):
    """Request body of POST /api/hires/escrow."""

    agent_id: str = Field(..., description="Canonical agent_id (chain_id:token_id).")
    task: str = Field(
        ...,
        min_length=1,
        max_length=4096,
        description="Free-form task description (audit trail on-chain).",
    )
    budget_wei: int = Field(
        ...,
        gt=0,
        description="Budget in raw $U wei (18 decimals). 1e17 = 0.1 $U.",
    )


class HireEscrowCall(BaseModel):
    """A single transaction the browser must sign + broadcast."""

    to: str = Field(..., description="Target contract address.")
    data: str = Field(..., description="0x-prefixed calldata (already ABI-encoded).")
    value: str = Field(
        default="0x0",
        description="Native value (wei); always 0 for ERC-20 + ERC-8183 calls.",
    )
    description: Literal["approve", "createJob", "fund"] = Field(
        ..., description="Human-readable label for the UI.",
    )


class HireEscrowCreateOut(BaseModel):
    """201 response of POST /api/hires/escrow.

    The browser receives three pre-armed transactions to sign in order:
    approve($U) -> createJob(...) -> fund(jobId, budget).
    After the createJob tx mines, the browser extracts jobId from the
    `JobCreated` event and POSTs it back via /submit.
    """

    model_config = ConfigDict(from_attributes=True)

    hire_id: int
    status: HiredStatus
    chain_id: int
    provider_address: str
    commerce_address: str
    u_token_address: str
    router_address: str
    policy_address: str
    # Five txs the browser must sign in order:
    approve: HireEscrowCall
    create_job: HireEscrowCall
    fund: HireEscrowCall
    # Hint for the dispute window: deadline after which `claimRefund` is callable.
    job_expiry_unix: int = Field(
        ..., description="Unix timestamp (seconds). Same as createJob's expiredAt.",
    )


class HireEscrowSubmit(BaseModel):
    """Request body of POST /api/hires/escrow/{id}/submit.

    The browser sends back the on-chain jobId and the createJob tx hash once
    the createJob transaction has mined.
    """

    job_id: int = Field(..., gt=0, description="On-chain ERC-8183 job id.")
    tx_hash: str = Field(..., min_length=66, max_length=66, description="0x + 64 hex.")


class HireEscrowOut(BaseModel):
    """Response of GET /api/hires/escrow/{id}.

    Mirrors HiredAgent + the live on-chain JobState when the browser has
    confirmed the createJob tx (status != 'pending' on the DB).
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    address: str
    agent_id: str
    status: HiredStatus
    tx_hash: str | None = None
    job_id: int | None = None
    chain_id: int | None = None
    provider_address: str | None = None
    budget_wei: Decimal | None = None
    created_at: datetime
    updated_at: datetime
    # On-chain state - only populated when the browser has posted job_id via
    # /submit AND we've successfully polled the contract.
    onchain_status: str | None = Field(
        default=None,
        description="One of: Open, Funded, Submitted, Completed, Rejected, Expired.",
    )
    onchain_expired_at: int | None = None
    onchain_submitted_at: int | None = None


class HireEscrowSettle(BaseModel):
    """Request body of POST /api/hires/escrow/{id}/settle.

    After the dispute window has passed with `onchain_status == Submitted`,
    the buyer (or anyone) calls `complete(jobId)` to release the escrow.
    """

    tx_hash: str = Field(..., min_length=66, max_length=66)


# Alias kept for the router signature readability.
EscrowCall = HireEscrowCall

__all__ = [
    "EscrowCall",
    "HireEscrowCall",
    "HireEscrowCreate",
    "HireEscrowCreateOut",
    "HireEscrowOut",
    "HireEscrowSettle",
    "HireEscrowSubmit",
]
