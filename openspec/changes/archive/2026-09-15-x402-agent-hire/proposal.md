# Proposal: x402-agent-hire — price the Hire CTA from the agent's real x402 offer

## Change name

`x402-agent-hire`

## Domain

Payments / agent hire (extends `x402-real-payment` spec)

## Status

Draft

---

## Problem

Today the marketplace prices every hire at the same flat `X402_DEFAULT_PRICE_USD`
($1.00) regardless of what the agent actually charges. The agent's own x402 server —
its A2A/MCP/web endpoint — responds `402 Payment Required` with a
`PAYMENT-REQUIRED` header encoding its real `accepts[]` (payTo, amount, asset,
network), but the marketplace never probes it. The Hire CTA shows the marketplace's
flat price, pays the agent's wallet, and ignores the agent's quoted price entirely.

This is disconnected from the x402 model the agents themselves advertise: an agent
is a server that tells you its price when you ask. The marketplace should ask.

## Existing evidence

- `app/routers/hires.py::create_hire` builds the challenge with
  `amount = settings.x402_default_price_usd`, `pay_to = agent.agent_wallet`.
- `app/services/payment.py::build_challenge` already appends a model-A marketplace
  commission fee as a second `accept` (`fee_pay_to` / `fee_amount_wei`).
- Spike proved the protocol end-to-end: `GET https://x402.payai.network/api/base-sepolia/paid-content`
  returns `HTTP/2 402` + `payment-required: <base64>` where the decoded payload is a
  v2 challenge with `accepts[0].payTo`, `accepts[0].amount` ("10000" wei),
  `accepts[0].asset`, `accepts[0].network` ("eip155:84532") — matching the shape
  `build_challenge` produces.
- `AgentCache.a2a_endpoint` / `agent_url` are nullable; the hire-discovery-gap TODO
  (pages.py:592) documents `agent_url` null in 6/6 sampled agents.

## What needs to change

1. **Probe helper** (`app/services/x402_client.py`): GET the agent endpoint, parse
   `402` + `PAYMENT-REQUIRED`, extract `AgentOffer(pay_to, amount_wei, asset,
   network, price_usd)`. Timeout 2s. Any non-402/timeout/parse failure → `None`.
2. **HTMX hire-offer endpoint** (`GET /agents/{chain}/{token}/hire-offer`): lazy
   probe; renders a partial with the real price + enabled button, or the disabled
   "not available" state.
3. **Partial** `app/templates/partials/hire_offer.html`: real-price button or
   disabled state.
4. **Detail page button**: starts as "Checking availability…" with `hx-get` to the
   new endpoint; swap-in replaces it.
5. **create_hire**: when an offer is available and its asset/network match the
   marketplace rail, use the agent's real `pay_to` + `amount` for `accepts[0]`,
   keep the marketplace fee as `accepts[1]`.
6. **Evidence columns** on `HiredAgent`: `amount_agent`, `pay_to_agent`,
   `asset_agent`, `network_agent` — records what the agent quoted (migration).
7. **Tests**: probe helper against a real 402 fixture; endpoint behavior (real price,
   not available, timeout); create_hire with/without offer; regression on flat path.

## Scope

**In**: probe helper, hire-offer endpoint + partial, button lazy render, create_hire
using offer, evidence columns, tests.

**Out**: multi-asset support (only asset/network matching the marketplace rail is
rendered as a price; everything else → "not available"), any change to the
settlement/verify path, SSRF hardening beyond http/https + private-range block,
scheduler for periodic probes.

## Acceptance criteria

1. AC-1 — `probe_agent_offer()` returns an `AgentOffer` for the PayAI 402 fixture
   and `None` for non-402/timeout/malformed.
2. AC-2 — `GET /agents/{chain}/{token}/hire-offer` renders the real price button
   when the agent endpoint returns 402 and the asset/network match the rail.
3. AC-3 — The same endpoint renders a disabled "not available" state when the
   endpoint is null, down, or quotes an unsupported asset/network.
4. AC-4 — `create_hire` uses the agent's real `pay_to`/`amount` when an offer is
   available; falls back to flat when not.
5. AC-5 — `HiredAgent` persists the 4 evidence columns.
6. AC-6 — The marketplace fee still applies on top of the real agent price.
7. AC-7 — Full pytest baseline preserved (414 passed at branch point).

## Risks

- Probe latency → 2s timeout + lazy HTMX render (never blocks first paint).
- SSRF → http/https only, private-range block, endpoint pinned to agent-declared URL.
- Unsupported asset/network → rendered as "not available" (no partial failure).
- Agent changes price between probe and pay → verify_payment at pay time already
  enforces payTo/amount; evidence columns keep the audit trail.
- Existing flat-price tests → updated to cover both paths.