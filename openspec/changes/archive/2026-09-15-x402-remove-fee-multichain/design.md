# Design — x402-remove-fee-multichain

**Change:** `x402-remove-fee-multichain` · **Phase:** design
**Branch:** `feat/x402-multichain-no-fee`
**Domain:** payments / agent-hire (extends `x402-real-payment` + `x402-agent-hire`)

Implements the spec's hard-constrained decisions without relitigating them: the
marketplace fee is removed end-to-end (`create_hire` / `pay_hire` / `payment.js`),
and settlement becomes multi-chain EVM through a **static rail map**
(`chain_id → {rpc_url, token_address, token_name, token_version}`) for Base 8453,
Polygon 137, Avalanche 43114 (USDC) plus BSC 56 / testnet 97 (`$U`). `create_hire`
charges the agent's probed offer (never a flat price); `pay_hire` settles on the
hire's recorded chain via the rail map. `decode_envelope` keeps parsing a legacy
`payload.fee` for back-compat, but the pay flow ignores it.

---

## 1. Architecture overview

```
create_hire (POST /api/hires)
  ├─ probe_agent_offer(endpoint)            # one probe at create time, never raises
  │     └─ 402 header → AgentOffer{pay_to, amount_wei, asset, network}
  ├─ is_supported_offer(offer, settings)    # rail-map: network in map AND asset==map token
  │     └─ False → raise AgentOfferUnavailable (503) — NO flat fallback
  ├─ get_token_config(settings, chain_id)   # rail-map token; unknown → UnknownRail → caught → 503
  ├─ HiredAgent row: amount=offer.amount_wei (wei, token-agnostic), token=rail.address,
  │     pay_to=offer.pay_to, evidence cols set
  └─ build_challenge(pay_to, resource, amount_wei=offer.amount_wei, chain_id)
        └─ exactly ONE accept (accepts[0]); fee params removed

pay_hire (POST /api/hires/{id}/pay)
  ├─ network=hire.network_agent → chain_id = eip155:<N>
  ├─ token_cfg = get_token_config(settings, chain_id)      # rail-map token facts
  ├─ rpc_url   = settings.x402_rail_for(chain_id).rpc_url  # rail-map RPC
  ├─ verify_payment(decoded, chain_id, token_cfg, pay_to, amount_wei=int(hire.amount), ...)
  │     # NO fee verify; decoded.fee IGNORED entirely
  └─ broadcaster.broadcast(decoded, token_cfg, facilitator_key, rpc_url, now)
        # NO fee broadcast — exactly one settlement; receipt marks hire paid
```

**What the rail map contains** (per chain): the chain id, a public JSON-RPC URL,
the token address the agent quotes on that chain, and the token's EIP-712
`name`/`version` facts so `verify_payment`'s `_recover_signer` recovers the payer
against the *correct* typed-data domain. Wrong facts fail signature recovery
visibly (403), not silently.

**Decisions resolved from the spec phase:**
- **USDC 6 vs $U 18 decimals (Q1):** `offer.amount_wei` is the agent's quoted wei;
  `HiredAgent.amount` stores it **as-is** (token-agnostic). No `*10**18` on the
  offer path and none in `verify_payment` (`decoded.amount == int(hire.amount)`).
  `price_usd` (`amount_wei/10**18`) is display-only. `_WEI_PER_UNIT` in `hires.py`
  is removed from the offer path.
- **No-offer / unsupported (Q2):** `create_hire` answers `503 agent_offer_unavailable`
  when there is no offer (endpoint down / no 402) AND when the offer's
  network/asset is unsupported. **No flat fallback** ("never a guessed price").
- **EIP-712 domain (Q3):** the map pins `token_name`/`token_version` per chain.
  USDC chains use `"USD Coin"`/`"2"` (FiatTokenV2); MUST be verified against each
  live `DOMAIN_SEPARATOR()`/`name()`/`version()` before the fixture freeze. A test
  freezes `get_token_config` JSON per chain.
- **Legacy fee in decode (Q4):** `decode_envelope` keeps parsing `payload.fee`;
  `pay_hire` ignores `decoded.fee` completely.

