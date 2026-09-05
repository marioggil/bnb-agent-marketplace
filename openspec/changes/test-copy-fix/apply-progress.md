# Apply Progress: test-copy-fix

## Status

`completed` — all 6 tasks (T1–T6) implemented, targeted suite green, baseline preserved, production diff empty.

## Completed tasks

- [x] **T1** — `tests/test_pages.py::test_agent_detail_shows_hired_panel_and_hire_again`: inserted `agent.x402_supported = True` between the `agent.agent_wallet = …` assignment and `await db.commit()`. This flips `profile.hireable` to True so the template renders the enabled CTA branch (`Hire {% if my_paid_count %}again {% endif %}for $…`).
- [x] **T2** — `tests/test_pages_x402.py::_seed_one`: added `x402_supported=wallet is not None` kwarg to the `AgentCache(...)` constructor. Now `profile.hireable = agent.x402_supported AND agent.agent_wallet` evaluates consistently with the test intent (enabled when a wallet is supplied, disabled when `wallet=None`).
- [x] **T3** — Captured the rendered body of `/agents/56/2` via `uv run pytest tests/test_pages_x402.py::test_cta_disabled_without_wallet -vvv`. All four assertions matched the template line ~595 except the price literal. The body rendered `Hire for $1.03` (because `.env` sets `X402_FEE_WALLET=0x49D8d8a61A7807a8cC78b42A34FA223014518863` and `X402_FEE_AMOUNT_USD=0.03` — same drift the design §1/§2 anticipated when "a fee wallet env var is ever set in test"). Updated the literal to `$1.03` per AC-4 (no paraphrasing, no settings mutation). The other three assertions (`id="hire-cta"`, `disabled`, `no payment wallet (payTo) is registered`) already matched the template byte-for-byte.
- [x] **T4** — `uv run pytest tests/test_pages.py tests/test_pages_x402.py -vvv` → **34 passed, 0 failed** in 2.23s. All three previously-failing tests now green: `test_agent_detail_shows_hired_panel_and_hire_again`, `test_cta_renders_price_and_is_enabled`, `test_cta_disabled_without_wallet`.
- [x] **T5** — `uv run pytest` → **285 passed, 8 skipped (Postgres-only), 0 failed** in 12.83s. The 282 baseline + the 3 newly-passing tests = 285 (spec R-3: baseline preserved, no new failures, no new errors).
- [x] **T6** — `git diff -- app/ app/templates app/static app/routers` → empty. `git diff -- tests/conftest.py` → empty. Only `tests/test_pages.py` (+2, -1) and `tests/test_pages_x402.py` (+3, -2) were touched. Production code byte-for-byte unchanged (AC-3 satisfied).

## Files changed

```text
tests/test_pages.py      | 3 ++-  (+1 x402_supported line, +1 $1.00→$1.03 in "Hire again for $…", -1 old literal)
tests/test_pages_x402.py | 5 +++-- (+1 x402_supported kwarg, +2 $1.00→$1.03 literals, -2 old literals)
```

Two minimal hunks. No production code touched. No settings, env vars, or template files mutated.

## Test commands run

| Command | Result |
|---|---|
| `uv run pytest tests/test_pages_x402.py::test_cta_disabled_without_wallet -vvv` (T3, before price literal update) | 1 failed (price drift: rendered `$1.03`, asserted `$1.00`) |
| `uv run pytest tests/test_pages.py tests/test_pages_x402.py -vvv` (T4) | 34 passed in 2.23s |
| `uv run pytest` (T5) | 285 passed, 8 skipped, 0 failed in 12.83s |
| `git diff -- app/ app/templates app/static app/routers` (T6) | empty |
| `git diff -- tests/conftest.py` (T6) | empty |

## Deviations from design

One deviation from design §1/§2: the design predicted "no string changes needed" because it assumed the test env's `X402_FEE_AMOUNT_USD` was `0` (or unset). On this branch, `.env` configures `X402_FEE_AMOUNT_USD=0.03` with a non-empty fee wallet, so the rendered price is `$1.03` and three price literals needed to be bumped from `$1.00` to `$1.03`. The design explicitly anticipated this path ("If `X402_FEE_AMOUNT_USD=0.03` and a fee wallet get set in conftest later, the price will read `$1.03` and this assertion becomes the one to update"). No settings were mutated to mask the drift; this matches design §1 ("Capture the body, read the substring `$X.XX`, update the assertion only if the rendered price differs from `1.00`. Do not mutate settings to mask drift").

## Remaining tasks

None. All T1–T6 and the verification checklist are complete.

## Workload / PR boundary

- Estimated changed lines: **3 hunks across 2 files** (~5 insertions, ~3 deletions).
- 400-line budget risk: **Low**.
- Chained PRs recommended: **No**.
- Delivery strategy: **single-pr**, single commit.
- Commit message: `test: fix 3 stale test asserts (x402_supported seed)` (parent prompt).

## Structured status consumed

- `applyState`: cleared (no blockers).
- `dependencies`: none.
- `blockedReasons`: none.
- `actionContext`: workspace-mode (not workspace-planning); no edit-root gating required for this scope.
- Authoritative status path: `openspec/changes/test-copy-fix/` (spec/design/tasks all present).

## Acceptance Criteria — final status

| AC | Description | Status |
|---|---|---|
| AC-1 | Targeted suite green | ✅ 34 passed |
| AC-2 | Baseline preserved (no new failures/errors) | ✅ 285 passed (282 baseline + 3 newly-passing) |
| AC-3 | Production diff empty | ✅ `git diff -- app/ app/templates app/static app/routers` empty; `tests/conftest.py` empty |
| AC-4 | Assertions are contiguous substrings of rendered HTML | ✅ All 3 updated price literals match `$1.03` exactly as rendered; no paraphrasing |