# Spec: x402-remove-fee-multichain — remove the marketplace fee; settle on the agent's EVM chain

**Change:** `x402-remove-fee-multichain`
**Domain:** payments / agent-hire (extends `x402-real-payment` + `x402-agent-hire`)
**Scope:** fee removal end-to-end (`create_hire` / `pay_hire` / `payment.js`); static multi-chain rail map; per-chain `get_token_config`; `create_hire` charges the probed offer; hire-offer UI enabled for rail-map EVM chains; `decode_envelope` `payload.fee` back-compat; tests.
**Output:** this file `openspec/changes/x402-remove-fee-multichain/spec.md`.

## Purpose

Today every hire is overpriced and single-rail. `create_hire`/`build_challenge` append a second `accept` (model-A marketplace commission to `X402_FEE_WALLET`) and `payment.js` makes the user sign two EIP-712 authorizations, so the user pays more than the agent actually quoted. Settlement is pinned to the one configured `$U` chain (BSC 56/97) even though production agents quote USDC across several EVM chains — every one of those quotes renders "not available". This change removes the marketplace fee entirely: **the agent's quoted price is the price.** The hire flow never creates, signs, verifies, or broadcasts a fee payment; the fee is removed, not bypassed behind a flag. `decode_envelope` keeps parsing a legacy `payload.fee` so envelopes produced by older clients still decode, and the pay flow simply ignores that field.

Settlement becomes multi-chain EVM. A static rail map in config maps `chain_id → {rpc_url, token_address, token_name, token_version}` for the EVM chains agents quote — Base 8453, Polygon 137, Avalanche 43114 (all USDC), plus the existing `$U` chains BSC 56 and testnet 97 for compatibility. `get_token_config(settings, chain_id)` returns the rail-map token for the quoted chain, so `create_hire` builds its challenge on the chain and token the agent actually accepts, and `pay_hire` verifies and broadcasts against that same chain through the rail-map RPC. Unknown chains still raise and surface as "not available".

The user signs exactly one authorization, for the agent's quoted amount and wallet, on the agent's quoted network. The hire-offer CTA continues to render the agent's real price and is now enabled for any rail-map EVM chain; unsupported chains (Solana, non-EVM, chains outside the map) keep the disabled "not available" state. The user pays exactly what the agent quotes — no fee line, no second signature, no surprise.

## Non-Goals

- **Solana / non-EVM settlement:** offers on networks outside the rail map still render disabled "not available"; no new settlement rail is introduced.
- **Dynamic RPC configuration:** v1 uses a static chain→RPC map; no per-chain API-key RPC management, RPC auto-discovery, or failover between providers is added.
- **Balance / liquidity checks:** no real USDC balance checks, swaps, or facilitator redesign; the facilitator continues to pay gas only.
- **Fee-removal back-compat:** the fee code path is removed end-to-end, not gated behind an env flag; no configuration re-enables a marketplace fee.
- **Schema changes:** no new hire columns or migrations (the four agent-evidence columns already exist); no offer scheduler or offline offer cache.
- **Not a relitigation** of the hard-constrained decisions above.

## Acceptance Criteria

- **AC-1 — Per-chain token config:** `get_token_config(settings, chain_id)` returns the rail-map token for Base 8453, Polygon 137, and Avalanche 43114 (USDC address + EIP-712 name/version from the map) and the `$U` token for BSC 56 and testnet 97.
- **AC-2 — No fee accept:** the hire challenge built by `create_hire` carries exactly one `accept` (`accepts[0]`); fee parameters are removed/ignored and no second accept is appended.
- **AC-3 — Quoted price charged:** `create_hire` charges the agent's probed price — `amount`, `payTo`, `asset`, and `network` taken from the offer's `accepts[0]` — not the flat `x402_default_price_usd`.
- **AC-4 — Evidence persisted:** each `HiredAgent` row records `amount_agent`, `pay_to_agent`, `asset_agent`, and `network_agent` from the probed offer, and the row's payment fields reflect the offer's token and network.
- **AC-5 — Settle on the offer's chain:** `pay_hire` verifies and broadcasts against the chain/token stored on the hire row (RPC + token config from the rail map) and performs no fee verification or broadcast.
- **AC-6 — Single signature:** `payment.js` signs exactly one EIP-712 authorization (`accepts[0]`) and produces an envelope with no `payload.fee`.
- **AC-7 — Full suite green:** the pytest baseline (446 passed at the branch point) is preserved and extended; `uv run pytest` passes with all fee assertions migrated to agent-quoted price only.

## Requirements

### R1: Static rail map covers the three USDC EVM chains plus the $U chains

A static rail map SHALL exist in config mapping `chain_id` to `{rpc_url, token_address, token_name, token_version}` for Base 8453, Polygon 137, and Avalanche 43114 (USDC), and SHALL also cover the existing `$U` chains BSC 56 and testnet 97.

