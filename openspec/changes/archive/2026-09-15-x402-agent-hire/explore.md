# Explore: x402-agent-hire — price the Hire CTA from the agent's real x402 offer

**Change:** `x402-agent-hire`
**Branch:** `feat/x402-agent-hire`
**Phase:** Explore
**Inputs read:** `app/routers/hires.py`, `app/routers/payments.py`, `app/routers/pages.py`, `app/services/payment.py`, `app/services/agent_payments.py`, `app/db/models/agent.py`, `app/templates/pages/agent_detail.html`, `app/static/js/payment.js`, spike results against `x402.payai.network/api/base-sepolia/paid-content`.

---

## 1. What exists today

### The hire flow is marketplace-priced, not agent-priced

- `app/routers/hires.py::create_hire` builds the B402 challenge with
  `amount = settings.x402_default_price_usd` (flat $1.00), `pay_to = agent.agent_wallet`,
  and optionally appends a **model-A commission fee** as a second `accept`
  (`fee_pay_to` + `fee_amount_wei`).
- `app/services/payment.py::build_challenge` already supports the second accept
  for the marketplace fee — the docstring says "model-A commission".
- `app/static/js/payment.js` already signs both accepts (`accept = challenge.accepts[0]`,
  `feeAccept = challenge.accepts[1]`).
- `app/routers/pages.py::_hire_pricing` renders the flat price on the button.

### The agent's real x402 offer is disconnected

- The agent's real server (A2A/MCP/web endpoint) responds `402` + `PAYMENT-REQUIRED`
  header with its own `accepts[]` (payTo, amount, asset, network).
- The marketplace never probes it. `agent_wallet` is used as payTo regardless of what
  the agent actually charges.
- `AgentCache.agent_url` and `a2a_endpoint` are **both nullable**; the hire-discovery-gap
  TODO at `app/routers/pages.py:592` documents `agent_url` being null in 6/6 sampled agents.

### Spike proof (this change's foundation)

Tested live against `x402.payai.network/api/base-sepolia/paid-content`:

```
HTTP/2 402
payment-required: <base64 JSON challenge>
```

Decoded challenge:

```json
{"x402Version":2,"error":"PAYMENT-SIGNATURE header is required",
 "accepts":[{"scheme":"exact","network":"eip155:84532","amount":"10000",
             "payTo":"0x2a835A505d4Ea32372Cc420d2663b885cE089453",
             "maxTimeoutSeconds":300,
             "asset":"0x036CbD53842c5426634e7929541eC2318f3dCF7e",
             "extra":{...}}]}
```

The shape matches `build_challenge`'s output (scheme exact, eip155:chain, amount wei str,
payTo, asset, maxTimeoutSeconds). Only `extra` differs (PayAI sends description/resource/
outputSchema; our repo sends assetTransferMethod: eip3009).

## 2. Surfaces relevant to this change

| Surface | State | Role in this change |
|---|---|---|
| `app/services/payment.py::build_challenge` | ✓ supports fee as second accept | Build challenge with REAL agent amount/payTo + marketplace fee |
| `app/routers/hires.py::create_hire` | Uses flat price | Use the probed agent offer (amount, payTo) instead |
| `app/routers/pages.py::_hire_pricing` | Renders flat price | Replace with real price from the offer probe |
| `app/db/models/agent.py` | `a2a_endpoint`, `agent_url`, `agent_wallet` nullable | Source of the endpoint to probe |
| `app/db/models/hired_agent.py` | `amount`, `pay_to`, `token`, `rail` | Add evidence columns (amount_agent, pay_to_agent, asset_agent, network_agent) |
| `app/templates/pages/agent_detail.html` | static button | Make it lazy: probe via HTMX, show real price or "not available" |
| `app/static/js/payment.js` | signs challenge accepts | No change needed (already signs accepts[0] + accepts[1]) |
| **NEW** `app/services/x402_client.py` | — | Probe agent endpoint, parse 402 + PAYMENT-REQUIRED → AgentOffer |
| **NEW** `app/routers/agents.py` (or pages) | — | HTMX endpoint `GET /agents/{chain}/{token}/hire-offer` |
| **NEW** `app/templates/partials/hire_offer.html` | — | Partial: real-price button or "not available" |

## 3. Design decisions locked by the user

1. **Endpoint source**: `a2a_endpoint` first, `agent_url` as fallback.
2. **No 402 / timeout / null endpoint**: button shows **"not available"** (disabled).
3. **Probe timing**: **lazy via HTMX** — button renders as "Checking availability…",
   HTMX calls the hire-offer endpoint after render, partial swaps in the real price
   or the disabled state.
4. **Fee**: marketplace fee (model-A commission) **adds on top** of the agent's real
   price as a second `accept` (already supported by `build_challenge`).
5. **Evidence**: add columns to `HiredAgent` recording what the agent quoted
   (`amount_agent`, `pay_to_agent`, `asset_agent`, `network_agent`) — small migration.
6. **SDD fast-track**: proposal → spec → design → tasks → apply (strict TDD) → verify → archive.

## 4. Risks

| # | Risk | Mitigation |
|---|---|---|
| R-1 | Probe adds latency and can hang | 2s timeout + HTMX lazy render (never blocks first paint) |
| R-2 | SSRF: probing arbitrary URLs from server | Validate scheme http/https only; block localhost/private ranges; pin to agent-declared endpoints |
| R-3 | Agent could quote an asset/network the marketplace doesn't support | Additive evidence columns; v1 only renders price when asset/network match `x402_chain_id` + `x402_u_token_address`; otherwise "not available" |
| R-4 | `a2a_endpoint` may point to an A2A AgentCard, not an x402 endpoint | Probe parses 402; if it doesn't return 402, falls through to "not available" — no false positive |
| R-5 | Agent reply changes between probe and pay | create_hire uses the probed offer; if it drifts, verify_payment still enforces payTo/amount at pay time — already covered |
| R-6 | Existing tests pin flat price in hire flow | Tests updated to cover both paths (probe 402 → real price; no probe → not available) |

## 5. Summary for proposal phase

Smallest cohesive slice:
1. `x402_client.py` probe helper (+ schema) — pure, DB-free, testable against the PayAI
   real 402 fixture.
2. HTMX hire-offer endpoint + partial (real price or "not available").
3. `create_hire` uses probed offer when available; evidence columns on HiredAgent.
4. Migration for the 4 evidence columns.