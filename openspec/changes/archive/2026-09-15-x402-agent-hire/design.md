# Design: x402-agent-hire — price the Hire CTA from the agent's real x402 offer

**Change:** `x402-agent-hire`
**Phase:** Design (SDD fast-track)
**Branch:** `feat/x402-agent-hire`
**Layer:** `packages/coding-agent` → repo `app/` (payments / agent-hire).
**Inputs read:** `openspec/changes/x402-agent-hire/{explore,proposal,spec}.md`; `app/routers/hires.py`, `app/services/payment.py`, `app/routers/pages.py`, `app/db/models/hired_agent.py`, `app/db/models/agent.py`, `app/templates/pages/agent_detail.html` (hire panel ~L590-640), `app/static/js/payment.js`, `app/schemas/hired.py`, `app/config.py`, `migrations/versions/` (head = `0012_compliance_penalty`), `tests/{conftest,test_pages_x402,test_api_hires,test_alembic_check}.py`, `tests/fixtures/b402_challenge.json`.
**Status:** Ready for tasks.

## 1. Overview

Today `create_hire` prices every hire at the flat `X402_DEFAULT_PRICE_USD` ($1.00)
and pays `agent.agent_wallet`, ignoring what the agent's own x402 server quotes.
The agent's endpoint answers `GET` with `HTTP 402` + a `PAYMENT-REQUIRED` header
(base64 JSON v2 challenge whose `accepts[0]` carries the real `payTo`, `amount`
wei, `asset`, `network`) — proven against `x402.payai.network` in explore. This
change makes the marketplace ask the agent, lazily, at the moment the detail page
renders and again at hire creation.

```
 detail page (agent_detail.html)
   │ server-render: <button id="hire-cta">Checking availability…</button>
   │                (data-agent-id / data-agent-url / data-csrf wired; hx-get on button)
   ▼ hx-trigger="load"
 GET /agents/{chain}/{token}/hire-offer          (pages.py router, public)
   │
   ├─ endpoint = a2a_endpoint ‖ agent_url        (a2a first, agent_url fallback)
   │      ├─ none                        → HireOffer{has_offer=False, reason="no_endpoint"}
   │      ├─ probe_agent_offer(url, timeout_s=2.0)
   │      │      ├─ 402 + payment-required → base64→JSON→accepts[0] → AgentOffer
   │      │      │                          (pay_to, amount_wei, asset, network, price_usd)
   │      │      └─ non-402 / timeout / malformed / blocked → None
   │      ├─ offer None                  → HireOffer{has_offer=False, reason="unreachable"}
   │      ├─ offer ∧ ¬is_supported_offer → HireOffer{has_offer=False, reason="unsupported_asset"}
   │      └─ offer ∧ is_supported_offer  → HireOffer{has_offer=True, price_usd=real+fee, …}
   ▼
 partials/hire_offer.html ── hx-swap="innerHTML" into #hire-offer-slot (button label)

 click #hire-cta (payment.js — UNCHANGED)
   ▼
 POST /api/hires  (create_hire)
   │  re-probe same helper (no cache) → offer ∧ supported?
   │    yes → accepts[0] = offer.pay_to / offer.amount_wei;  pay_to = offer.pay_to
   │    no  → accepts[0] = agent.agent_wallet / flat amount  (existing behavior)
   │  fee always → accepts[1] = fee_wallet / fee_amount_wei (model-A commission)
   │  HiredAgent += amount_agent, pay_to_agent, asset_agent, network_agent (evidence)
   ▼
 challenge → payment.js signs accepts[0] + accepts[1] → POST /api/hires/{id}/pay
             (verify/settle path UNCHANGED)
```

## 2. Hard-constrained decisions (carried forward, not relitigated)

| # | Decision | Where it lands |
|---|---|---|
| 1 | Probe source: `a2a_endpoint` first, `agent_url` fallback; both null → no offer | hire-offer endpoint + `create_hire` (§7, §8) |
| 2 | No 402 / timeout / null → disabled "not available" (never a guessed price) | `HireOffer.disabled` + partial (§5, §7) |
| 3 | Lazy probe via HTMX; never blocks first paint | `hx-trigger="load"` (§9) |
| 4 | Marketplace fee adds on top of the real price as `accepts[1]` | `build_challenge(fee_pay_to, fee_amount_wei)` — already supported, reused (§8) |
| 5 | 4 evidence columns on `HiredAgent` via migration | model + `0013_*` (§10) |
| 6 | Price rendered only when offer asset+network match the rail | `is_supported_offer` (§6) |
| 7 | SSRF guard: http/https only, no private/loopback ranges | `_validate_probe_url` (§4) |
| 8 | Strict TDD: tests first, fixtures pinned, baseline preserved | §11 |