| chain_id | token_address | token |
|---|---|---|
| 8453 (Base) | `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` | USDC |
| 137 (Polygon) | `0x3c499c542cEF5E3811e1792ce70d8cC03d50c3359` | USDC |
| 43114 (Avalanche) | `0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48A6E` | USDC |
| 56 (BSC) | `0xcE24439F2D9C6a2289F741120FE202248B666666` | $U |
| 97 (BSC testnet) | `0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565` | $U |

#### Scenario: Map is complete and resolvable

- GIVEN the shipped config
- WHEN each rail-map chain is inspected
- THEN chain 8453, 137, 43114 each map to their USDC address with a non-empty `rpc_url` and EIP-712 `token_name`/`token_version` facts, and chains 56 and 97 map to their pinned `$U` address
- THEN the map contains no other settlement chains in v1

### R2: get_token_config returns the rail-map token for the quoted chain

`get_token_config(settings, chain_id)` SHALL return the rail-map token for the requested chain: `address` from the map's `token_address`, and `name`/`version` from the map's token facts. For the `$U` chains the name SHALL remain "United Stables" and version "1"; for the USDC chains the returned name/version SHALL be the EIP-712 domain facts of the mapped USDC contract, so signature recovery against that domain succeeds.

#### Scenario: USDC chain requested

- GIVEN `settings` with the shipped rail map and `chain_id` = 8453
- WHEN `get_token_config(settings, 8453)` is called
- THEN it returns a `TokenConfig` whose `address` equals `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` and whose `name`/`version` are the map's USDC domain facts for Base

#### Scenario: $U chain requested

- GIVEN `chain_id` = 97 (or 56)
- WHEN `get_token_config(settings, chain_id)` is called
- THEN it returns the pinned `$U` address for that chain with name "United Stables" and version "1"

### R3: get_token_config raises on an unknown chain

`get_token_config(settings, chain_id)` SHALL raise when `chain_id` is not present in the rail map. Callers of the hire-offer path SHALL catch that error and render the disabled "not available" state rather than guessing a price.

#### Scenario: Chain outside the map

- GIVEN a chain id that is not in the rail map (e.g. a Solana id, SKALE, or a sepolia/testnet id such as 84532)
- WHEN `get_token_config(settings, chain_id)` is called
- THEN it raises, and the hire-offer caller catches the error and renders "not available"

### R4: create_hire charges the offer's accepts[0] and records evidence

`create_hire` SHALL probe the agent's endpoint at create time and, when a supported offer is available, build the hire and its challenge from the offer's `accepts[0]`: `pay_to` from `offer.pay_to`, `amount_wei` from the offer's quoted amount (no flat USD price, no re-derivation from a configured price), and the challenge's `network`/`asset`/EIP-712 facts from the offer's `network` and the rail-map token for that network. The `HiredAgent` row SHALL record `amount_agent`, `pay_to_agent`, `asset_agent`, and `network_agent` from the offer, and SHALL persist the payment fields (`amount`, `token`, `pay_to`) reflecting the offer so `pay_hire` can re-derive chain and token. Fee parameters SHALL be removed from the `create_hire` → challenge-build call.

#### Scenario: Supported offer at create time

- GIVEN the agent probe returns an offer whose network is a rail-map chain and whose asset equals that chain's map token
- WHEN `create_hire` runs for that agent
- THEN the pending hire's `pay_to`/`amount` come from the offer, `token` and challenge `asset`/`network`/domain facts come from the offer's chain via the rail map, and `amount_agent`/`pay_to_agent`/`asset_agent`/`network_agent` equal the offer's quoted values

#### Scenario: No supported offer at create time

- GIVEN the probe yields no offer, or the offer's network/asset is unsupported
- WHEN `create_hire` runs
- THEN it creates no hire and answers an error (the "not available" state), never falling back to the flat `x402_default_price_usd`

### R5: The hire challenge contains exactly one accept — no fee accept

The hire challenge SHALL contain exactly one `accept` (`accepts[0]`); the challenge builder SHALL NOT append a second marketplace-fee accept for the hire flow (fee parameters removed/ignored). `challenge.accepts.length` SHALL equal 1 for every hire created by this change.

#### Scenario: Single-accept challenge

- GIVEN a supported offer is used to create a hire
- WHEN the hire challenge is built
- THEN `challenge.accepts` has length 1, its sole accept carries the offer's `payTo`, quoted `amount`, and the chain/token facts of the offer's network, and no fee wallet or fee amount appears anywhere in the challenge

### R6: pay_hire verifies and broadcasts on the hire's chain via the rail map, with no fee

`pay_hire` SHALL derive the settlement chain from the hire row's offer data (the `eip155:<chain_id>` network recorded at create time), SHALL obtain the token config and RPC URL for that chain from the rail map, SHALL verify the decoded payment against that chain, token, `pay_to`, and quoted amount, and SHALL broadcast exactly one settlement. It SHALL NOT verify or broadcast any fee, and SHALL ignore a legacy `payload.fee` if one is present in the envelope.

