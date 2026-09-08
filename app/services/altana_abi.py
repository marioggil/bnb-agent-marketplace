"""ERC-8183 ABI subset for the bnb-agent marketplace.

Subset of the official `AgenticCommerceUpgradeable` ABI from
https://github.com/bnb-chain/apex-contracts (only the functions we call from
the backend: `createJob`, `fund`, `setBudget`, `getJob`, `jobs`, plus the
event `JobFunded` we listen for in the polling client).

Plus the minimal ERC-20 ABI (approve, allowance) to drive the budget
deposit (`approve → fund` two-step).

Kept as Python lists so `eth_abi.encode/decode` consumes them directly
without a JSON parse on every call.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# AgenticCommerceUpgradeable (BSC mainnet proxy: 0xEa4DAa3100A767e86FDed867729ae7446476EBA6)
# ---------------------------------------------------------------------------

# Function signatures we call from the wrapper (calldata builders).
COMMERCE_FUNCTION_ABI: list[dict] = [
    {
        "name": "createJob",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "provider", "type": "address"},
            {"name": "evaluator", "type": "address"},
            {"name": "expiredAt", "type": "uint256"},
            {"name": "description", "type": "string"},
            {"name": "hook", "type": "address"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "fund",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "jobId", "type": "uint256"},
            {"name": "expectedBudget", "type": "uint256"},
            {"name": "optParams", "type": "bytes"},
        ],
        "outputs": [],
    },
    {
        "name": "setBudget",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "jobId", "type": "uint256"},
            {"name": "amount", "type": "uint256"},
            {"name": "optParams", "type": "bytes"},
        ],
        "outputs": [],
    },
    {
        "name": "getJob",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "jobId", "type": "uint256"}],
        # Tuple(Job): see Job tuple below. Use the raw ABI tuple form so
        # `eth_abi.decode` matches the on-chain layout.
        "outputs": [
            {
                "name": "",
                "type": "tuple",
                "components": [
                    {"name": "id", "type": "uint256"},
                    {"name": "client", "type": "address"},
                    {"name": "provider", "type": "address"},
                    {"name": "evaluator", "type": "address"},
                    {"name": "description", "type": "string"},
                    {"name": "budget", "type": "uint256"},
                    {"name": "expiredAt", "type": "uint256"},
                    {"name": "status", "type": "uint8"},
                    {"name": "hook", "type": "address"},
                    {"name": "submittedAt", "type": "uint256"},
                    {"name": "deliverable", "type": "bytes32"},
                ],
            }
        ],
    },
]

# Tuple types used by `eth_abi.decode`.
JOB_TUPLE_TYPE = (
    "(uint256,address,address,address,string,uint256,uint256,uint8,address,uint256,bytes32)"
)

# ---------------------------------------------------------------------------
# ERC-20 ($U on BSC mainnet: 0xcE24439F2D9C6a2289F741120FE202248B666666)
# ---------------------------------------------------------------------------

ERC20_FUNCTION_ABI: list[dict] = [
    {
        "name": "approve",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "allowance",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "balanceOf",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]

__all__ = [
    "COMMERCE_FUNCTION_ABI",
    "JOB_TUPLE_TYPE",
    "ERC20_FUNCTION_ABI",
]