---

## 2. Rail map in config — Option A (constant dict)

Picked **Option A**: a module-level constant dict in `app/config.py` + a
`Settings` method `x402_rail_for(chain_id)`. The map is static and shipped in
code (v1 does not manage dynamic/per-provider RPC); a JSON file adds parsing for
no benefit.

```python
@dataclass(frozen=True)
class Rail:
    chain_id: int; rpc_url: str; token_address: str; token_name: str; token_version: str

X402_RAIL_MAP: Final[dict[int, Rail]] = {
    8453:  Rail(8453,  "https://mainnet.base.org",
                "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "USD Coin", "2"),
    137:   Rail(137,   "https://polygon-rpc.com",
                "0x3c499c542cEF5E3811e1792ce70d8cC03d50c3359", "USD Coin", "2"),
    43114: Rail(43114, "https://api.avax.network/ext/bc/C/rpc",
                "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48A6E", "USD Coin", "2"),
    56:    Rail(56,    "https://bsc-dataseed.bnbchain.org",
                "0xcE24439F2D9C6a2289F741120FE202248B666666", U_TOKEN_NAME, U_TOKEN_VERSION),
    97:    Rail(97,    "https://bsc-testnet-rpc.publicnode.com",
                "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565", U_TOKEN_NAME, U_TOKEN_VERSION),
}
```

`$U` chains reuse `U_TOKEN_NAME`/`U_TOKEN_VERSION` (`"United Stables"`/`"1"`), so
56/97 addresses/RPCs stay unchanged and the map is the single source of truth.

```python
# on Settings
def x402_rail_for(self, chain_id: int) -> Rail | None:
    """Rail-map lookup; None for chains the marketplace cannot settle."""
    return X402_RAIL_MAP.get(chain_id)
```

`x402_rail_for` returns `None` (not raise) for the *support-check* path
(`is_supported_offer`, UI render); the *strict* path (`get_token_config`) raises.

---

## 3. `get_token_config` rewrite (map-driven)

`app/services/payment.py` becomes map-driven:

```python
def get_token_config(settings: Any, chain_id: int) -> TokenConfig:
    rail = settings.x402_rail_for(chain_id)
    if rail is None:
        raise UnknownRail(f"chain eip155:{chain_id} has no settlement rail")
    return TokenConfig(address=rail.token_address, name=rail.token_name,
                       version=rail.token_version)
```

- `TokenConfig` is unchanged (`address/name/version`).
- The `U_TOKEN_NAME`/`U_TOKEN_VERSION` imports in `payment.py` are dropped (the
  map carries the facts).
- **New error** `UnknownRail(PaymentError)` (500, `unknown_rail`) added to
  `app/errors.py`; callers that render a user-facing state catch it (§9).

---

## 4. `create_hire` flow (offer-driven, no fee)

`app/routers/hires.py::create_hire` loses the flat-price and fee branches:

```python
endpoint = agent.a2a_endpoint or agent.agent_url
offer = await probe_agent_offer(endpoint) if endpoint else None
if not (offer and is_supported_offer(offer, settings)):
    raise AgentOfferUnavailable("agent offer unavailable (down or unsupported chain/asset)")

chain_id = int(offer.network.split(":")[1])         # eip155:<N>
token_cfg = get_token_config(settings, chain_id)    # UnknownRail → caught (§9)

await sweep_expired(db, user)
row = HiredAgent(
    address=user.address, agent_id=payload.agent_id, status=HiredStatus.PENDING,
    amount=Decimal(offer.amount_wei),       # WEI as-is (token-agnostic), NO conversion
    token=token_cfg.address, rail=EIP3009_RAIL, pay_to=offer.pay_to,
    challenge_expiry=now + timedelta(seconds=DEFAULT_TIMEOUT_SECONDS),
    amount_agent=offer.price_usd,           # display-only evidence (units)
    pay_to_agent=offer.pay_to, asset_agent=offer.asset, network_agent=offer.network,
)
db.add(row); await db.flush()
resource_url = f"{str(request.base_url).rstrip('/')}/api/hires/{row.id}"
challenge = build_challenge(offer.pay_to, resource_url,
                            amount_wei=offer.amount_wei,   # quoted wei, no conversion
                            timeout_s=DEFAULT_TIMEOUT_SECONDS,
                            chain_id=chain_id)             # offer's chain, not settings
```

