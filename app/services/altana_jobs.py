"""ERC-8183 job-escrow wrapper (buyer-side, BSC mainnet).

Pure-function module: builds the calldata the browser must sign + decode the
on-chain `getJob` tuple into a typed `JobState`. **Never touches the chain**
itself — the browser pays its own gas via MetaMask.

Spec: docs/category-study.md §ERC-8183 (buyer-side).

Public surface:
    - 4-byte selectors: CREATE_JOB_SELECTOR, FUND_SELECTOR, APPROVE_SELECTOR
    - calldata builders: build_create_job_calldata, build_fund_calldata,
      build_approve_calldata
    - state parser: parse_job_state → JobState

The browser flow it supports:
    1. POST /api/hires/escrow       → backend returns params for createJob
    2. User signs createJob         → returns jobId
    3. POST /api/hires/escrow/{id}/submit { jobId, txHash } → FUNDED in DB
    4. Browser polls getJob(jobId)  → display status

No web3.py dependency — uses eth_abi + eth_utils directly to keep the dep
footprint small and the code auditable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import IntEnum
from typing import Final

import eth_abi
from eth_utils import keccak

# ABIs are exported by `altana_abi` for tests + downstream callers; the
# wrapper itself only needs them implicitly via eth_abi's canonical type
# strings.
from app.services.altana_abi import JOB_TUPLE_TYPE  # noqa: F401

__all__ = [
    "APPROVE_SELECTOR",
    "CREATE_JOB_SELECTOR",
    "FUND_SELECTOR",
    "JobState",
    "JobStatusInt",
    "build_approve_calldata",
    "build_create_job_calldata",
    "build_fund_calldata",
    "parse_job_state",
]


# ---------------------------------------------------------------------------
# Function selectors (4-byte keccak of the canonical signature).
# ---------------------------------------------------------------------------

# keccak("createJob(address,address,uint256,string,address)")[:4]
CREATE_JOB_SELECTOR: Final[str] = "0x" + keccak(
    text="createJob(address,address,uint256,string,address)"
)[:4].hex()
# keccak("fund(uint256,uint256,bytes)")[:4]
FUND_SELECTOR: Final[str] = "0x" + keccak(text="fund(uint256,uint256,bytes)")[:4].hex()
# keccak("approve(address,uint256)")[:4]
APPROVE_SELECTOR: Final[str] = "0x" + keccak(text="approve(address,uint256)")[:4].hex()


# ---------------------------------------------------------------------------
# Address validation
# ---------------------------------------------------------------------------

_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _check_address(name: str, value: str) -> None:
    """Reject anything that isn't a 20-byte hex address (case-preserved)."""
    if not isinstance(value, str) or not _ADDRESS_RE.match(value):
        raise ValueError(f"{name} must be a 0x-prefixed 40-hex address, got {value!r}")
    if int(value, 16) == 0:
        raise ValueError(f"{name} must be non-zero")


# ---------------------------------------------------------------------------
# JobStatus enum (mirrors IACP.sol)
# ---------------------------------------------------------------------------


class JobStatusInt(IntEnum):
    """On-chain JobStatus enum from AgenticCommerceUpgradeable.

    Source: bnb-chain/apex-contracts/contracts/IACP.sol.
    """

    Open = 0
    Funded = 1
    Submitted = 2
    Completed = 3
    Rejected = 4
    Expired = 5


_STATUS_TO_STR: Final[dict[int, str]] = {member.value: member.name for member in JobStatusInt}


# ---------------------------------------------------------------------------
# Calldata builders
# ---------------------------------------------------------------------------


def build_create_job_calldata(
    provider: str,
    evaluator: str,
    expired_at: int,
    description: str,
    hook: str,
) -> str:
    """Encode `createJob(provider, evaluator, expiredAt, description, hook)`.

    Args:
        provider: address receiving the escrow on `complete` (the agent).
        evaluator: address that judges the deliverable (router proxy).
        expired_at: unix timestamp; must be > now + 5min per contract.
        description: free-form task description (used for audit trail).
        hook: address called on job events (router proxy doubles as hook).

    Returns:
        0x-prefixed hex calldata ready to be sent to the commerce proxy.

    Raises:
        ValueError: on bad address / past expiry.
    """
    _check_address("provider", provider)
    _check_address("evaluator", evaluator)
    _check_address("hook", hook)
    now = int(datetime.now(tz=timezone.utc).timestamp())
    if expired_at <= now + 300:  # contract enforces now+5min
        raise ValueError(
            f"expired_at must be > now+5min (got {expired_at}, now={now})"
        )
    if not description:
        raise ValueError("description must be non-empty")

    encoded_args = eth_abi.encode(
        ["address", "address", "uint256", "string", "address"],
        [provider, evaluator, expired_at, description, hook],
    )
    return CREATE_JOB_SELECTOR + encoded_args.hex()


def build_fund_calldata(job_id: int, expected_budget: int) -> str:
    """Encode `fund(jobId, expectedBudget, optParams=0x)`.

    Args:
        job_id: id returned by createJob.
        expected_budget: wei amount; must equal the approved $U balance.

    Raises:
        ValueError: on zero job_id or zero budget.
    """
    if job_id <= 0:
        raise ValueError(f"job_id must be > 0 (got {job_id})")
    if expected_budget <= 0:
        raise ValueError(f"expected_budget must be > 0 (got {expected_budget})")
    encoded_args = eth_abi.encode(
        ["uint256", "uint256", "bytes"],
        [job_id, expected_budget, b""],
    )
    return FUND_SELECTOR + encoded_args.hex()


def build_approve_calldata(spender: str, amount: int) -> str:
    """Encode `approve(spender, amount)` for the $U token.

    Args:
        spender: commerce proxy address.
        amount: wei amount to allow the proxy to pull.
    """
    _check_address("spender", spender)
    if amount <= 0:
        raise ValueError(f"amount must be > 0 (got {amount})")
    encoded_args = eth_abi.encode(["address", "uint256"], [spender, amount])
    return APPROVE_SELECTOR + encoded_args.hex()


# ---------------------------------------------------------------------------
# State parser
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JobState:
    """Typed view of an on-chain Job (decoded from `getJob(jobId)`)."""

    job_id: int
    client: str
    provider: str
    evaluator: str
    description: str
    budget_wei: int
    expired_at: int
    status: str  # one of JobStatusInt.name
    hook: str
    submitted_at: int
    deliverable: bytes  # 32-byte keccak hash


def parse_job_state(raw: tuple) -> JobState:
    """Decode the 11-element tuple returned by `commerce.getJob(jobId)`.

    The on-chain tuple layout matches `JOB_TUPLE_TYPE` in `altana_abi.py`.
    """
    if len(raw) != 11:
        raise ValueError(f"job tuple must have 11 fields, got {len(raw)}")
    status_int = int(raw[7])
    if status_int not in _STATUS_TO_STR:
        raise ValueError(f"unknown job status {status_int}")
    return JobState(
        job_id=int(raw[0]),
        client=raw[1],
        provider=raw[2],
        evaluator=raw[3],
        description=raw[4],
        budget_wei=int(raw[5]),
        expired_at=int(raw[6]),
        status=_STATUS_TO_STR[status_int],
        hook=raw[8],
        submitted_at=int(raw[9]),
        deliverable=raw[10],
    )
