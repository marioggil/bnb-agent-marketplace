# Tasks — x402-remove-fee-multichain

Strict TDD (RED → GREEN → TRIANGULATE → REFACTOR), read-only on production code; this file is the only artifact written here. Branch `feat/x402-multichain-no-fee`. Baseline: 446 passing.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~900–1200 (additions + deletions) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (rail map + token config + errors) → PR 2 (backend create/pay, fee removal) → PR 3 (JS + UI + test migration) |
| Delivery strategy | ask-on-risk |
| Chain strategy | feature-branch-chain |

```text
Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: High
```

Implementation touches 8 production files, 6+ test files, and a frozen-fixture contract, and removes a parallel fee code path — well over the 400-line budget. Split into three autonomous PRs, each green at its boundary. Apply order: PR1 → PR2 → PR3.

---

## PR 1 — Rail map + per-chain token config (Phase 1)

### T1 — RED: rail-map shape test
- [x] Add `tests/test_payment_rail_map.py::test_rail_map_has_evm_and_uo_chains`: assert `X402_RAIL_MAP` keys `== {8453, 137, 43114, 56, 97}`, each `Rail` has non-empty `rpc_url`/`token_address`/`token_name`/`token_version`, and USDC chains 8453/137/43114 pin addresses `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`, `0x3c499c542cEF5E3811e1792ce70d8cC03d50c3359`, `0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48A6E`. Run RED (fails: no `X402_RAIL_MAP`). <!-- sdd-owner: implementation -->
- [x] Implement and verify the behavior. <!-- sdd-owner: implementation -->

### T2 — GREEN: rail map constant in config
- [x] Add `@dataclass(frozen=True) Rail` (chain_id, rpc_url, token_address, token_name, token_version) and `X402_RAIL_MAP: Final[dict[int, Rail]]` in `app/config.py` per design §2 (Base `https://mainnet.base.org`, Polygon `https://polygon-rpc.com`, Avalanche `https://api.avax.network/ext/bc/C/rpc`; 56/97 reuse `U_TOKEN_NAME`/`U_TOKEN_VERSION` and `_X402_RPC_DEFAULTS`). Add `Settings.x402_rail_for(chain_id) -> Rail | None` (None for unknown). <!-- sdd-owner: implementation -->
- [x] Implement and verify the behavior. <!-- sdd-owner: implementation -->

### T3 — RED: per-chain token config
- [x] Extend `tests/test_payment_rail_map.py::test_get_token_config_returns_per_chain_token`: `get_token_config(settings, 8453)` → address Base USDC, name `"USD Coin"`, version `"2"`; `get_token_config(settings, 56)` → `$U` address, `"United Stables"`, `"1"`. Run RED. <!-- sdd-owner: implementation -->
- [x] Implement and verify the behavior. <!-- sdd-owner: implementation -->

### T4 — GREEN: map-driven get_token_config
- [x] Rewrite `get_token_config(settings, chain_id)` in `app/services/payment.py` to resolve via `settings.x402_rail_for(chain_id)` and return `TokenConfig(rail.token_address, rail.token_name, rail.token_version)`; raise `UnknownRail` when the rail is None. Drop `U_TOKEN_NAME`/`U_TOKEN_VERSION` imports from `payment.py`. <!-- sdd-owner: implementation -->
- [x] Implement and verify the behavior. <!-- sdd-owner: implementation -->

### T5 — RED: unknown chain raises
- [x] Add `tests/test_payment_rail_map.py::test_get_token_config_unknown_chain_raises`: `get_token_config(settings, 999999)` (and `84532`) raises `UnknownRail`. Run RED. <!-- sdd-owner: implementation -->
- [x] Implement and verify the behavior. <!-- sdd-owner: implementation -->

### T6 — GREEN: UnknownRail error
- [x] Add `UnknownRail(PaymentError)` (500, `unknown_rail`) in `app/errors.py` and register it in the handler tuple in `register_error_handlers`. <!-- sdd-owner: implementation -->
- [x] Implement and verify the behavior. <!-- sdd-owner: implementation -->

### T7 — TRIANGULATE: frozen per-chain facts + x402_rail_for
- [x] Add `tests/test_payment_rail_map.py::test_token_config_frozen_fixture`: `json.dumps([get_token_config(settings, c) for c in sorted(X402_RAIL_MAP)])` equals a pinned literal (locks USDC `"USD Coin"`/`"2"` and `$U` facts). Add `tests/test_config_x402.py` asserting `x402_rail_for(8453).rpc_url`/`.token_address` resolve and `x402_rail_for(84532) is None`. Verify USDC `name()`/`version()` facts against live contracts before freezing. <!-- sdd-owner: implementation -->
- [x] Implement and verify the behavior. <!-- sdd-owner: implementation -->