- **No flat fallback:** the `use_offer` branch is gone; every hire is
  offer-driven. `NoPayTo` is unreachable (a supported offer always has `pay_to`)
  but the guard is retained.
- **Evidence columns** are now always populated (no non-offer path).
- `amount` = raw wei; `amount_agent` = `price_usd` (display). Same offer, differ
  by scale — documented in the model comment.

### `build_challenge` (multi-chain + fee-free)

`build_challenge` currently hardcodes the `$U` address/facts and appends an
optional fee accept. New behavior:
- Resolve the accept's token facts from `get_token_config(settings, chain_id)`.
- Build **exactly one** accept: `asset` = rail `token_address`,
  `extra.name/version` = rail `token_name/token_version`.
- `fee_pay_to`/`fee_amount_wei` parameters are **removed** from the signature
  (no second accept, no fee validation).

`build_challenge(pay_to, resource_url, *, amount_wei, timeout_s, chain_id)`.

---

## 5. `pay_hire` flow (settle on the hire's chain via rail map, no fee)

`pay_hire` derives the chain from the recorded offer data instead of
`settings.x402_chain_id`:

```python
network = hire.network_agent
if not network:
    raise AgentOfferUnavailable("hire has no settlement network recorded")
chain_id = int(network.split(":")[1])
token_cfg = get_token_config(settings, chain_id)   # UnknownRail → 503 (§9)
rail = settings.x402_rail_for(chain_id)             # non-None after lookup
rpc_url = rail.rpc_url                              # rail-map RPC, not X402_RPC_URL

decoded = decode_envelope(header)
verify_payment(decoded, chain_id=chain_id, token_cfg=token_cfg,
               pay_to=hire.pay_to, amount_wei=int(hire.amount),  # stored wei, direct
               payer=user.address, now=now)                      # NO fee verify

try:
    result = await broadcaster.broadcast(decoded, token_cfg,
        facilitator_key=settings.x402_facilitator_key, rpc_url=rpc_url, now=now)
except BroadcastFailed:
    hire.status = HiredStatus.FAILED; hire.updated_at = now
    await db.commit(); raise

hire.status = HiredStatus.PAID; hire.tx_hash = result.tx_hash; hire.updated_at = now
await db.commit(); await db.refresh(hire)
```

- **Chain source:** `hire.network_agent` (written at create). The old
  `settings.x402_chain_id` / `x402_rpc_url_resolved` single-chain selection is
  gone from pay.
- **Token facts:** from the rail map (address + name/version) so recovery uses
  the correct domain.
- **RPC:** from the rail map. `X402_RPC_URL` / `x402_rpc_url_resolved` are no
  longer consulted by settlement (legacy single-chain override; deprecated).
- **Legacy fee ignored:** `decoded.fee` is never verified nor broadcast; a legacy
  `payload.fee` decodes fine and does not affect the outcome.
- **Wrong chain is visible:** `chain_id` comes from the hire, so an envelope
  signed on a different chain/token fails with `WrongChain`/403 exactly as before.

---

## 6. `payment.js` diff (one authorization, no fee)

Minimal — remove the fee-signing block. The domain already derives `chainId`
from `accept.network`:

```js
var accept = challenge.accepts[0];
// REMOVED: var feeAccept = challenge.accepts.length > 1 ? challenge.accepts[1] : null;
var domain = {
  name: accept.extra.name,                 // rail token_name (already correct)
  version: accept.extra.version,           // rail token_version
  chainId: parseInt(accept.network.split(":")[1], 10),  // unchanged, correct
  verifyingContract: accept.asset,
};
// ... signPayment(accept) unchanged ...
var main = await signPayment(accept);
var payload = { signature: main.signature, authorization: main.authorization };
// REMOVED the `if (feeAccept) { ... payload.fee = {...} }` block + fee comment
```

