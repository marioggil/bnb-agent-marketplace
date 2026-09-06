# Agent Detail UI — Test Copy Drift Fix

**Change:** `test-copy-fix`
**Domain:** `agent-detail-ui` (renders produced by `app/templates/pages/agent_detail.html`, asserted by `tests/test_pages.py` and `tests/test_pages_x402.py`)
**Scope:** Tests-only. Update 3 stale assertions to match the current rendered copy introduced by FU-2 (commits `ffaad16`, `2b92290`).
**Output path note:** This spec is written flat at `openspec/changes/test-copy-fix/spec.md` per the parent task's explicit output path, instead of the `specs/{domain}/spec.md` nested layout. Archive will treat this as a full new domain spec and copy it to `openspec/specs/agent-detail-ui/spec.md`.

---

## Purpose

The 3 tests below fail because their `assert` strings stopped matching the rendered HTML after FU-2 x402 copy work. The drift is purely in the test files — the production template, routes, and JS are correct and MUST NOT change. This spec pins down the post-fix contract: each failing test must pass, assert against the current rendered copy, and preserve the rest of the 282-test baseline.

---

## Non-Goals

- **No production code changes.** `app/templates/pages/agent_detail.html`, `app/routers/pages.py`, `app/static/js/payment.js`, and any other runtime artifact MUST stay byte-for-byte unchanged.
- **No behaviour changes** for end users (CTA copy, panel copy, prices, disabled states remain as today).
- **No new tests** are added or removed — only the 3 listed assertions are updated.
- **No CI / tooling changes** (no `pytest.ini`, no `conftest.py` flag flips beyond what's strictly needed to make the assertions evaluate against real rendered output).

---

## Acceptance Criteria

The change MUST satisfy all of the following, verifiable from a clean checkout:

1. **AC-1 — Targeted suite green.** `uv run pytest tests/test_pages.py tests/test_pages_x402.py` reports `0 failed`.
2. **AC-2 — Baseline preserved.** `uv run pytest` reports no new failures and no new errors compared to the pre-change baseline of 282 passing tests. (The 3 previously-failing tests in scope now count toward the passing total.)
3. **AC-3 — Production diff is empty.** `git diff -- app/ app/templates app/static app/routers` is empty. Any change outside `tests/` (including `tests/conftest.py`) must be justified by a per-line comment in the diff.
4. **AC-4 — Assertions reflect current copy.** Each updated assertion string MUST match a contiguous substring of the actual rendered HTML from the corresponding `client.get(...)` response, not a paraphrase.

---

## Requirements

### Requirement: Hired-by-you panel + "Hire again" CTA test passes

The test `tests/test_pages.py::test_agent_detail_shows_hired_panel_and_hire_again` MUST pass after the fix, asserting that a signed-in user who owns a paid `HiredAgent` row for the page's agent sees (a) the "Hired by you" panel and (b) the CTA button rendered as `Hire again for $1.00`.

The template (`app/templates/pages/agent_detail.html` line ~578) renders the CTA as:

```jinja
Hire {% if my_paid_count %}again {% endif %}for ${{ '%.2f'|format(hire_price_usd) }}
```

so the literal string `Hire again for $1.00` is present in the rendered body if and only if `my_paid_count >= 1` for the signed-in viewer.

#### Scenario: paid hire is visible to the page render

- GIVEN a seeded `AgentCache` at token id 1 (chain 56) with a non-null `agent_wallet`
- AND a signed-in session via `tests.conftest._sign_in`
- AND exactly one `HiredAgent` row belonging to that user with `status = PAID`, committed in a session the page-render path can read
- WHEN the client issues `GET /agents/56/1`
- THEN the response body contains the substring `Hired by you`
- AND the body contains the substring `You hired this agent once`
- AND the body contains the substring `Hire again for $1.00`
- AND the body contains the substring `view transaction`

#### Scenario: my_paid_count visibility (DB session sharing)

- GIVEN the same setup as above
- WHEN the route handler reads `my_paid_count` from the request-scoped DB session for the signed-in address
- THEN `my_paid_count` MUST equal `1`
- IF `my_paid_count` is `0` because the seeded PAID row lives in a different SQLAlchemy session than the one the route uses, the fix MUST make the seed visible (e.g. via `tests/conftest.py` wiring, transaction rollback, or `expire_all`) rather than weakening the assertion to `Hire for $1.00`. The intent of the test is to lock down the "Hire again" copy for returning customers; relaxing it would lose coverage.

---

### Requirement: CTA price-and-enabled test passes

The test `tests/test_pages_x402.py::test_cta_renders_price_and_is_enabled` MUST pass after the fix. The CTA button MUST render with the configured flat price, be enabled (no `disabled` attribute), and expose the signer wiring attributes the JS consumes.

The template renders the button as:

```jinja
<button id="hire-cta" type="button" class="btn btn-primary"
        data-agent-id="..." data-agent-url="..." data-csrf="...">
  Hire {% if my_paid_count %}again {% endif %}for ${{ '%.2f'|format(hire_price_usd) }}
</button>
```

with `my_paid_count = 0` (no prior hires seeded by this test) and `hire_price_usd = X402_DEFAULT_PRICE_USD + X402_FEE_AMOUNT_USD` when a fee wallet is set, otherwise the flat price alone.

#### Scenario: agent with wallet renders enabled CTA at configured price

- GIVEN a seeded `AgentCache` at token id 1 (chain 56) with a non-null `agent_wallet`
- AND no `HiredAgent` rows for the (anonymous) viewer
- WHEN the client issues `GET /agents/56/1`
- THEN the response body contains `id="hire-cta"`
- AND the body contains the configured hire price in the form `Hire for $X.XX` where `X.XX` matches `('%.2f' % hire_price_usd)` for the current default settings
- AND the body does NOT contain the substring `disabled` in any attribute context for the `hire-cta` element
- AND the body contains `data-agent-url="https://agent.example.com/chat"`
- AND the body contains the exact `data-agent-id="56:0x8004A169FB4a3325136EB29fA0ceB6D2e539a432:1"`
- AND the body contains `data-csrf="`

#### Scenario: assertion string matches actual rendered price

- IF the actual rendered price is `Hire for $1.03` (because `X402_FEE_AMOUNT_USD` is non-zero in the test environment) rather than `Hire for $1.00`, the test assertion MUST be updated to the actual rendered price string — NOT to `$1.00`, NOT to a regex, NOT removed. The fix MUST NOT mutate the settings under test to mask the assertion drift.

---

### Requirement: CTA disabled-without-wallet test passes

The test `tests/test_pages_x402.py::test_cta_disabled_without_wallet` MUST pass after the fix. When an agent has no payment wallet, the CTA button MUST render with the `disabled` attribute, and the page MUST surface the existing FU-2 hint copy.

The template renders the disabled branch as (lines ~589-595):

```jinja
<button id="hire-cta" type="button" class="btn btn-primary" disabled ...>
  Hire for ${{ '%.2f'|format(hire_price_usd) }}
</button>
<p class="hint">This agent cannot be hired &mdash; no payment wallet (payTo) is registered.</p>
```

#### Scenario: agent without wallet renders disabled CTA with hint

- GIVEN a seeded `AgentCache` at token id 2 (chain 56) with `agent_wallet = NULL`
- AND no `HiredAgent` rows for the viewer
- WHEN the client issues `GET /agents/56/2`
- THEN the response body contains `id="hire-cta"`
- AND the CTA element includes the `disabled` attribute
- AND the body contains the hint substring `no payment wallet (payTo) is registered`
- AND the body contains the configured price string `Hire for $X.XX` matching the current default settings

#### Scenario: hint copy exact match

- The hint substring asserted in the test MUST be byte-for-byte a substring of the rendered HTML (accounting for Jinja-emitted `&mdash;` only if the assertion is updated to use the entity form). If the rendered hint now reads "no payment wallet is registered" without the parenthetical, the test assertion MUST be updated to that string — not the other way around.

---

## Risks

- **R-1 (low):** `test_pages.py::test_agent_detail_shows_hired_panel_and_hire_again` may need a `tests/conftest.py` tweak so the seeded PAID hire is visible to the route's request-scoped session. That is still test-only and acceptable; AC-3 allows `conftest.py` edits when justified.
- **R-2 (low):** If the assertion in `test_cta_renders_price_and_is_enabled` was relaxed to `Hire for $1.00` while the actual rendered price is `$1.03` (or whatever the env's fee default is), the test would pass silently and stop guarding the price format. The fix MUST verify the rendered price string matches `('%.2f' % settings.x402_default_price_usd + settings.x402_fee_amount_usd)` before updating the assertion.
- **R-3 (low):** Pre-change baseline was 282 passing. If the test runner reports a different baseline on the working branch (e.g. 281 or 283), AC-2 is interpreted as "no change in failure count" rather than a hard 282 number.
- **R-4 (info):** This spec is written flat (`openspec/changes/test-copy-fix/spec.md`) rather than nested under `specs/agent-detail-ui/spec.md`. Archive should still treat it as a full new domain spec and copy to `openspec/specs/agent-detail-ui/spec.md` on acceptance.
