# Change: x402-remove-fee-multichain

## Change name

`x402-remove-fee-multichain`

## Domain

Payments / agent hire (extends `x402-agent-hire` + `x402-real-payment`)

## Status

Draft

---

## Problem

Two legacy marketplace behaviors are now wrong for the agents this marketplace
actually indexes:

1. **The marketplace fee is appended to every hire.** `build_challenge` adds a
   second `accept` (model-A commission) to `X402_FEE_WALLET`, and `payment.js`
   makes the user sign two authorizations. The user decided: **remove it**. The
   user pays exactly what the agent quotes, nothing more.
2. **Single-rail settlement.** `get_token_config()` always returns the settings'
   `$U` token, and `x402_rpc_url_resolved` always uses ONE configured RPC/chain.
   Production agent ClawdMint (56/2468) quotes **8 networks** (Base, Polygon,
   Avalanche, SKALE, Solana…), all USDC. The current code says "not available"
   for every one of them. The user decided: **pay on whatever EVM chain the agent
   accepts** (USDC / any eip3009 token), static chain→RPC/token map.

## Existing evidence

- `app/services/payment.py`:
  - `build_challenge(pay_to, resource_url, *, amount_wei, timeout_s, chain_id,
    fee_pay_to=None, fee_amount_wei=None)` — fee as `accepts[1]`.
  - `get_token_config(settings, chain_id)` → always `settings.x402_u_token_address`.
  - `verify_payment(...)` already validates `decoded.chain_id == chain_id`,
    `decoded.token == token_cfg.address`, amount, payTo, window, signer —
    **chain/token aware by construction**; only the *caller* passes the wrong
    single-chain config today.
  - `OnchainBroadcaster.broadcast(... rpc_url, token_cfg, ...)` — takes any RPC.
- `app/routers/hires.py::create_hire` — flat `x402_default_price_usd` + fee;
  `pay_hire` — verifies + broadcasts fee first, then principal.
- `app/static/js/payment.js` — signs `accepts[0]` + optional `feeAccept`.
- `app/services/x402_client.py::probe_agent_offer` — parses the agent's real
  challenge (verified live: ClawdMint, USDC eip155:8453, 1000 wei, wallet
  `0x75b5…`).
- `app/config.py` — `x402_u_token_address_56/97`, `x402_fee_wallet`,
  `x402_fee_amount_usd`, `x402_chain_id`, `x402_rpc_url`.

## What needs to change

1. **Remove the fee from create_hire/build_challenge/payment.js/pay_hire.**
   `payload.fee` handling stays in decode (back-compat for old envelopes) but
   the hire flow never creates one.
2. **Static multi-chain rail map** (config): `chain_id -> {rpc_url, usdc}`
   for the EVM chains ClawdMint quotes (Base 8453, Polygon 137, Avalanche 43114,
   SKALE 1482601649/11155111 equivalents). Keep `$U` chains too (56/97) for
   compatibility.
3. **`get_token_config(settings, chain_id)` → use the map**: address from the
   rail map for the quoted chain, name/version `$U` or `USDC` per chain.
4. **create_hire**: probe the agent offer at create time; use its real
   `accepts[0]` (payTo, amount, asset, network) instead of the flat price.
   Record `amount_agent/pay_to_agent/asset_agent/network_agent` (evidence columns
   already added by x402-agent-hire).
5. **UI**: the hire-offer partial renders the agent's real price (already done);
   if the agent is EVM-compatible (any chain in the rail map), show the price +
   enabled hire.
6. **Tests**: update all fee assertions (flat + fee) → agent-quoted price only.

## Scope

**In**: remove fee; static multi-chain rail map; create_hire uses agent offer;
get_token_config per chain; UI reflects multi-chain; tests.

**Out**: Solana/non-EVM settlement (still "not available"); dynamic RPC config
(static map v1); real USDC balance checks; swap/facilitator changes.

## Acceptance criteria

1. AC-1 — `get_token_config(settings, chain_id)` returns the rail-map token for
   Base/Polygon/Avalanche (USDC) and $U on 56/97.
2. AC-2 — `build_challenge` no longer appends a fee accept when called with the
   new hire flow (fee params removed/ignored).
3. AC-3 — `create_hire` charges the agent's probed price (amount, payTo, asset,
   network from the offer), not the flat price.
4. AC-4 — `HiredAgent` evidence columns populated from the offer.
5. AC-5 — `pay_hire` verifies + broadcasts against the chain from the offer
   (rpc/token from rail map), no fee broadcast.
6. AC-6 — `payment.js` signs one authorization only (no fee payload).
7. AC-7 — Full pytest baseline preserved (446 passed at branch point).

## Risks

- Multi-chain RPC reliability → static public RPCs for v1, per-chain timeout.
- USDC ≠ $U semantics → name/version from the map must match the EIP-712 domain
  of the quoted token; mismatch fails signature verification (visible, not silent).
- Removing fee changes revenue model → explicit user decision; fee code paths
  removed, not just bypassed.
- Old envelopes with `payload.fee` → decode still accepts for back-compat, pay
  flow ignores it.