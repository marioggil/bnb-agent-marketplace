# Tasks: test-copy-fix

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~3 lines (2 in `tests/test_pages.py`, 1 in `tests/test_pages_x402.py`) |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | single PR |
| Delivery strategy | single-pr |
| Chain strategy | size-exception |

```text
Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: size-exception
400-line budget risk: Low
```

## Task ordering rationale

Production code (`app/`, `tests/conftest.py`) stays byte-for-byte unchanged (spec AC-3). The diagnosis in `design.md` already established that the three failing tests share one root cause: `profile.hireable = agent.x402_supported AND agent.agent_wallet`, and the seed rows leave `x402_supported=False` (model `server_default=text("false")` at `app/db/models/agent.py:113`). Tasks are ordered: fix seed (T1, T2) → re-verify copy assertions (T3) → run targeted suite (T4) → run full baseline (T5) → confirm empty production diff (T6).

---

## Tasks

- [x] **T1 — Make `test_agent_detail_shows_hired_panel_and_hire_again` seed hireable agent.** In `tests/test_pages.py` at line ~111, after `agent.agent_wallet = "0x" + "77" * 20` and before `await db.commit()`, insert the line `agent.x402_supported = True`. This flips `profile.hireable` to True so the template renders the enabled branch (line ~578), which is the only branch where `{% if my_paid_count %}again {% endif %}` evaluates and emits `Hire again for $1.00`. <!-- sdd-owner: implementation -->

- [x] **T2 — Make `_seed_one` honour wallet presence in `tests/test_pages_x402.py`.** In the helper at line ~21, add the kwarg `x402_supported=wallet is not None` to the `AgentCache(...)` constructor (currently ends at line ~36 with `updated_at=_now()`). This makes the seed consistent with `profile.hireable = agent.x402_supported AND agent.agent_wallet`: hireable when a wallet is supplied (test `test_cta_renders_price_and_is_enabled` gets the enabled branch) and unhireable when `wallet=None` (test `test_cta_disabled_without_wallet` still hits the disabled branch). <!-- sdd-owner: implementation -->

- [x] **T3 — Verify `test_cta_disabled_without_wallet` assertions against template line ~595.** Read `app/templates/pages/agent_detail.html` lines 589–595 and confirm the four assertions at `tests/test_pages_x402.py` lines 66–69 (`id="hire-cta"`, `disabled`, `no payment wallet (payTo) is registered`, `Hire for $1.00`) are contiguous substrings of the rendered body. If `X402_FEE_AMOUNT_USD > 0` is ever set in `tests/conftest.py`, the price assertion becomes `Hire for $1.03` — update only the literal. If the `(payTo)` parenthetical ever drops from line 595, update the hint substring per spec AC-4. No change is expected on a clean checkout, but capture the body via a temporary `print(body)` (removed before commit) to confirm. <!-- sdd-owner: implementation -->

- [x] **T4 — Run the targeted suite.** Execute `uv run pytest tests/test_pages.py tests/test_pages_x402.py -vvv` from the repo root. Expect `0 failed` (spec AC-1). If any assertion still fails, capture the failure diff, re-read the failing test against `app/templates/pages/agent_detail.html`, and apply the minimal one-line assertion update that matches the actual rendered substring (do not weaken, do not regex, do not mutate settings). <!-- sdd-owner: implementation -->

- [x] **T5 — Run the full pytest baseline.** Execute `uv run pytest` from the repo root. Expect the previous baseline of 282 passing tests, plus the 3 newly-passing tests (now 285 total passing, `0 failed`). Spec AC-2 is interpreted as "no change in failure count" if the pre-change baseline on the working branch differs from 282 (spec R-3). <!-- sdd-owner: implementation -->

- [x] **T6 — Confirm the production diff is empty.** Execute `git diff -- app/ app/templates app/static app/routers` from the repo root and verify the output is empty (spec AC-3). Also run `git diff -- tests/conftest.py` and confirm it is empty — the design fixes the root cause at the seed site, so `conftest.py` should not need to change. If `conftest.py` does need a tweak (spec R-1 fallback, e.g. session-sharing for the PAID row), annotate each edited line with a one-line comment justifying the change. The expected final diff is two minimal hunks: one `agent.x402_supported = True` assignment in `tests/test_pages.py`, one `x402_supported=wallet is not None` kwarg in `tests/test_pages_x402.py`. <!-- sdd-owner: implementation -->

---

## Verification checklist (apply-time)

- [x] `uv run pytest tests/test_pages.py tests/test_pages_x402.py -vvv` → 0 failed (AC-1).
- [x] `uv run pytest` → failure count unchanged from pre-change baseline (AC-2).
- [x] `git diff -- app/ app/templates app/static app/routers` → empty (AC-3).
- [x] Every updated assertion string is a contiguous substring of the rendered HTML, not a paraphrase (AC-4).
- [x] No settings, env vars, or template files were mutated to mask drift (design §1, spec R-2).