## 3. Module surfaces

### 3.1 NEW `app/services/x402_client.py` (service, leaf module)

Pure async probe client + offer value type. No DB, no router imports, no
FastAPI deps — unit-testable against an httpx mock.

```python
@dataclass(frozen=True)
class AgentOffer:
    pay_to: str        # accepts[0].payTo    — 0x address
    amount_wei: int    # accepts[0].amount   — wei, validated numeric
    asset: str         # accepts[0].asset    — token address (case-preserved)
    network: str       # accepts[0].network  — "eip155:<chain_id>"
    price_usd: Decimal # amount_wei / 10**18 — $U units (18 decimals)

async def probe_agent_offer(url: str, *, timeout_s: float = 2.0) -> AgentOffer | None: ...
def is_supported_offer(offer: AgentOffer, settings: Any) -> bool: ...
def _validate_probe_url(url: str) -> httpx.URL: ...   # raises ValueError on block
```

- Module constant `_WEI_PER_UNIT = Decimal(10**18)` mirrors `hires._WEI_PER_UNIT`
  (same value; the service layer must not import from a router, so the constant is
  duplicated locally — documented in the module docstring).
- `_validate_probe_url` returns `httpx.URL`; `probe_agent_offer` calls it first.
- Note: `_WEI_PER_UNIT` in `hires.py` stays untouched (no refactor).

### 3.2 NEW `app/schemas/hire_offer.py`

```python
class HireOffer(BaseModel):
    has_offer: bool
    price_usd: Decimal | None      # TOTAL = agent real price + fee (what the label shows)
    agent_price_usd: Decimal | None
    fee_usd: Decimal | None        # None when no fee wallet configured
    pay_to: str | None
    disabled: bool                 # True for every not-available state
    reason: str | None             # "no_payment_wallet" | "no_endpoint" | "unreachable"
                                   #   | "unsupported_asset" | None
```

Used by the hire-offer endpoint as typed render context (also `model_dump()`-able
so a non-HTMX caller gets the same contract as JSON without extra plumbing).

### 3.3 `app/routers/pages.py` — hire-offer endpoint (same router as the detail page)

`GET /agents/{chain_id}/{token_id}/hire-offer` (public, no auth, no CSRF — GET
fragment). Declared after `/agents/compare` and alongside `/feedbacks`; the extra
path segment cannot collide with `/agents/{chain_id}/{token_id}`.

Endpoint calls a small module-level builder `_build_hire_offer(agent, offer, settings) -> HireOffer`
(§7) and renders `partials/hire_offer.html`, returning the fragment for both
`HX-Request` and plain callers (fragment is safe standalone).

### 3.4 NEW `app/templates/partials/hire_offer.html`

Swap-target content for `#hire-offer-slot`:
- `offer.has_offer` → `Hire for ${{price_usd}}` + `<small>Includes ${{fee_usd}} marketplace fee</small>` (when fee present).
- `offer.disabled` → `Not available` (+ reason hint) and an idempotent inline
  `<script>` that sets `#hire-cta.disabled = true` / `aria-disabled` / `.is-disabled`.
  htmx executes inline scripts in swapped content (default `allowScriptTags=true`).

### 3.5 `app/templates/pages/agent_detail.html` — CTA initial state (~L596-627)

Hireable branch only (`profile.hireable`). The button node is STABLE (never
swapped — see D-8): it keeps today's `data-agent-id` / `data-agent-url` /
`data-csrf` and gains `hx-get` / `hx-target` / `hx-swap` / `hx-trigger`; its label
is a `<span id="hire-offer-slot">Checking availability…</span>` child. The static
`<p class="hire-price">` and the flat button label in this branch are removed
(price now lives in the slotted label). Non-hireable / no-wallet branches keep the
current flat-priced disabled fallback unchanged.

### 3.6 `app/routers/hires.py::create_hire` — use the offer, else flat

