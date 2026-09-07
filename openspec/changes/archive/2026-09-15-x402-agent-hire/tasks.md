# Tasks: x402-agent-hire

**Change:** `x402-agent-hire`
**Branch:** `feat/x402-agent-hire`
**Phase:** Tasks (SDD fast-track)
**Inputs:** `openspec/changes/x402-agent-hire/{explore,proposal,spec,design}.md`.
**Method:** Strict TDD — RED → GREEN → TRIANGULATE → REFACTOR per work unit.
**Output contract:** this file is the only deliverable; production code stays read-only.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | 900–1200 (adds+deletes) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (probe+offer) → PR 2 (endpoint+partial) → PR 3 (create_hire+evidence+CTA) |
| Delivery strategy | auto-chain |
| Chain strategy | feature-branch-chain |

```text
Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: High
```

Rationale: 2 new modules + schema + partial, a migration, 3 test files plus 3
extended suites, and template/JS wiring push well past 400 lines → 3 verifiable PRs.

## Hard-constrained decisions (carried forward, not relitigated)

1. `a2a_endpoint` first, `agent_url` fallback; both null → no offer.
2. No 402 / timeout / null endpoint → disabled "not available" (never a guessed price).
3. Lazy HTMX probe (`hx-trigger="load"`); never blocks first paint.
4. Marketplace fee adds on top of the real price as `accepts[1]`.
5. 4 evidence columns on `HiredAgent` (migration `0013_*`).
6. Price rendered only when offer asset+network match the rail (`is_supported_offer`).
7. SSRF guard: http/https only; block private/loopback/link-local/reserved/multicast.
8. Strict TDD, offline (aiosqlite + respx), 414-pass baseline preserved.

Out of scope: multi-asset rails, verify/settle path, `payment.js`, scheduler/cache.

---

## PR 1 — Probe helper (`app/services/x402_client.py`)

Boundary: fixture + pure async probe module (no routers/DB/templates). Rollback:
delete `tests/fixtures/x402_challenge_payai.*` + `x402_client.py` + `test_x402_client.py`.

### Fixtures
- [x] Add `tests/fixtures/x402_challenge_payai.json` — the captured PayAI v2
  challenge (`accepts[0]` with payTo/amount/asset/network; pin exact values from
  the explore spike) and `tests/fixtures/x402_challenge_payai.b64` (whitespace-free
  base64 = literal `PAYMENT-REQUIRED` value). Add a test helper
  `payai_header(*, asset=None, network=None)` (rewrites asset/network to the test
  rail; returns base64). <!-- sdd-owner: implementation -->

### T1 RED
- [x] `tests/test_x402_client.py::test_probe_agent_offer_parses_real_402` —
  respx mock returns 402 + `payment-required: <payai_header()>`; assert
  `AgentOffer.pay_to/amount_wei/asset/network` match and `price_usd ==
  Decimal(10000)/10**18`. Assert it fails against the absent module.
  <!-- sdd-owner: implementation -->

### T2 GREEN
- [x] `app/services/x402_client.py` — `@dataclass(frozen=True) AgentOffer` (pay_to,
  amount_wei:int, asset, network, price_usd:Decimal) + `async def
  probe_agent_offer(url, *, timeout_s=2.0) -> AgentOffer | None`: 2s timeout,
  `follow_redirects=False`, parse 402 + base64 JSON `accepts[0]`, validate 0x40
  payTo + numeric amount, `price_usd = amount_wei/10**18`; every failure returns
  `None`, never raises. Local `_WEI_PER_UNIT = Decimal(10**18)` (do not import
  from routers). <!-- sdd-owner: implementation -->

### T3 TRIANGULATE
- [x] `tests/test_x402_client.py` — `test_probe_returns_none_on_timeout`
  (resp delayed >2s / `httpx.TimeoutException`), `test_probe_returns_none_on_404`,
  `test_probe_returns_none_on_malformed_payment_required` (bad base64, non-JSON,
  missing `accepts`, bad payTo/amount) — all return `None`, no raise.
  <!-- sdd-owner: implementation -->

### T4 RED
- [x] `tests/test_x402_client.py::test_probe_rejects_private_range_url` — SSRF:
  `file://`, `http://127.0.0.1`, `http://10.0.0.1`, `http://169.254.169.254`,
  `http://[::1]/` → `None` AND respx asserts no route hit (fail on current module
  lacking the guard). <!-- sdd-owner: implementation -->

