# Spec: x402-agent-hire — price the Hire CTA from the agent's real x402 offer

**Change:** `x402-agent-hire`
**Domain:** payments / agent-hire (extends `x402-real-payment` behavior)
**Scope:** probe helper, hire-offer HTMX endpoint + partial, lazy button render, `create_hire` using the real offer, evidence columns on `HiredAgent`, tests.
**Output:** this file `openspec/changes/x402-agent-hire/spec.md`.

## Purpose

Today the marketplace prices every hire at the same flat `X402_DEFAULT_PRICE_USD`
($1.00) regardless of what the agent actually charges. The agent's own x402 server —
its A2A/MCP/web endpoint — responds `402 Payment Required` with a
`PAYMENT-REQUIRED` header encoding its real `accepts[]` (payTo, amount, asset,
network), but the marketplace never probes it. The Hire CTA shows the marketplace's
flat price, pays the agent's wallet, and ignores the agent's quoted price entirely.
This is disconnected from the x402 model the agents themselves advertise: an agent is
a server that tells you its price when you ask.

This change makes the marketplace ask. When an agent advertises a real x402 offer, the
Hire CTA is priced from that offer (the agent's own `payTo` and `amount`), with the
marketplace fee applied on top as a second `accept`. When the agent cannot be reached
or quotes an unsupported asset/network, the CTA shows a disabled "not available" state
rather than guessing a price.

The probe is performed lazily via HTMX so it never blocks first paint, bounded by a 2s
timeout, and constrained by an SSRF guard (http/https schemes only, no private or
loopback ranges). The agent's quoted values are recorded as evidence columns on each
`HiredAgent` row so the marketplace keeps an audit trail of what the agent actually
quoted at hire time.

## Non-Goals

- **Multi-asset support:** only the offer whose `asset`/`network` match the
  marketplace rail (`x402_u_token_address` + `x402_chain_id`) is rendered as a price;
  every other asset/network renders as "not available".
- **Settlement / verify changes:** the challenge-build and verify/payment path are not
  changed beyond feeding the real offer into `accepts[0]`; settlement remains governed
  by the existing verify-time enforcement of `payTo`/`amount`.
- **Scheduler / proactive probing:** offers are probed on-demand at hire time; no
  background scheduler or offline cache of offers is introduced.
- **SSRF hardening beyond the guard:** only scheme restriction plus private/loopback
  range blocking; deeper network egress policy is out of scope.

## Acceptance Criteria

- **AC-1 — Probe result:** `probe_agent_offer()` returns an `AgentOffer` (parsed from
  a `402` + `PAYMENT-REQUIRED` response) for the PayAI 402 fixture, and returns `None`
  for non-402, timeout, malformed, or unreachable responses.
- **AC-2 — Real price rendered:** `GET /agents/{chain}/{token}/hire-offer` renders the
  real-price button when the agent endpoint returns a valid 402 offer whose
  asset/network match the marketplace rail.
- **AC-3 — Not available rendered:** the same endpoint renders a disabled "not
  available" state when the endpoint is null, down/timed out, non-402, or quotes an
  unsupported asset/network.
- **AC-4 — create_hire uses offer:** `create_hire` uses the agent's real `pay_to` and
  `amount` for `accepts[0]` when an offer is available; it falls back to the flat
  price when not.
- **AC-5 — Evidence persisted:** each `HiredAgent` row persists the 4 evidence columns
  (`amount_agent`, `pay_to_agent`, `asset_agent`, `network_agent`).
- **AC-6 — Fee on top:** the marketplace fee still applies on top of the real agent
  price as `accepts[1]` when an offer is used.
- **AC-7 — Test baseline:** the full pytest baseline is preserved (414 passed at the
  branch point) with new tests covering both the offer and the not-available paths.

## Requirements

### R1: Probe helper returns AgentOffer or None

The probe helper SHALL GET the agent's endpoint with a 2s timeout, and SHALL return an
`AgentOffer` (pay_to, amount_wei, asset, network, price_usd) parsed from a `402` +
`PAYMENT-REQUIRED` response. It SHALL return `None` for any non-402 status, timeout,
malformed payload, or unreachable endpoint.

#### Scenario: Valid 402 offer

- GIVEN the agent endpoint returns `HTTP 402` with a `PAYMENT-REQUIRED` header encoding
  a valid v2 challenge with `accepts[0]` containing payTo, amount, asset, network
- WHEN the probe helper requests the endpoint
- THEN it returns an `AgentOffer` whose pay_to/amount_wei/asset/network match the
  challenge and whose price_usd is derived from the amount

#### Scenario: Non-402 / timeout / malformed

- GIVEN the agent endpoint returns a non-402 status, times out, is unreachable, or
  returns an unparseable `PAYMENT-REQUIRED` payload
- WHEN the probe helper requests the endpoint
- THEN it returns `None` and does not raise

### R2: Endpoint source — a2a_endpoint then agent_url

The hire-offer probe SHALL prefer `a2a_endpoint` as the endpoint source and SHALL fall
back to `agent_url` when `a2a_endpoint` is null or empty. When both are null/empty, the
probe SHALL yield no offer.

#### Scenario: a2a_endpoint present

- GIVEN an agent whose `a2a_endpoint` is populated and `agent_url` is also set
- WHEN the probe runs
- THEN it probes `a2a_endpoint` (never `agent_url`)

#### Scenario: a2a_endpoint null, agent_url present

- GIVEN an agent whose `a2a_endpoint` is null and `agent_url` is populated
- WHEN the probe runs
- THEN it probes `agent_url`

#### Scenario: both null

- GIVEN an agent whose `a2a_endpoint` and `agent_url` are both null
- WHEN the probe runs
- THEN it yields no offer (renders "not available")

### R3: Render real price when asset+network match the rail

The hire-offer endpoint SHALL render the real-price button only when the probed offer's
`asset` and `network` match the marketplace rail (`x402_u_token_address` and
`x402_chain_id`), showing the offer's price.

#### Scenario: matching offer

- GIVEN an offer whose asset equals `x402_u_token_address` and network equals the
  marketplace chain
- WHEN `GET /agents/{chain}/{token}/hire-offer` is requested
- THEN the response renders an enabled Hire button showing the offer's real price

### R4: Render disabled "not available" otherwise

The hire-offer endpoint SHALL render a disabled "not available" state when there is no
offer, the endpoint is null/down/timed out/non-402, or the offer's asset/network do not
match the rail.

#### Scenario: no or unsupported offer

- GIVEN no offer is available, or the offer's asset/network do not match the rail
- WHEN `GET /agents/{chain}/{token}/hire-offer` is requested
- THEN the response renders a disabled button labeled "not available"

### R5: create_hire uses the offer or falls back to flat

`create_hire` SHALL use the offer's `pay_to` and `amount` for `accepts[0]` when an
offer is available; otherwise it SHALL fall back to the current flat price and the
agent wallet.

#### Scenario: offer available

- GIVEN an available offer whose asset/network match the rail
- WHEN a hire is created
- THEN `accepts[0]` uses the offer's `pay_to` and `amount`

#### Scenario: no offer

- GIVEN no offer is available
- WHEN a hire is created
- THEN `accepts[0]` uses the flat price and the agent wallet (existing behavior)

### R6: Marketplace fee applies on top of the real price

The marketplace fee (model-A commission, `X402_FEE_WALLET`) SHALL be added on top of
the real agent price as a second `accept` (`accepts[1]`) whenever the real offer is
used, consistent with the existing fee-on-flat behavior.

#### Scenario: fee on real price

- GIVEN an offer is used for `accepts[0]`
- WHEN the hire challenge is built
- THEN `accepts[1]` carries the marketplace fee (fee_pay_to + fee_amount_wei) computed
  from the agent's real amount

### R7: HiredAgent persists 4 evidence columns

The `HiredAgent` model SHALL persist `amount_agent`, `pay_to_agent`, `asset_agent`, and
`network_agent` recording what the agent quoted, via a migration. These SHALL be
populated when an offer is used and null when it is not.

#### Scenario: offer used

- GIVEN a hire is created using a probed offer
- WHEN the `HiredAgent` row is written
- THEN `amount_agent`, `pay_to_agent`, `asset_agent`, and `network_agent` hold the
  offer's quoted values

#### Scenario: no offer

- GIVEN a hire is created without a probed offer
- WHEN the `HiredAgent` row is written
- THEN the 4 evidence columns are null

### R8: SSRF guard

The probe SHALL only issue requests with `http` or `https` schemes and SHALL refuse
hosts resolving to private or loopback ranges, so the server cannot be used to probe
internal addresses.

#### Scenario: disallowed scheme / private range

- GIVEN an endpoint with a non-http(s) scheme, or a host resolving to a private or
  loopback range
- WHEN the probe is attempted
- THEN no request is made and the probe yields no offer

#### Scenario: allowed public endpoint

- GIVEN an http(s) endpoint resolving to a public (non-private, non-loopback) address
- WHEN the probe is attempted
- THEN the request is made and the offer is parsed normally

### R9: Lazy HTMX contract

The Hire button SHALL render initially as "Checking availability…" and SHALL trigger
`hx-get` to `GET /agents/{chain}/{token}/hire-offer`, swapping the result partial in.
Without JavaScript, the behavior SHALL fall back to the flat price or the disabled
state rather than breaking the page.

#### Scenario: lazy probe swap

- GIVEN the detail page renders the Hire button
- WHEN the button's initial "Checking availability…" state loads
- THEN HTMX requests the hire-offer endpoint and swaps the partial in, showing the
  real price or the disabled "not available" state

#### Scenario: no-JS fallback

- GIVEN JavaScript is unavailable
- WHEN the Hire button renders
- THEN the page still presents a usable flat-priced or disabled Hire control (no
  broken/unstyled dead button)

## Out of Scope

- Multi-asset support beyond the single rail match (other assets/network → "not
  available").
- Changes to the settlement/verify/payment path beyond feeding the real offer into
  `accepts[0]`.
- SSRF hardening beyond http/https scheme restriction and private/loopback range
  blocking.
- A scheduler or offline cache of agent offers.