Adds a guarded probe before building the row (§8): `endpoint = agent.a2a_endpoint
or agent.agent_url`, `offer = await probe_agent_offer(endpoint)` when endpoint
present, support check via `is_supported_offer`, else `offer = None`. Fee
computation and `build_challenge` call are unchanged in shape — only
`pay_to`/`amount_wei`/evidence inputs change.

### 3.7 `app/db/models/hired_agent.py` + NEW `migrations/versions/0013_hired_agent_offer_evidence.py`

Four nullable columns on `hired_agents` (all `nullable=True`, no data migration):

| Column | Type | Meaning |
|---|---|---|
| `amount_agent` | `Numeric(38, 18)` | offer `price_usd` ($U units), null when no offer |
| `pay_to_agent` | `Text` | offer `pay_to` |
| `asset_agent` | `Text` | offer `asset` |
| `network_agent` | `Text` | offer `network` |

Migration: `revision="0013_hired_agent_offer_evidence"`,
`down_revision="0012_compliance_penalty"` (head today; compliance consumed
`0012_*`, so `0013_*` is the next slot). `upgrade()` adds the 4 columns via
`op.add_column`; `downgrade()` drops them in reverse. No indexes needed (evidence
is write-once audit, never queried).

### 3.8 `app/schemas/hired.py` — HireOut echo (small, D-9)

`HireOut`/`HireCreateOut` gain the 4 evidence fields (`from_attributes=True`
already set) so the API echoes what the agent quoted. Optional but cheap;
skipping it does not affect AC-5 (columns are persisted regardless).

### 3.9 Explicitly NOT changed

`app/static/js/payment.js` (signs `accepts[0]` + `accepts[1]`, unchanged — D-8
guarantees its `#hire-cta` binding survives); `app/services/payment.py`
(`build_challenge` already supports the second accept); the verify/settle path;
`app/services/agent_payments.py`; `_hire_pricing` (still used by home cards, the
no-wallet fallback branch, and `my_last_hire` formatting).

## 4. `probe_agent_offer` internals

```python
async def probe_agent_offer(url, *, timeout_s=2.0) -> AgentOffer | None:
    try:
        target = _validate_probe_url(url)                     # SSRF guard, raises ValueError
        async with httpx.AsyncClient(timeout=timeout_s,
                                     follow_redirects=False) as client:
            resp = await client.get(target)
    except (httpx.HTTPError, ValueError, OSError):
        return None                                           # unreachable/blocked → None
    if resp.status_code != 402:
        return None                                           # R-4: non-402 → None, no false positive
    header = resp.headers.get("payment-required")             # httpx headers are case-insensitive
    if not header:
        return None
    try:
        payload = json.loads(base64.b64decode(header, validate=True).decode("utf-8"))
        a = payload["accepts"][0]                             # only accepts[0]; fee is ours, not theirs
        pay_to, amount, asset, network = a["payTo"], a["amount"], a["asset"], a["network"]
    except (KeyError, IndexError, TypeError, ValueError,
            binascii.Error, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not re.match(r"^0x[0-9a-fA-F]{40}$", pay_to) or not re.match(r"^\d+$", str(amount)):
        return None                                           # shape guard mirrors payment.py validators
    return AgentOffer(pay_to, int(amount), asset, network,
                      Decimal(amount) / _WEI_PER_UNIT)        # price_usd = amount_wei / 10**18
```

`_validate_probe_url`:
1. `u = httpx.URL(url)`; require `u.scheme in ("http", "https")`, else raise.
2. `host = u.host`, must be non-empty.
3. Literal IP → `ipaddress.ip_address(host)`; reject `is_private`,
   `is_loopback`, `is_link_local`, `is_reserved`, `is_multicast`,
   `is_unspecified`.
4. Hostname → `socket.getaddrinfo(host, port)`, collect IPv4/IPv6 results; if any
   resolved address falls in the rejected classes → raise; resolution failure →
   raise (fail-closed).
5. `follow_redirects=False` on the client is itself an SSRF mitigation (no
   redirect-following to internal targets).

Every failure path returns `None`; `probe_agent_offer` never raises.

## 5. `HireOffer` builder (endpoint + shared with tests)