Net effect: exactly **one** `signTypedData` over `accepts[0]`; `payload` carries
only `signature` + `authorization` — no `fee` key.

---

## 7. `is_supported_offer` + UI (rail-map aware, no fee line)

### `is_supported_offer` (app/services/x402_client.py)

Rewrite from single-chain to rail-map aware:

```python
def is_supported_offer(offer: AgentOffer, settings: Any) -> bool:
    m = re.match(r"^eip155:(\d+)$", offer.network or "")
    if not m:
        return False
    rail = settings.x402_rail_for(int(m.group(1)))
    return rail is not None and offer.asset.lower() == rail.token_address.lower()
```

Base/Polygon/Avalanche USDC and BSC `$U` offers resolve; Solana / non-EVM /
out-of-map chains return `False` (→ "not available").

### UI (`pages.py::_build_hire_offer` + `hire_offer.html`)

- `_build_hire_offer`: remove `fee = settings.x402_fee_amount_usd if (...)` and
  `total = price_usd + fee`; set `price_usd = float(offer.price_usd)`,
  `fee_usd = None`; the enabled branch fires whenever `is_supported_offer` is
  True (any rail-map chain), not just the configured `$U` chain.
- `hire_offer.html`: delete the `<small>Includes $X marketplace fee</small>` line.
  `HireOffer.fee_usd` stays in the schema (nullable) but is always `None` and the
  partial no longer renders it.

---

## 8. Rail map table

| chain_id | chain | token | token_address | name | version | rpc_url |
|---|---|---|---|---|---|---|
| 8453 | Base | USDC | `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` | USD Coin | 2 | `https://mainnet.base.org` |
| 137 | Polygon | USDC | `0x3c499c542cEF5E3811e1792ce70d8cC03d50c3359` | USD Coin | 2 | `https://polygon-rpc.com` |
| 43114 | Avalanche | USDC | `0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48A6E` | USD Coin | 2 | `https://api.avax.network/ext/bc/C/rpc` |
| 56 | BSC | $U | `0xcE24439F2D9C6a2289F741120FE202248B666666` | United Stables | 1 | `https://bsc-dataseed.bnbchain.org` |
| 97 | BSC testnet | $U | `0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565` | United Stables | 1 | `https://bsc-testnet-rpc.publicnode.com` |

No other settlement chains in v1 (R1). USDC `name`/`version` facts must be
verified against the live contracts at implementation time before the freeze.

---

## 9. Error / status table

| Scenario | Where | Error | HTTP | code |
|---|---|---|---|---|
| No offer (endpoint down / no 402) | create_hire | `AgentOfferUnavailable` (new) | 503 | `agent_offer_unavailable` |
| Offer exists, network/asset unsupported | create_hire | `AgentOfferUnavailable` | 503 | `agent_offer_unavailable` |
| Offer chain not in rail map | create_hire (`UnknownRail` caught) | `AgentOfferUnavailable` | 503 | `agent_offer_unavailable` |
| Direct `get_token_config`, unknown chain | payment.py | `UnknownRail` (new) | 500 | `unknown_rail` |
| Hire has no `network_agent` | pay_hire | `AgentOfferUnavailable` | 503 | `agent_offer_unavailable` |
| Hire chain no longer in rail map | pay_hire (`UnknownRail` caught) | `AgentOfferUnavailable` | 503 | `agent_offer_unavailable` |
| RPC down / revert at settlement | pay_hire | `BroadcastFailed` | 503 | `payment_broadcast_failed` |
| Envelope wrong chain/token | pay_hire | `WrongChain` | 403 | `payment_wrong_chain` |
| Amount ≠ stored wei | pay_hire | `AmountMismatch` | 403 | `payment_amount_mismatch` |

`UnknownRail` and `AgentOfferUnavailable` are new `PaymentError` subclasses
registered in the `errors.py` handler tuple. `create_hire`/`pay_hire` catch
`UnknownRail` and re-raise `AgentOfferUnavailable` for a consistent 503
"cannot settle this hire" instead of a raw 500.

---

## 10. Test strategy

