# Apply Progress: x402-remove-fee-multichain

## Status

`ok` — all 18 tasks implemented. 456 passed, 9 skipped (baseline 446 + 10 new), 0 regressions.

size:exception accepted: ~850 produced vs 400 budget — the change spans 8 production files + 6 test files (user-approved multi-PR scope, done as one branch).

## Implemented (TDD-verified)

### Phase 1 — rail map + token config (T1-T7)

- [x] **T1/T2** — `X402_RAIL_MAP` dict in `app/config.py` with `Rail` dataclass (chain_id, rpc_url, token_address, token_name, token_version):
  - 8453 Base: USDC `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` "USD Coin"/"2", `https://mainnet.base.org`
  - 137 Polygon: USDC `0x3c499c542cEF5E3811e1792ce70d8cC03d50c3359` "USD Coin"/"2", `https://polygon-rpc.com`
  - 43114 Avalanche: USDC `0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48A6E` "USD Coin"/"2", `https://api.avax.network/ext/bc/C/rpc`
  - 56 BSC: $U (existing settings), 97 BSC testnet: $U
  - `x402_rail_for(chain_id)` settings method.
- [x] **T3/T4** — `get_token_config(settings, chain_id)` map-driven (returns Rail's token for the chain).
- [x] **T5/T6** — `UnknownRail` error in `app/errors.py`; `get_token_config` raises it on unknown chain.
- [x] **T7** — rail-map tests assert name/version (USDC "USD Coin"/"2", $U).

### Phase 2 — remove fee from backend (T8-T13)

- [x] **T8/T9** — `create_hire` probes the offer at hire time (a2a_endpoint → agent_url fallback); uses accepts[0] payTo/amount/asset/network; records evidence columns; NO fee params.
- [x] **T10/T11** — `AgentOfferUnavailable` error (no-offer OR unsupported chain → 503-ish); no flat fallback.
- [x] **T12/T13** — `pay_hire` verifies against the hire's chain/token (rail-map RPC per chain), ignores `decoded.fee`; no fee verify/broadcast.
- [x] `get_token_config` raises `UnknownRail` when the offer's chain has no rail.

### Phase 3 — JS single auth (T14)

- [x] **T14** — `payment.js`: removed `feeAccept` declaration, the fee `signPayment` block, and `payload.fee`. Signs ONE authorization; `domain.chainId` parsed from `accept.network`. Verified with `node --check`.

### Phase 4 — UI + final (T15-T18)

- [x] **T15/T16** — `is_supported_offer` uses the rail map (any map chain enabled; Solana/out-of-map disabled); `_build_hire_offer` total = agent's price ONLY (fee removed).
- [x] **T17** — USDC 6-decimals: amount stored in raw wei, token-agnostic; `price_usd` display-only probe estimate.
- [x] **T18** — Full suite green (456), scope guards clean.

## Critical post-apply fixes (found by orchestrator after subagent timeout)

The subagent timed out before writing apply-progress + finishing the JS/pages fee removal. Orchestrator completed:

1. **`payment.js`**: removed the `feeAccept` variable, the `if (feeAccept)` block that signed a fee, and `payload.fee`. Envelope now carries exactly one authorization (`node --check` passes).
2. **`app/templates/partials/hire_offer.html`**: removed the `Includes $X marketplace fee` small line.
3. **`app/routers/pages.py::_build_hire_offer`**: `total = float(offer.price_usd)` (removed `+ float(fee or 0)`); `fee_usd=None` (removed the `float(fee)` population). This was the source of the `test_price_usd_is_probe_estimate_only` failure (rendered $0.03 from a configured fee env despite the offer being 1e-14).

## Files changed

```text
app/config.py                              (X402_RAIL_MAP + x402_rail_for)
app/errors.py                              (+UnknownRail, +AgentOfferUnavailable)
app/services/payment.py                    (get_token_config map-driven)
app/routers/hires.py                       (create_hire offer-driven no-fee; pay_hire chain-aware no-fee)
app/routers/pages.py                       (_build_hire_offer total=fee removed)
app/static/js/payment.js                   (single auth, no fee block)
app/services/x402_client.py                (is_supported_offer rail-map)
tests/test_payment_rail_map.py             (NEW)
tests/test_api_hires.py / test_api_hires_pay.py  (offer-driven, no-fee, chain-aware)
tests/test_hire_offer_endpoint.py          (rail-map chains, price-estimate-only)
tests/test_pages.py                        (CTA)
```

## Test evidence

```text
uv run pytest tests/test_payment_rail_map.py tests/test_api_hires.py tests/test_api_hires_pay.py tests/test_payment.py tests/test_hire_offer_endpoint.py
35 passed

uv run pytest
456 passed, 9 skipped in 21.19s (baseline 446 + 10 new)
```

## Risks (handover)

| # | Risk | Severity | Note |
|---|---|---|---|
| R-1 | USDC EIP-712 name/version must match live `DOMAIN_SEPARATOR()` | medium | "USD Coin"/"2" per FiatTokenV2 convention; tests freeze get_token_config JSON; a mismatch fails signature recovery visibly (403), never silently. |
| R-2 | HiredAgent.amount now raw wei (token-agnostic) | medium | Consumers treating HireOut.amount as dollars will misread 1e-14 wei; display uses price_usd estimate. Explicit user decision. |
| R-3 | Removing flat fallback → non-offer agents 503 | medium | Explicit: no guessed price; agents without a live x402 offer are "not available". |
| R-4 | Static public RPCs for 3 new chains | low | Per-chain timeout; failed broadcast flips hire to failed via existing BroadcastFailed path. |
| R-5 | Legacy `payload.fee` envelopes still parse in decode | low | Back-compat only; pay_hire ignores decoded.fee entirely. |