```python
def _build_hire_offer(agent, offer: AgentOffer | None, settings) -> HireOffer:
    if not (agent.x402_supported and agent.agent_wallet):
        return HireOffer(has_offer=False, price_usd=None, agent_price_usd=None,
                         fee_usd=None, pay_to=None, disabled=True,
                         reason="no_payment_wallet")
    if offer is None:
        return HireOffer(has_offer=False, price_usd=None, agent_price_usd=None,
                         fee_usd=None, pay_to=None, disabled=True,
                         reason="no_endpoint" if _endpoint_of(agent) is None else "unreachable")
    if not is_supported_offer(offer, settings):
        return HireOffer(has_offer=False, price_usd=None, agent_price_usd=None,
                         fee_usd=None, pay_to=offer.pay_to, disabled=True,
                         reason="unsupported_asset")
    fee = settings.x402_fee_amount_usd if (settings.x402_fee_wallet or "").strip() else None
    total = offer.price_usd + (fee or Decimal("0"))
    return HireOffer(has_offer=True, price_usd=total,
                     agent_price_usd=offer.price_usd, fee_usd=fee,
                     pay_to=offer.pay_to, disabled=False, reason=None)
```

`_endpoint_of(agent) = agent.a2a_endpoint or agent.agent_url` (a2a first, D-1).
`price_usd` is the TOTAL the user pays (fee adds on top, D-4); the label renders
it with the existing `'%.2f'` convention (tiny wei quotes can display `$0.00` —
flagged as a display note, not a correctness issue: the challenge carries exact
wei and `verify_payment` enforces exact amounts).

## 6. `is_supported_offer`

```python
def is_supported_offer(offer: AgentOffer, settings) -> bool:
    return (offer.network == f"eip155:{settings.x402_chain_id}"
            and offer.asset.lower() == settings.x402_u_token_address.lower())
```

Single rail, v1 (multi-asset is out of scope): asset must be the pinned `$U` for
the configured chain AND network must be `eip155:<configured chain id>`. Case-
insensitive asset comparison (addresses may be checksummed either way).

## 7. hire-offer endpoint flow

```
1. select AgentCache where chain_id, token_id → 404 "not cached" if missing
2. endpoint = agent.a2a_endpoint or agent.agent_url
3. offer = None
   if agent.x402_supported and agent.agent_wallet and endpoint:
       offer = await probe_agent_offer(endpoint)
4. hire_offer = _build_hire_offer(agent, offer, get_settings())
5. return _render(request, "partials/hire_offer.html", {"offer": hire_offer})
```

No login, no CSRF, no DB writes. Renders the fragment for both HTMX and plain
callers. Worst case the probe adds 2s server-side; it never blocks first paint
(hx-trigger="load").

## 8. `create_hire` change (D-11: re-probe at hire time, no cache)

```python
settings = get_settings()
endpoint = agent.a2a_endpoint or agent.agent_url        # D-1
offer = await probe_agent_offer(endpoint) if endpoint else None
use_offer = offer is not None and is_supported_offer(offer, settings)

pay_to = offer.pay_to if use_offer else agent.agent_wallet
if not pay_to:
    raise NoPayTo(...)                                   # unchanged gate (offer pay_to is validated 0x)
amount = offer.price_usd if use_offer else settings.x402_default_price_usd
amount_wei = int(amount * _WEI_PER_UNIT)                 # same conversion as today

row = HiredAgent(
    ..., amount=amount, token=settings.x402_u_token_address, rail=EIP3009_RAIL,
    pay_to=pay_to, ...
    amount_agent=offer.price_usd if use_offer else None,
    pay_to_agent=offer.pay_to if use_offer else None,
    asset_agent=offer.asset if use_offer else None,
    network_agent=offer.network if use_offer else None,
)
challenge = build_challenge(
    pay_to, resource_url, amount_wei=amount_wei,
    timeout_s=DEFAULT_TIMEOUT_SECONDS, chain_id=settings.x402_chain_id,
    fee_pay_to=fee_wallet or None, fee_amount_wei=fee_wei,     # accepts[1] UNCHANGED
)
```

- AC-4: offer path uses real `pay_to`/`amount`; fallback preserves today's flat
  behavior + `agent_wallet`.
- AC-6: `fee_wallet`/`fee_wei` logic untouched → fee lands on top of the real
  price as `accepts[1]`; `payment.js` already signs both accepts.