Strict TDD, RED first. Full suite offline (FakeBroadcaster + locally-signed
envelopes; rail-map RPCs never touched).

### New `tests/test_payment_rail_map.py`
- **Map shape (R1):** `X402_RAIL_MAP` keys exactly `{8453,137,43114,56,97}`; each
  has non-empty `rpc_url` + pinned address; no other chains.
- **`get_token_config` per chain (AC-1):** 8453/137/43114 → USDC address +
  `("USD Coin","2")`; 56/97 → `$U` + `("United Stables","1")`.
- **Frozen JSON fixture:** `json.dumps([get_token_config(settings,c) for c in
  sorted(map)])` equals a pinned literal (locks domain facts, Q3).
- **Unknown chain raises:** `get_token_config(settings, 84532)` raises
  `UnknownRail`.

### Extend `tests/test_payment.py`
- **`build_challenge` single-accept (AC-2/R5):** for a Base offer,
  `len(accepts)==1`; `accepts[0]` carries offer `payTo`, quoted `amount`,
  `network=="eip155:8453"`, `asset`=Base USDC, `extra=={"name":"USD Coin",
  "version":"2","assetTransferMethod":"eip3009"}`; no `fee_*` kwargs exist.
- Keep the bad-inputs parametrized test; delete/replace the two fee tests
  (`test_challenge_with_fee_two_accepts` / `..._rejects_bad_wallet_or_amount`).
- **`decode_envelope` fee back-compat (R9):** keep `test_decode_envelope_with_fee`
  and the wrong-payer rejection — decode still parses `payload.fee`; these do not
  touch the pay flow.
- **`verify_payment` per chain:** signed envelope on 8453 with Base USDC facts
  verifies against `get_token_config(settings,8453)`; wrong chain/token raises
  `WrongChain`.

### Extend `tests/test_api_hires.py`
- **Offer-driven create (AC-3/AC-4/R4):** probe returns a Base USDC offer
  (`payai_header` rewritten to `eip155:8453` + Base USDC); assert
  `accepts[0]` from the offer, `len(accepts)==1` (no fee), evidence columns
  persisted and echoed.
- **No-offer → 503 (R4/Q2):** endpoint down/absent → `503 agent_offer_unavailable`,
  `challenge is None`, no flat price. Rewrites
  `test_create_hire_falls_back_to_flat_without_offer` / `..._when_probe_unreachable`.
- **Unsupported offer → 503:** offer on `eip155:84532` (raw PayAI fixture) →
  `503 agent_offer_unavailable`.
- **`test_hire_happy_returns_pending`:** seed a supported offer (no flat path);
  assert `amount` echoes the quoted wei.

### Extend `tests/test_api_hires_pay.py`
- **Settle on offer chain (AC-5/R6):** create from a Base USDC offer, sign on
  8453, pay → `paid`; `FakeBroadcaster.calls[0]["rpc_url"]` == Base rail RPC,
  `token_cfg` == Base USDC, **exactly one** broadcast.
- **Legacy fee ignored (R6):** attach a well-formed `payload.fee` → still `paid`,
  one broadcast, fee wallet never in calls.
- **Wrong chain rejected (R6):** envelope signed on 137 while hire is on 8453 →
  `403 payment_wrong_chain`, zero broadcasts.
- Remove `test_pay_with_marketplace_fee` and `test_pay_fee_without_fee_wallet_config`
  (fee path gone; superseded by legacy-fee-ignored test).

### UI tests (`test_hire_offer_endpoint.py`, `test_pages_x402.py`)
- **Rail-map EVM offer enabled (R8):** Base/Polygon/Avalanche USDC renders
  `Hire for $<price>` enabled, **no** "marketplace fee" text.
- **Unsupported disabled (R8):** Solana / `eip155:84532` / non-EVM → "Not
  available", `data-has-offer="false"`.
- **`is_supported_offer` multi-chain** (in `test_x402_client.py`): true per
  rail-map chain with its token; false for wrong asset / out-of-map / non-`eip155`.
- Remove the "marketplace fee" assertion from `test_hire_offer_renders_real_price`.