### T5 GREEN
- [x] `app/services/x402_client.py::_validate_probe_url(url) -> httpx.URL` —
  http/https only, non-empty host; literal IP via `ipaddress` rejected when
  private/loopback/link_local/reserved/multicast/unspecified; hostname via
  `socket.getaddrinfo` rejected if any resolved addr is rejected, fail-closed on
  resolution failure; raises `ValueError` (caught in probe → None).
  <!-- sdd-owner: implementation -->

### T6 RED
- [x] `tests/test_x402_client.py::test_is_supported_offer_matches_rail` — assert
  match for `eip155:97` + testnet `$U` (case-insensitive asset) and mismatch for
  wrong chain / wrong asset. Fails on absent `is_supported_offer`.
  <!-- sdd-owner: implementation -->

### T7 GREEN
- [x] `app/services/x402_client.py::is_supported_offer(offer, settings) -> bool` —
  `offer.network == f"eip155:{settings.x402_chain_id}"` and
  `offer.asset.lower() == settings.x402_u_token_address.lower()`.
  <!-- sdd-owner: implementation -->

**PR 1 verification:** `pytest tests/test_x402_client.py` green; full suite still 414-pass.

---

## PR 2 — hire-offer endpoint + partial

Boundary: `HireOffer` schema, `GET /agents/{chain}/{token}/hire-offer` in
`app/routers/pages.py`, `partials/hire_offer.html`. No `create_hire` change.
Rollback: delete schema, endpoint, partial, test file.

### T8 RED
- [x] `tests/test_hire_offer_endpoint.py::test_hire_offer_renders_real_price` —
  seed agent with a2a_endpoint, respx 402 supported offer → response contains
  `Hire for $` real total (price+fee when fee wallet set via monkeypatch), data-*
  wiring intact, `has-offer` markers. Fails on missing endpoint.
  <!-- sdd-owner: implementation -->

### T9 GREEN
- [x] `app/schemas/hire_offer.py` — `HireOffer` model (has_offer, price_usd,
  agent_price_usd, fee_usd, pay_to, disabled, reason). Endpoint
  `GET /agents/{chain_id}/{token_id}/hire-offer` in `app/routers/pages.py`
  (public GET, no auth/CSRF) — select `AgentCache`, `endpoint = a2a_endpoint or
  agent_url`, probe when `x402_supported and agent_wallet and endpoint`, build via
  module-level `_build_hire_offer`, render `partials/hire_offer.html` for both
  HTMX and plain callers. <!-- sdd-owner: implementation -->

### T10 RED
- [x] `tests/test_hire_offer_endpoint.py::test_hire_offer_disabled_when_no_endpoint`
  — agent with null a2a_endpoint and agent_url → body contains "Not available" +
  disabled-state script; no probe issued. Fails on current behavior.
  <!-- sdd-owner: implementation -->

### T11 GREEN
- [x] `app/routers/pages.py::_build_hire_offer` + `partials/hire_offer.html` — no
  endpoint → `disabled, reason="no_endpoint"`; null wallet →
  `"no_payment_wallet"`; unreachable/timeout/non-402 → `"unreachable"`. Disabled
  partial shows "Not available" + hint + idempotent inline script setting
  `#hire-cta.disabled=true`/aria-disabled/is-disabled. <!-- sdd-owner: implementation -->

### T12 RED
- [x] `tests/test_hire_offer_endpoint.py::test_hire_offer_disabled_when_unsupported_asset`
  — offer with `eip155:84532` / non-rail asset (raw PayAI fixture) → "Not available"
  + `reason="unsupported_asset"`, no enabled price. Fails on missing support gate.
  <!-- sdd-owner: implementation -->

### T13 GREEN
- [x] `_build_hire_offer` — when `is_supported_offer` is False → disabled
  `reason="unsupported_asset"` (price not rendered). When supported → enabled,
  `price_usd = offer.price_usd + fee` (`fee = settings.x402_fee_amount_usd` only if
  `x402_fee_wallet` set, else None). Also add `test_hire_offer_prefers_a2a_over_agent_url`
  (both set → respx asserts only a2a_endpoint called). <!-- sdd-owner: implementation -->

**PR 2 verification:** `pytest tests/test_hire_offer_endpoint.py` green; baseline
preserved.

---

## PR 3 — create_hire uses offer + evidence + CTA wiring

Boundary: `create_hire`, `HiredAgent` model + `0013_*` migration, `HireOut` echo,
`agent_detail.html` CTA, `test_pages_x402`/`test_alembic_check` updates. Rollback:
revert create_hire, drop columns/migration, revert template + tests.