- AC-5: evidence columns populated only on `use_offer`, else null.
- R-5: if the agent's offer drifts between probe and pay, `verify_payment` still
  enforces challenge `payTo`/`amount` at pay time; evidence columns keep the audit
  trail. If the probe fails at hire time (or the agent was unreachable), create
  falls back to flat — the UI disabled state already prevented the click in the
  unreachable case; a click during the probe window still hires at the re-probed
  (or flat) price.

## 9. HTMX contract + DOM mechanics (D-8, payment.js untouched)

Initial markup (hireable branch):

```html
<button id="hire-cta" type="button" class="btn btn-primary"
        data-agent-id="{{ agent.agent_id }}"
        data-agent-url="{{ agent.agent_url or '' }}"
        data-csrf="{{ csrf_token() if csrf_token is defined else '' }}"
        hx-get="/agents/{{ agent.chain_id }}/{{ agent.token_id }}/hire-offer"
        hx-target="#hire-offer-slot" hx-swap="innerHTML" hx-trigger="load">
  <span id="hire-offer-slot">Checking availability…</span>
</button>
```

- **D-8 core:** `#hire-cta` is a STABLE node — the swap target is the label
  `<span>` *inside* the button, so `payment.js`'s `DOMContentLoaded` binding
  (lookup + `addEventListener` on the button) survives the swap. The signer module
  genuinely needs no changes.
- Enabled swap → label becomes `Hire for ${{price_usd}}` (+ fee `<small>`).
- Disabled swap → label `Not available` + hint + inline swap script
  (executed by htmx) that sets `#hire-cta.disabled = true`,
  `aria-disabled="true"`, `class += "is-disabled"` — the existing
  `if (cta.disabled) return` in payment.js short-circuits before any click.
- Initial button is ENABLED so payment.js binds (a `disabled` initial state would
  make the DOMContentLoaded guard bail and never bind); a click during the ≤2s
  probe window is safe because `create_hire` re-probes (§8, D-11).
- No-JS: hx-* attributes are inert, the button REMAINS with its data-* wiring and
  "Checking availability…" label ("button remains" — test pins the markup). The
  marketplace hire flow requires JS anyway (wallet signing), so the visible
  fallback is the wired initial CTA, never a broken/dead-looking element.
- `#hire-status` (payment.js status UI) stays OUTSIDE the button/slot, untouched.

## 10. Data / entity changes

- `HiredAgent` +4 nullable evidence columns (0013 migration) — §3.7.
- No changes to `AgentCache` (a2a_endpoint/agent_url already exist, nullable).
- No package/dependency additions (httpx, ipaddress, socket all stdlib/available).

## 11. Test strategy (strict TDD, offline)

### 11.1 NEW `tests/fixtures/x402_challenge_payai.json` + `tests/fixtures/x402_challenge_payai.b64`

- JSON = the real captured PayAI challenge (explore spike): `{"x402Version":2,
  "error":"PAYMENT-SIGNATURE header is required","accepts":[{scheme:exact,
  network:"eip155:84532", amount:"10000", payTo:"0x2a835A…89453",
  maxTimeoutSeconds:300, asset:"0x036CbD…3CF7e", extra:{…}}]}`.
- `.b64` = the whitespace-free base64 of that JSON — the literal header value from
  the spike, pinned so drift is caught.
- A small test helper `payai_header(*, asset=None, network=None)` loads the JSON,
  rewrites `asset`/`network` to the test rail when requested (testnet chain id 97 +
  `settings.x402_u_token_address`), returns the base64 — lets the SAME fixture
  drive supported-path and unsupported-path tests.

### 11.2 NEW `tests/test_x402_client.py`

- Valid 402 (respx mock) → `AgentOffer` fields + `price_usd == Decimal(10000)/10**18`.
- Non-402 (200/500) → `None`; timeout (`respx` delayed > 2s or `httpx.TimeoutException` raise) → `None`.
- Malformed header: bad base64, non-JSON, missing `accepts`, bad payTo/amount → `None`, never raises.
- SSRF guard: `file://`, `http://127.0.0.1`, `http://10.0.0.1`, `http://169.254.169.254`, `http://[::1]/` → `None` AND no request issued (respx asserts no route hit).
- `is_supported_offer`: match (eip155:97 + testnet $U case-insensitive) / mismatch (wrong chain, wrong asset).

### 11.3 NEW `tests/test_hire_offer_endpoint.py`