**PR1 gate:** `uv run pytest tests/test_payment_rail_map.py tests/test_config_x402.py tests/test_payment.py` green; `app/routers/*` untouched.

---

## PR 2 — Backend fee removal + offer-driven create/pay (Phase 2)

### T8 — RED: create_hire uses offer + evidence
- [x] Extend `tests/test_api_hires.py::test_create_hire_probes_offer_and_sets_evidence`: mock probe returns a Base USDC offer (`eip155:8453` + Base USDC address via `payai_header(asset=…, network=…)`); assert challenge has `len(accepts)==1`, `accepts[0]` amount/pay_to/network/asset from the offer, and `amount_agent`/`pay_to_agent`/`asset_agent`/`network_agent` persisted. Run RED. <!-- sdd-owner: implementation -->
- [x] Implement and verify the behavior. <!-- sdd-owner: implementation -->

### T9 — GREEN: offer-driven create_hire
- [x] Rewrite `create_hire` in `app/routers/hires.py`: probe endpoint; when `offer and is_supported_offer(offer, settings)`, use `offer.amount_wei` (raw wei, no `*10**18`), `offer.pay_to`, and `chain_id = int(offer.network.split(":")[1])`; `token_cfg = get_token_config(settings, chain_id)`; drop flat `x402_default_price_usd` and fee args from `build_challenge`. Remove `_WEI_PER_UNIT` from the offer path. Evidence columns always set. <!-- sdd-owner: implementation -->
- [x] Implement and verify the behavior. <!-- sdd-owner: implementation -->

### T10 — RED: no-offer → 503
- [x] Extend `tests/test_api_hires.py::test_create_hire_agent_offer_unavailable_503`: probe returns None (endpoint down) and probe returns unsupported offer (`eip155:84532`) → both `503` `agent_offer_unavailable`, `challenge is None`, no flat price. Replace `test_create_hire_falls_back_to_flat_without_offer` / `..._when_probe_unreachable`. Run RED. <!-- sdd-owner: implementation -->
- [x] Implement and verify the behavior. <!-- sdd-owner: implementation -->

### T11 — GREEN: AgentOfferUnavailable error + handler
- [x] Add `AgentOfferUnavailable(PaymentError)` (503, `agent_offer_unavailable`) in `app/errors.py`; register it. In `create_hire`, raise it when no supported offer, and catch `UnknownRail` → re-raise `AgentOfferUnavailable`. <!-- sdd-owner: implementation -->
- [x] Implement and verify the behavior. <!-- sdd-owner: implementation -->

### T12 — RED: pay_hire settles on hire chain, ignores fee
- [x] Extend `tests/test_api_hires_pay.py::test_pay_hire_verifies_against_hire_chain_and_skips_fee`: create from a Base 8453 USDC offer, sign envelope on 8453 with the quoted amount (optionally including a well-formed legacy `payload.fee`), pay → `paid`; assert `FakeBroadcaster.calls` has exactly one broadcast with `rpc_url` == Base rail RPC and `token_cfg` == Base USDC. Run RED. <!-- sdd-owner: implementation -->
- [x] Implement and verify the behavior. <!-- sdd-owner: implementation -->

### T13 — GREEN: pay_hire chain/token from hire, no fee
- [x] Rewrite `pay_hire` in `app/routers/hires.py`: derive `chain_id` from `hire.network_agent` (`eip155:<N>`; missing → `AgentOfferUnavailable`); `token_cfg = get_token_config(settings, chain_id)` and `rpc_url = settings.x402_rail_for(chain_id).rpc_url`; `verify_payment(..., amount_wei=int(hire.amount))` (stored raw wei, no `*10**18`); remove all `decoded.fee` verify/broadcast; catch `UnknownRail` → `AgentOfferUnavailable`; broadcast exactly once via rail RPC. <!-- sdd-owner: implementation -->
- [x] Implement and verify the behavior. <!-- sdd-owner: implementation -->

