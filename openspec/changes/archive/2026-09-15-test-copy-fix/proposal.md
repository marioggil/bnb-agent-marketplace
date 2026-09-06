# Proposal: test-copy-fix — update 3 stale test asserts

## Change name
`test-copy-fix`

## Domain
Tests / copy drift

## Status
Draft

---

## Problem

3 tests fail because their `assert` strings don't match the current template/route copy. The drift happened when FU-2 x402 payment copy was finalized in commit `ffaad16 fix(ui): Payment field copy mirrors CTA — honest about x402 vs wallet` and `2b92290 fix(ui): honest CTA copy when wallet exists but x402 doesn't`. The tests were never updated.

## Failing tests (current `uv run pytest` output)

```
FAILED tests/test_pages.py::test_agent_detail_shows_hired_panel_and_hire_again
  assert "Hire again for $1.00" in body
  -> body has "Hire for $1.00" (no "again")

FAILED tests/test_pages_x402.py::test_cta_renders_price_and_is_enabled
  assert <price string>

FAILED tests/test_pages_x402.py::test_cta_disabled_without_wallet
  assert 'H...' (truncated in output)
```

## Root cause

`app/templates/pages/agent_detail.html` line ~570:

```jinja
Hire {% if my_paid_count %}again {% endif %}for ${{ '%.2f'|format(hire_price_usd) }}
```

This renders as:
- `Hire for $1.00` (when my_paid_count=0)
- `Hire again for $1.00` (when my_paid_count>=1)

The test `test_agent_detail_shows_hired_panel_and_hire_again` expects the "again" variant AFTER seeding a paid hire. Looking at the actual error output, the page rendered `Hire for $1.00` (no "again") even though `my_paid_count` was set to 1. Likely cause: the test's DB seed + the app's session use different sqlite connections, so the seeded hire isn't visible to the page render.

## Scope

**In**: update 3 test asserts to match current copy. Diagnose and fix the `my_paid_count` visibility issue if confirmed.

**Out**: anything beyond these 3 tests.

## Acceptance criteria

1. `uv run pytest tests/test_pages.py tests/test_pages_x402.py` → 0 failed
2. `uv run pytest` → no new failures (282 passed baseline preserved)
3. No production code changes (template/JS/router untouched)

## Risks

- **Low**: tests-only change, no runtime impact
- **Low**: if `my_paid_count` isn't visible from test client, the fix may need a small conftest tweak (still test-only)
