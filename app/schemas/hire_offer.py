"""HireOffer schema — typed render context for the hire-offer endpoint.

The endpoint `/agents/{chain_id}/{token_id}/hire-offer` builds a `HireOffer`
after probing the agent's x402 offer and passes it to the
`partials/hire_offer.html` fragment. `model_dump()`-able so a non-HTMX caller
gets the same contract as JSON without extra plumbing.
"""

from __future__ import annotations

from pydantic import BaseModel


class HireOffer(BaseModel):
    """What the Hire CTA should show after the lazy probe (x402-agent-hire).

    `price_usd` is the TOTAL the user pays — the agent's real price plus the
    marketplace fee (hard constraint D-4: fee adds on top). `disabled` is True
    for every not-available state (D-2); `reason` carries one of
    "no-payment-wallet" | "no-endpoint" | "endpoint-unreachable" |
    "asset-network-not-supported" | None.
    """

    has_offer: bool
    price_usd: float | None = None
    agent_price_usd: float | None = None
    fee_usd: float | None = None
    pay_to: str | None = None
    disabled: bool
    reason: str | None = None