- Enabled: agent with a2a_endpoint → respx 402 supported offer → body contains
  `Hire for $` real price, `data-*` wiring intact, `has-offer` markers; fee line
  when fee wallet set (monkeypatch like `test_api_hires_pay.py`).
- Disabled: no endpoint; endpoint 500/timeout (unreachable); unsupported asset
  (raw PayAI fixture, eip155:84532); no wallet → `Not available` + `disabled`-state
  script present.
- a2a preferred over agent_url (both set → respx asserts only a2a_endpoint called; both null → no request).

### 11.4 extend `tests/test_api_hires.py` (the "test_hires" surface)

- Offer path: seed agent with a2a_endpoint, respx 402 supported offer → 201;
  `challenge.accepts[0].payTo/amount` match the offer; `accepts[1]` = fee (when fee wallet set);
  response evidence fields populated; DB row evidence columns set.
- Fallback path: no endpoint / probe 500 → flat amount + `agent_wallet`,
  evidence columns None.
- Regression: unknown agent 404, auth+CSRF unchanged.

### 11.5 update `tests/test_pages_x402.py` (+ `test_pages.py` if it asserts the CTA)

- `test_cta_renders_price_and_is_enabled`: initial label is now
  "Checking availability…"; assert `hx-get`, `hx-target="#hire-offer-slot"`,
  `hx-swap="innerHTML"`, `hx-trigger="load"`, and the preserved data-* wiring
  (R-6: the old `Hire for $1.03` flat-label assert moves to the endpoint partial tests).
- `test_cta_disabled_without_wallet`: unchanged (non-hireable branch keeps flat fallback).
- New: no-JS fallback — body contains the enabled initial button with label + data
  attributes ("button remains"); `#hire-status` outside the slot.

### 11.6 extend `tests/test_alembic_check.py`

Static DDL-fragment checks for `0013_*` (add_column ×4 / drop_column ×4 parity),
mirroring the `0004` up/down parity test pattern.

### 11.7 Acceptance mapping

AC-1 → 11.2 · AC-2 → 11.3 enabled · AC-3 → 11.3 disabled · AC-4 → 11.4 ·
AC-5 → 11.4 + migration · AC-6 → 11.4 (accepts[1]) · AC-7 → full baseline
(414 at branch point) + new files, offline (aiosqlite + respx).

## 12. Risks

| # | Risk | Mitigation |
|---|---|---|
| R-1 | Probe latency; can hang | 2s timeout + lazy HTMX render (never blocks first paint) |
| R-2 | SSRF via arbitrary probe URLs | `_validate_probe_url` (http/https only, private/loopback/link-local/reserved/multicast block, literal IP + DNS resolution, `follow_redirects=False`) |
| R-3 | Agent quotes unsupported asset/network | v1 renders price only on rail match; otherwise disabled "not available" (AC-3) |
| R-4 | `a2a_endpoint` points at an A2A AgentCard, not an x402 endpoint | Probe only accepts `402` + parseable `PAYMENT-REQUIRED`; anything else → None (`test_hire_offer_endpoint` non-402) |
| R-5 | Agent reply changes between probe and pay | `verify_payment` enforces challenge payTo/amount at pay time; evidence columns audit the quote |
| R-6 | Flat-price tests pin the CTA label | W1 asserts updated to the initial state; price assertions live in the endpoint partial tests (11.5) |
| R-7 | Migration slot collision | Next slot is `0013_*` (head `0012_compliance_penalty`); alembic parity checks in 11.6; `0013` up/down symmetric |
| R-8 | HTMX swap drops the payment.js click binding | D-8: button node stable, only its inner label span is swapped; inline swap script sets disabled for not-available; `#hire-status` kept outside the slot |

## 13. Rollout / verification order (strict TDD)

1. Fixtures (11.1) → 2. `test_x402_client.py` → `x402_client.py` → 3.
   `test_hire_offer_endpoint.py` → endpoint + partial + schema → 4.
   `test_api_hires.py` offer/fallback → `create_hire` change + model columns +
   migration → 5. template CTA wiring + `test_pages_x402` updates (R-6) → 6.
   `test_alembic_check` parity block → 7. full pytest (AC-7 baseline preserved) →
   verify phase.

## 14. Out of scope (re-confirmed)

No multi-asset rails; no verify/settle changes; no SSRF hardening beyond the
guard; no scheduler/cache of offers; `payment.js` untouched; no `tasks.md` in
this phase.