#### Scenario: Multi-chain settlement

- GIVEN a hire created from a Base 8453 USDC offer and an envelope signed on chain 8453 with the quoted amount to the offer's wallet
- WHEN `pay_hire` runs
- THEN it verifies `chain_id` 8453 and the rail-map USDC token, verifies payTo/amount/window/signer, and broadcasts the single authorization via the rail-map RPC for 8453, marking the hire paid with the receipt hash

#### Scenario: Legacy fee payload is ignored

- GIVEN an envelope that also carries a well-formed legacy `payload.fee`
- WHEN `pay_hire` runs against a matching hire
- THEN the fee is neither verified nor broadcast and does not affect the outcome; the principal payment alone is verified and broadcast

#### Scenario: Wrong chain is rejected visibly

- GIVEN an envelope signed on a chain different from the hire's recorded network, or for a token that is not the rail-map token of that chain
- WHEN `pay_hire` runs
- THEN verification raises (wrong chain/token) and the hire is not paid

### R7: payment.js signs one authorization with no fee payload

`payment.js` SHALL sign exactly one EIP-712 `TransferWithAuthorization` derived from `challenge.accepts[0]`, SHALL set `domain.chainId` from `accept.network` (the `eip155:<chain_id>` segment), and SHALL build an envelope whose `payload` carries only `signature` and `authorization`. It SHALL NOT sign or attach any second/fee authorization, and `payload.fee` SHALL never be present in the envelope.

#### Scenario: One authorization signed

- GIVEN the hire API returns a single-accept challenge
- WHEN the user confirms the hire in the browser
- THEN exactly one `signTypedData` call is made over `accepts[0]` with `domain.chainId` equal to the network's chain id, and the posted envelope's `payload` contains no `fee` key and a single `authorization`

### R8: Hire-offer UI enables rail-map EVM chains; "not available" otherwise

The hire-offer partial SHALL render an enabled Hire control showing the agent's real quoted price when the probed offer's network is a rail-map chain and its asset is that chain's map token (Base/Polygon/Avalanche USDC and BSC `$U`). It SHALL render the disabled "not available" state for no offer, an unreachable endpoint, or an offer whose network/asset is not in the rail map (e.g. Solana, non-EVM, unknown chains). The rendered price SHALL be the agent's quoted price with no marketplace-fee line.

#### Scenario: Rail-map EVM offer

- GIVEN an offer whose network is `eip155:8453` (or 137/43114/56/97) and whose asset matches the map token for that chain
- WHEN the hire-offer partial renders
- THEN it shows the agent's quoted price on an enabled Hire control with no fee text

#### Scenario: Unsupported offer

- GIVEN an offer on Solana, a non-EVM network, or a chain outside the rail map
- WHEN the hire-offer partial renders
- THEN it shows the disabled "not available" state

### R9: decode_envelope keeps parsing legacy payload.fee

`decode_envelope` SHALL continue to parse a legacy `payload.fee` (same-payer EIP-3009 authorization) into `DecodedPayment.fee` for back-compat with old envelopes, and SHALL keep raising `InvalidEnvelope` when a present `payload.fee` is malformed. The hire/pay flow SHALL ignore the parsed fee (R6); no new code SHALL produce a fee payload.

#### Scenario: Legacy envelope still decodes

- GIVEN a base64 envelope that carries a well-formed `payload.fee` signed by the same payer
- WHEN `decode_envelope` parses it
- THEN it returns a `DecodedPayment` whose `fee` is populated with the fee authorization and signature

#### Scenario: Fee-free envelope

- GIVEN an envelope whose payload has no `fee` key
- WHEN `decode_envelope` parses it
- THEN it returns a `DecodedPayment` with `fee` equal to `None`

### R10: Full suite green under uv run pytest

The test suite SHALL pass end-to-end with `uv run pytest`. All fee-era assertions SHALL be migrated: no test asserts a second accept, a fee signature, a fee payload, or a fee verification/broadcast in the hire flow; tests SHALL cover the rail-map token resolution (R1–R3), single-accept challenges and offer-driven `create_hire` evidence (R4–R5), multi-chain verify/broadcast with a fake broadcaster (R6), the fee-free envelope (R7), UI enabled/disabled states (R8), and legacy `payload.fee` decoding (R9).

#### Scenario: Baseline plus new behavior

- GIVEN the implemented change and its tests
- WHEN `uv run pytest` runs from the repository root
- THEN the full suite passes (baseline 446 preserved and extended) with no fee-creation assertions remaining

## Out of Scope

- Solana / non-EVM settlement; dynamic or per-provider RPC management; real USDC balance checks or swap/facilitator changes.
- Retaining any configurable/flagged marketplace-fee path for the hire flow.
- New hire schema columns, migrations, offer scheduling, or offline offer caching.
- Updating the canonical `openspec/specs/x402-agent-hire/spec.md` fee-on-top and single-rail requirements (handled at archive time as a delta against this change).