### JS
**No JS test infrastructure** in the repo (no `*.test.js`/`*.spec.js`, no
node/jest runner — verified). The `payment.js` change is **manual review**; its
contract (one accept, no fee payload) is covered indirectly by API tests asserting
`challenge.accepts.length == 1` for every hire (R5/AC-2/AC-6). No new JS tooling.

### Config tests (`tests/test_config_x402.py`)
- Add rail-map resolution: `settings.x402_rail_for(8453).rpc_url/.token_address`
  resolve; `x402_rail_for(84532)` is `None`. Existing default/mainnet/override
  tests stay (§11).

---

## 11. Rollout / files changed

Read-only on production code; these are intended edits for the tasks phase.

- **`app/config.py`** — add `Rail`, `X402_RAIL_MAP`, `Settings.x402_rail_for`.
  Retain deprecated `x402_default_price_usd`/`x402_fee_wallet`/`x402_fee_amount_usd`
  and `_X402_RPC_DEFAULTS`/`x402_rpc_url_resolved` (still covered by
  `test_config_x402`; no longer consulted by hire/pay — a follow-up may remove).
- **`app/errors.py`** — add `UnknownRail` + `AgentOfferUnavailable`; register both.
- **`app/services/payment.py`** — rewrite `get_token_config` (map + raise) and
  `build_challenge` (rail facts, single accept, drop fee params); drop unused
  `U_TOKEN_NAME/VERSION` imports.
- **`app/services/x402_client.py`** — rewrite `is_supported_offer` (rail-map).
- **`app/routers/hires.py`** — rewrite `create_hire` (offer-driven, no flat/fee,
  catch `UnknownRail`) and `pay_hire` (chain from `network_agent`, rail
  RPC/token, no fee); drop `_WEI_PER_UNIT` on the offer path.
- **`app/routers/pages.py`** — `_build_hire_offer`: remove fee math.
- **`app/templates/partials/hire_offer.html`** — remove the fee `<small>` line.
- **`app/static/js/payment.js`** — remove feeAccept + `payload.fee` block.
- **Tests** — new `test_payment_rail_map.py`; extend/rewrite `test_payment.py`,
  `test_api_hires.py`, `test_api_hires_pay.py`, `test_hire_offer_endpoint.py`,
  `test_x402_client.py`, `test_config_x402.py`.

Rollout order: config/errors → payment service → x402_client → hires router →
pages/partial/payment.js → tests. No DB migration (evidence columns exist;
`amount` semantics change but the column is reused).

---

## 12. Risks

- **USDC name/version mismatch:** if pinned `token_name`/`token_version` do not
  match a live contract's EIP-712 domain, recovery fails (visible 403). Mitigated
  by the frozen-`get_token_config` fixture + a required live
  `DOMAIN_SEPARATOR()` check at implementation.
- **Multi-chain RPC reliability:** static public RPCs may be slow/rate-limited.
  Mitigated by v1 static map + per-call timeout; a failed broadcast flips the hire
  to `failed` (existing `BroadcastFailed` path).
- **`amount` semantics change:** the column now stores raw wei (token-agnostic)
  instead of `$U` units; consumers of `HireOut.amount` treating it as dollars will
  misread it. Documented; display price comes from the probe's `price_usd`.
- **Removing flat fallback breaks non-offer agents:** they can no longer be hired
  (503). Explicit user decision (no guessed price); UI already shows "not
  available".
- **Legacy envelopes with `payload.fee`:** still decode (back-compat) but the fee
  is not settled. Acceptable per spec R9.

---

## 13. Acceptance criteria mapping

| AC | Where satisfied |
|---|---|
| AC-1 per-chain token config | §3 + `test_payment_rail_map.py` |
| AC-2 no fee accept | §4/§5 + `build_challenge` single accept |
| AC-3 quoted price charged | §4 + `test_api_hires.py` |
| AC-4 evidence persisted | §4 row columns + tests |
| AC-5 settle on offer's chain | §5 + `test_api_hires_pay.py` |
| AC-6 single signature | §6 payment.js + R5 length-1 contract tests |
| AC-7 full suite green | §10 (baseline 446 preserved, fee assertions migrated) |