### T14 RED
- [x] Extend `tests/test_api_hires.py::test_create_hire_uses_agent_offer_when_available`
  — seed agent with a2a_endpoint, respx 402 supported offer → 201;
  `challenge.accepts[0].payTo/amount` match the offer; `accepts[1]` = fee when fee
  wallet set; evidence fields in response + DB row. Fails on flat-only behavior.
  <!-- sdd-owner: implementation -->

### T15 GREEN
- [x] `app/routers/hires.py::create_hire` — `endpoint = a2a_endpoint or agent_url`;
  `offer = await probe_agent_offer(endpoint)` if endpoint else None;
  `use_offer = offer and is_supported_offer(offer, settings)`. When use_offer →
  `pay_to = offer.pay_to`, `amount = offer.price_usd`; else keep flat
  `agent.agent_wallet`/`x402_default_price_usd`. Keep NoPayTo gate, fee→accepts[1],
  and `build_challenge` shape unchanged. <!-- sdd-owner: implementation -->

### T16 RED
- [x] Extend `tests/test_api_hires.py::test_create_hire_falls_back_to_flat_without_offer`
  — no endpoint / probe 500 → flat amount + `agent_wallet`, evidence columns None.
  Fails before the conditional is wired. <!-- sdd-owner: implementation -->

### T17 GREEN
- [x] Confirm/refine `create_hire` fallback path preserves current flat behavior
  (regression: unknown agent 404, auth+CSRF unchanged). No code change beyond T15 if
  already covered. <!-- sdd-owner: implementation -->

### T18 RED
- [x] Extend `tests/test_api_hires.py::test_create_hire_persists_evidence_columns` —
  offer path → DB row has `amount_agent/pay_to_agent/asset_agent/network_agent`
  populated; no-offer path → all None. Fails on missing columns.
  <!-- sdd-owner: implementation -->

### T19 GREEN
- [x] Migration `migrations/versions/0013_hired_agent_offer_evidence.py`
  (`revision="0013_hired_agent_offer_evidence"`, `down_revision="0012_compliance_penalty"`)
  adds 4 nullable columns `amount_agent Numeric(38,18)`, `pay_to_agent Text`,
  `asset_agent Text`, `network_agent Text`; downgrade drops in reverse; no indexes.
  Update `app/db/models/hired_agent.py` (+4 nullable attrs) and `app/schemas/hired.py`
  `HireOut`/`HireCreateOut` (+4 echo fields); extend `tests/test_alembic_check.py`
  with `0013` up/down column parity. <!-- sdd-owner: implementation -->

### T20 RED
- [x] Extend `tests/test_pages_x402.py::test_cta_renders_price_and_is_enabled` —
  initial label "Checking availability…" + `hx-get`, `hx-target="#hire-offer-slot"`,
  `hx-swap="innerHTML"`, `hx-trigger="load"`, preserved data-agent-id/data-agent-url/
  data-csrf. New `test_cta_no_js_fallback` pins the enabled initial button remains
  with data-* and `#hire-status` outside the slot. Fails on current static label.
  <!-- sdd-owner: implementation -->

### T21 GREEN
- [x] `app/templates/pages/agent_detail.html` — hireable branch: stable `#hire-cta`
  button node (never swapped) with `hx-get/hx-target/hx-swap/hx-trigger="load"` and
  child `<span id="hire-offer-slot">Checking availability…</span>`; remove static
  `.hire-price`/flat label in this branch. Non-hireable/no-wallet branches keep
  flat disabled fallback. `payment.js` untouched (swap only inner slot so its
  binding survives). <!-- sdd-owner: implementation -->

### T22 Final
- [x] Run full `pytest` — new suites + all extended suites green; 414-pass baseline
  preserved. Confirm scope guards: `app/services/payment.py`, `app/static/js/payment.js`,
  and the verify path untouched (git diff shows no changes there).
  <!-- sdd-owner: implementation -->

**PR 3 verification:** full suite green; alembic check passes; manual: detail page
shows "Checking availability…" → HTMX swaps real price or "Not available".

---

## Cross-cutting notes

- New deps: none (httpx, ipaddress, socket, respx already available).
- SSRF enforced in the probe only; never follow redirects.
- `probe_agent_offer` re-invoked at hire time (no cache); `verify_payment` still
  enforces payTo/amount at pay time.
