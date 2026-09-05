# Design: test-copy-fix

## Approach

Tests-only. Diagnose each failing assertion by capturing the actual rendered body, then apply the minimal test-side change that makes the assertion a contiguous substring of the body. The proposal's hypothesis (`my_paid_count` DB-session visibility) is **not** the primary root cause — see §1 below.

Root cause for tests 1 & 2: `app/routers/pages.py:605` computes `profile.hireable = bool(agent.x402_supported and agent.agent_wallet)`. The seeded agents don't set `x402_supported`, so the column stores `False` (model `server_default=text("false")` at `app/db/models/agent.py:113`). With `hireable=False`, the template renders the **disabled** branch (lines 589–595), whose button copy is `Hire for $X.XX` — never `Hire again for $X.XX`, regardless of `my_paid_count`. That is why test 1's body shows `"Hire for $1.00"` even with a PAID row seeded.

Production code (`agent_detail.html`, `pages.py`, `payment.js`) stays byte-for-byte unchanged. AC-3 satisfied: the only touched files are `tests/test_pages.py` and `tests/test_pages_x402.py`.

---

## 1. `tests/test_pages.py::test_agent_detail_shows_hired_panel_and_hire_again`

**Diagnosis.** Run `uv run pytest tests/test_pages.py::test_agent_detail_shows_hired_panel_and_hire_again -vvv` and inspect the rendered `<button id="hire-cta">` substring. Expected finding: the disabled-branch button (no `data-agent-url`, no `{% if my_paid_count %}again {% endif %}`). The PAID row is already committed and visible to the page route — both the test's `db` fixture and `pages.py` use `_TestSessionLocal` (same engine, same sqlite file at `bnb_agent_test.sqlite3`, AUTOCOMMIT isolation), so `my_hires` will return the row once `profile.hireable=True`.

**Fix (single line).** After `agent.agent_wallet = "0x" + "77" * 20`, add:

```python
agent.x402_supported = True
await db.commit()
```

This makes `profile.hireable=True` so the enabled branch renders. The PAID row → `my_paid_count=1` → `Hire again for $1.00`. The four existing assertions (`Hired by you`, `You hired this agent once`, `Hire again for $1.00`, `view transaction`) all match.

**Verify the actual price.** If a fee wallet env var is ever set in test, `hire_price_usd` becomes `1.00 + fee`. Capture the body, read the substring `$X.XX`, update the assertion only if the rendered price differs from `1.00`. Do **not** mutate settings to mask drift (spec AC-4, R-2).

No `tests/conftest.py` change is required for session sharing — R-1 in the spec is a fallback, not the actual cause.

---

## 2. `tests/test_pages_x402.py::test_cta_renders_price_and_is_enabled`

**Diagnosis.** Same root cause as test 1: `_seed_one` (this file's helper, lines 24–44) does not set `x402_supported`. With `wallet="0x…"`, `profile.hireable` is still `False`, so the disabled branch renders — the body contains `disabled`, but **not** `data-agent-url="…"` or the `{% if my_paid_count %}again` copy. The first assertion to fail in the spec's failure transcript is the price string, but actually every assertion after `"Hire for $1.00"` will fail (the price itself does appear in both branches; `"disabled" not in body` does not).

**Fix (one kwarg in the helper).** In `_seed_one`, default the seed to `x402_supported=True` whenever a wallet is supplied:

```python
async def _seed_one(session, token_id: int = 1, wallet: str | None = "0x" + "77" * 20) -> str:
    ...
    session.add(
        AgentCache(
            ...,
            agent_wallet=wallet,
            x402_supported=wallet is not None,  # NEW
            ...
        )
    )
```

This makes the seeded agent hireable when it has a wallet and unhireable when it doesn't, matching the test's documented intent ("agent with wallet renders enabled CTA at configured price"). Test 3 (`test_cta_disabled_without_wallet`) still seeds `wallet=None`, so `x402_supported=False`, `hireable=False`, disabled branch — correct.

**Assertions after fix.** All six assertions in `test_cta_renders_price_and_is_enabled` match the enabled branch:

- `"Hire for $1.00"` (anonymous viewer, `my_paid_count=0`, so the `{% if my_paid_count %}again {% endif %}` collapses)
- `'id="hire-cta"'`
- `"disabled" not in body`
- `f'data-agent-url="{_AGENT_URL}"'`
- `'data-agent-id="56:0x8004A169FB4a3325136EB29fA0ceB6D2e539a432:1"'`
- `"data-csrf="`

No string changes needed. If `X402_FEE_AMOUNT_USD=0.03` and a fee wallet get set in conftest later, the price will read `$1.03` and this assertion becomes the one to update.

---

## 3. `tests/test_pages_x402.py::test_cta_disabled_without_wallet`

**Diagnosis.** Seed: `wallet=None`, `x402_supported=False`. `profile.hireable=False`, hits disabled branch (lines 589–595). Expected rendered substrings: `id="hire-cta"`, `disabled`, `Hire for $1.00`, and the hint `no payment wallet (payTo) is registered` (line 595). All four assertions already match the template as it stands today.

If the test fails, capture the body and diff against the expected substrings above. Most likely culprits:

- **`csrf_token` undefined.** Template lines 591 / 596 call `csrf_token()` inside `{% if csrf_token is defined %}`. If the global is missing in this render path, the page returns 500 and `body` is the error page. (Verify: check `tests/conftest.py`'s `app` fixture or Starlette's `Jinja2Templates` wiring — should be defined globally; no fix expected.)
- **Price drift.** If `$1.03` renders instead of `$1.00`, update only the price assertion. Do not touch settings.
- **Hint copy drift.** If line 595 ever changes (e.g. the `(payTo)` parenthetical is dropped), update the assertion to the new exact substring per AC-4.

**Fix.** Most likely: no code change required, just re-run after the §2 helper update lands. If a string has drifted, replace the literal in the assertion to match the rendered substring (one-line edit).

---

## File changes

| File | Change |
|---|---|
| `tests/test_pages.py` | +1 line: `agent.x402_supported = True` before `await db.commit()` in `test_agent_detail_shows_hired_panel_and_hire_again`. |
| `tests/test_pages_x402.py` | +1 kwarg: `x402_supported=wallet is not None` in the `AgentCache(...)` constructor inside `_seed_one`. |
| `app/`, `tests/conftest.py` | unchanged. |

## Contracts & data flow

- `profile.hireable` (read by template) = `agent.x402_supported AND agent.agent_wallet`. Seed must satisfy both for the enabled CTA branch.
- `hire_price_usd` (template) = `settings.x402_default_price_usd + (settings.x402_fee_amount_usd if settings.x402_fee_wallet else 0)`. With `X402_FEE_WALLET` unset in conftest, price = `$1.00`.
- `my_paid_count` (route) = `sum(1 for h in my_hires if h.status == "paid")`. `HiredStatus.PAID.value == "paid"` (string enum), so the equality holds. Session sharing is automatic via `_TestSessionLocal`.

## Tests & rollout

1. `uv run pytest tests/test_pages.py tests/test_pages_x402.py -vvv` → 0 failed (AC-1).
2. `uv run pytest` → 282 passed baseline preserved, +3 newly-passing (AC-2).
3. `git diff -- app/` → empty (AC-3).
4. `git diff -- tests/` → two minimal hunks: one `x402_supported = True` assignment, one `x402_supported=wallet is not None` kwarg.
5. Single PR, no migration, no config, no runtime risk.