### T14 — TRIANGULATE: backend fee-free contract + wrong-chain
- [x] In `tests/test_api_hires_pay.py`: add `test_pay_wrong_chain_rejected` (envelope signed on 137 while hire on 8453 → `403 payment_wrong_chain`, zero broadcasts); add `test_pay_legacy_fee_ignored` (well-formed `payload.fee` → `paid`, one broadcast, fee wallet never in calls). Remove `test_pay_with_marketplace_fee` / `test_pay_fee_without_fee_wallet_config`. In `tests/test_payment.py`, delete `test_challenge_with_fee_two_accepts` / `test_challenge_fee_rejects_bad_wallet_or_amount`; keep `test_decode_envelope_with_fee` + wrong-payer rejection (decode back-compat, R9). <!-- sdd-owner: implementation -->
- [x] Implement and verify the behavior. <!-- sdd-owner: implementation -->

**PR2 gate:** `uv run pytest tests/test_api_hires.py tests/test_api_hires_pay.py tests/test_payment.py tests/test_payment_rail_map.py` green; `app/static/js` and `app/routers/pages.py`/`templates` untouched.

---

## PR 3 — JS single auth + UI multi-chain + full suite (Phases 3–4)

### T15 — RED: UI enabled for rail-map EVM offer
- [x] Extend `tests/test_hire_offer_endpoint.py` / `tests/test_pages_x402.py`: Base 8453, Polygon 137, Avalanche 43114 USDC offers render an enabled `Hire for $<price>` with no "marketplace fee" text; Solana / `eip155:84532` / non-EVM render disabled "Not available". Extend `tests/test_x402_client.py::test_is_supported_offer_multi_chain` (true per rail-map chain with its token; false for wrong asset/out-of-map/non-`eip155`). Run RED. <!-- sdd-owner: implementation -->
- [x] Implement and verify the behavior. <!-- sdd-owner: implementation -->

### T16 — GREEN: rail-map-aware is_supported_offer + UI, no fee line
- [x] Rewrite `is_supported_offer` in `app/services/x402_client.py` to match `^eip155:(\d+)$` and require `x402_rail_for(chain_id)` non-None and `offer.asset.lower() == rail.token_address.lower()`. In `app/routers/pages.py::_build_hire_offer`, remove fee math (`total = price + fee`), set `price_usd = float(offer.price_usd)`, `fee_usd = None`; the enabled branch fires for any rail-map chain. In `app/templates/partials/hire_offer.html`, remove the "Includes $X marketplace fee" `<small>` line. <!-- sdd-owner: implementation -->
- [x] Implement and verify the behavior. <!-- sdd-owner: implementation -->

### T17 — payment.js single authorization
- [x] In `app/static/js/payment.js`: remove `feeAccept` (and its `signPayment(feeAccept)` + `payload.fee` block); sign only `accepts[0]`; keep `domain.chainId = parseInt(accept.network.split(":")[1])`; `payload` carries only `signature` + `authorization` with no `fee` key. No JS test infra exists (verified) — covered by R5 length-1 API tests + manual review of `signTypedData` call count. <!-- sdd-owner: implementation -->
- [x] Implement and verify the behavior. <!-- sdd-owner: implementation -->

### T18 — TRIANGULATE: USDC 6-decimals display-only sanity
- [x] Extend `tests/test_hire_offer_endpoint.py` (or `test_pages_x402.py`) with `test_price_usd_is_probe_estimate_only`: assert `HireOffer.price_usd` equals the probe `price_usd` (display-only) while the persisted `amount` is raw wei; no `*10**18` conversion anywhere on the offer path; `HireCreateOut.amount` echoes quoted wei. <!-- sdd-owner: implementation -->
- [x] Implement and verify the behavior. <!-- sdd-owner: implementation -->

### T19 — REFACTOR/scope guard: full suite green
- [x] Run `uv run pytest` from repo root: baseline 446 preserved and extended; no test asserts a second accept, fee signature, fee payload, or fee verify/broadcast in the hire flow. Verify scope guards: `app/agents.py` untouched; `app/config.py` retains deprecated `x402_default_price_usd`/`x402_fee_*`/`x402_rpc_url_resolved` (still covered by `test_config_x402`); `payment.py` changes limited to `get_token_config` + `build_challenge` (single accept, fee params removed). <!-- sdd-owner: implementation -->
- [x] Implement and verify the behavior. <!-- sdd-owner: implementation -->

---

## Scope / rollback boundaries

- **Rollback per PR:** revert the PR's commit; prior PRs remain green and functional.
- **Read-only:** no production file is modified in this tasks phase — all edits above are implementation instructions for the apply phase.
- **Out of scope (do not do):** Solana/non-EVM settlement; dynamic RPC/failover; real USDC balance checks; new hire columns/migrations; re-adding any fee path; editing `openspec/specs/x402-agent-hire/spec.md` (archived later).
