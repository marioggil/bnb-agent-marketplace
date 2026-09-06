# Tasks — Phase 1b (UI + additive API fields)

**Sub-PR:** `1b`
**Branch:** `feat/compliance-flags-ui`
**Base:** `main` (post-1a-ii merge)
**Stack slot:** 3rd PR
**Depends on:** `1a-i` + `1a-ii` merged

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~156 |
| 400-line budget risk | Low (60% headroom) |
| Chained PRs recommended | Yes (this is 3 of 3) |
| Suggested split | n/a |
| Delivery strategy | ask-on-risk |
| Chain strategy | stacked-to-main |

```text
Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: Low
```

## Dependency statement

This sub-PR **requires 1a-i AND 1a-ii merged to `main`** because:
- `pages.py::agent_detail` reads `agent_compliance_flags` rows (table created by 1a-i; rows written by 1a-ii's orchestrator).
- `agents.py::get_agent_score` reads `agent_cache.compliance_penalty` (column added by 1a-i; populated by 1a-ii).
- The pre-seeding fixture `compliance_seed` (design §4.6) inserts one `agent_cache` row + one `agent_compliance_flags` row via raw SQL so the tests work even on a branch that rebased on a pre-merge fork.

## Task ordering rationale

API shape before UI so the schema `ScoreOut` field additions are landed before the page template references them. Inside API: score endpoint before UI. Inside UI: server-side page read before template so the route's context dict is already populated when the template assertions run.

---

## Tasks

- [x] **T1 — RED: `test_score_endpoint_includes_compliance_penalty`.** Create `tests/test_compliance_api.py`. Use the `compliance_seed` fixture (design §4.6) that inserts one `agent_cache` row with `activity_score=72.50, compliance_penalty=30.00` and one `agent_compliance_flags` row. Call `GET /api/agents/56/1/score`. Assert response JSON includes key `compliance_penalty`. Run `uv run pytest tests/test_compliance_api.py::test_score_endpoint_includes_compliance_penalty -x`. Expected: assertion fails because the key is missing from `ScoreOut`. (RED: schema lacks the field.) <!-- sdd-owner: implementation -->

- [x] **T2 — GREEN: add `compliance_penalty` + `displayed_activity_score` to `ScoreOut`.** Modify `app/schemas/score.py`. In the `ScoreOut` model add `compliance_penalty: float = 0.0` and `displayed_activity_score: float = 0.0`. **Do NOT** remove or rename any existing field. Re-run `uv run pytest tests/test_compliance_api.py::test_score_endpoint_includes_compliance_penalty`. Expected: still fails (T3 covers the populate step) — partial GREEN at this step means the field is in the schema; route handler populate is T4. Files touched: `app/schemas/score.py` (~2 lines). <!-- sdd-owner: implementation -->

- [x] **T3 — RED: `test_displayed_activity_score_subtracts_penalty`.** In `tests/test_compliance_api.py` add the case. Seed `activity_score=72.50`, `compliance_penalty=30.00`, call `GET /api/agents/56/1/score`, assert `displayed_activity_score == 42.5` (`max(0, 72.5 - 30.0)`); assert `activity_score == 72.5` (canonical, unchanged); assert `compliance_penalty == 30.0`. Run `uv run pytest tests/test_compliance_api.py -k displayed -x`. Expected: `displayed_activity_score` is `0.0` from T2 default (route handler not populating yet). RED evidence captured. <!-- sdd-owner: implementation -->

- [x] **T4 — GREEN: populate in `get_agent_score`.** Modify `app/routers/agents.py`. In the existing `get_agent_score` handler (around lines 234–273), after the existing `ScoreOut(...)` construction, add (or extend the existing constructor): `compliance_penalty=float(row.compliance_penalty or 0)`, `displayed_activity_score=max(0.0, float(row.activity_score or 0) - float(row.compliance_penalty or 0))`. Do NOT change `activity_score` itself. Re-run `uv run pytest tests/test_compliance_api.py -k displayed`. Expected: passes. Files touched: `app/routers/agents.py` (~4 lines). <!-- sdd-owner: implementation -->

- [x] **T5 — RED: extend `tests/test_pages.py` with `test_agent_detail_hire_cta_disabled_when_both_compliance_flags_set`.** In `tests/test_pages.py` extend (additive — do not edit existing cases) with a `_seed_compliance_agent(agent_id, creator_flagged, owner_flagged)` helper exported via `tests/_compliance_fixtures.py`. Seed: one `agent_cache` row, one `agent_compliance_flags` row with `creator_flagged=true`, `owner_flagged=true`. Call `GET /agents/56/1` (detail page). Assert response body contains:
  - `id="hire-cta"` AND `disabled` substring (both on the same `<button>` element)
  - `aria-disabled="true"`
  - The OFAC block banner string `Hiring is disabled while OFAC compliance is unresolved for this agent`
  
  Run `uv run pytest tests/test_pages.py -k both_compliance_flags_set -x`. Expected: assertion fails because the current `agent_detail` template has no `disabled` on `#hire-cta`. (RED: route doesn't pass the flag yet.) <!-- sdd-owner: implementation -->

- [x] **T6 — GREEN: read `agent_compliance_flags` in `agent_detail`.** Modify `app/routers/pages.py::agent_detail` (around line 770–880). After the existing `flagged_addresses = await _flagged_addresses_set()` line (~line 856), add a single-row `SELECT * FROM agent_compliance_flags WHERE agent_id = :id` (use `select(AgentComplianceFlag).where(...)`), compute `creator_flagged`, `owner_flagged`, `creator_is_owner`, `displayed_activity_score = max(Decimal("0.00"), Decimal(str(local_score or 0)) - compute_penalty(creator, owner))`, `compliance_penalty = compute_penalty(creator, owner)`. Pass these into the existing template context dict along with `creator_flagged` + `owner_flagged`. Import `from app.db.models.agent_compliance import AgentComplianceFlag` and `from app.services import compliance_refresh`. Files touched: `app/routers/pages.py` (~22 lines). Re-run `uv run pytest tests/test_pages.py -k both_compliance_flags_set`. Expected: still fails (T5 fails on template — handler populates context but template doesn't render). <!-- sdd-owner: implementation -->

- [x] **T7 — RED: `test_agent_detail_hire_cta_enabled_when_only_one_flag_set`.** In `tests/test_pages.py` add: seed `creator_flagged=true`, `owner_flagged=false` (creator-only). Call `GET /agents/56/1`. Assert body contains a warning substring (e.g. `OFAC warning: creator address flagged`) but does NOT contain `disabled` on `#hire-cta` and does NOT contain `aria-disabled="true"`. Run `uv run pytest tests/test_pages.py -k only_one_flag_set -x`. Expected: fails — current template has no warning copy. (RED: template lacks the warning render.) <!-- sdd-owner: implementation -->

- [x] **T8 — GREEN: render banner + `disabled` in template.** Modify `app/templates/pages/agent_detail.html`. Three localized edits per design §4.2:
  1. **OFAC banner** (insert immediately before `#hire-cta`, ~line 510): three-branch `{% if creator_flagged and owner_flagged %}OFAC: hiring blocked — Hiring is disabled while OFAC compliance is unresolved for this agent.{% elif creator_flagged %}OFAC warning: creator address flagged. Hiring remains enabled.{% elif owner_flagged %}OFAC warning: owner address flagged. Hiring remains enabled.{% endif %}`.
  2. **`#hire-cta` `disabled` + `aria-disabled="true"`** (toggle only when both flags): `<button id="hire-cta" … {% if creator_flagged and owner_flagged %}disabled aria-disabled="true"{% endif %}>Hire …</button>`.
  3. **Compliance badge on Activity score card** (~lines 287–304): change the score line to `{{ displayed_activity_score or 'n/a' }}/100` and add `{% if compliance_penalty > 0 %}<span class="badge risk">⚠ Compliance: −{{ '%.2f'|format(compliance_penalty) }} pts</span>{% endif %}`.
  
  Re-run `uv run pytest tests/test_pages.py -k both_compliance_flags_set or only_one_flag_set`. Expected: both pass. Files touched: `app/templates/pages/agent_detail.html` (~18 lines). <!-- sdd-owner: implementation -->

- [x] **T9 — TRIANGULATE: badge substring + clip-to-zero + clean agent.** Add to `tests/test_compliance_api.py`:
  - `test_score_endpoint_clip_to_zero_when_penalty_exceeds_activity`: seed `activity_score=20.00, compliance_penalty=30.00`; assert `displayed_activity_score == 0.0` (clipped, never negative).
  - `test_score_endpoint_clean_agent_zero_penalty`: seed `compliance_penalty=0.00`; assert `displayed_activity_score == activity_score`.
  - `test_agent_endpoint_exposes_compliance_penalty`: `GET /api/agents/56/1` returns JSON with `compliance_penalty` key on the `AgentOut` shape (additive — schema equivalent if `AgentOut` has the field; otherwise document the field as `ScoreOut`-only and add a contract test pinning that boundary).
  - `test_agent_detail_compliance_penalty_badge_renders_with_negative_value`: seed `activity_score=72.50`, `compliance_penalty=30.00`; assert body contains both `42.50` (displayed) and `Compliance: -30.00 pts` (badge substring).
  
  Run `uv run pytest tests/test_compliance_api.py tests/test_pages.py -k "compliance or penalty or both or only_one"`. Expected: all green. <!-- sdd-owner: implementation -->

- [x] **T10 — Final verification.** Run:
  1. `uv run pytest tests/test_compliance_penalty.py tests/test_compliance_models.py tests/test_compliance_refresh.py tests/test_admin_compliance.py` → all green (1a-i + 1a-ii untouched).
  2. `uv run pytest tests/test_pages.py tests/test_pages_x402.py tests/test_score_api.py tests/test_compliance_api.py tests/test_agent_score.py` → all green.
  3. `uv run pytest` (full suite) → baseline `285 passed, 8 skipped` preserved + new tests pass.
  4. `git diff -- migrations/ app/db/models/ app/services/ app/routers/admin.py app/main.py` → empty (1a-i + 1a-ii own these).
  5. `app/static/js/payment.js` byte-identical (verify with `git diff -- app/static/js/payment.js` → empty).
  6. `git diff --stat -- app/ tests/` → ≤ 400 net lines per file (low risk for 1b).
  7. **Manual**: `GET /agents/56/1` against a seeded dual-flag agent renders `#hire-cta` with `disabled` + block banner; single-flag renders warning copy only and CTA stays enabled; clean agent renders neither. <!-- sdd-owner: implementation -->

---

## REFACTOR notes

- `_seed_compliance_agent` lives in `tests/_compliance_fixtures.py` (new tiny helper module) so `tests/test_pages.py` and `tests/test_compliance_api.py` share one row seeding implementation.
- `agent_detail` read path: do not loop; keep one `SELECT * FROM agent_compliance_flags WHERE agent_id = :id` — design §5.2 N+1 invariant.
- Template: use the existing `.badge.risk` class (already styled for owner/payment compliance markers). Do not introduce new CSS.

## Evidence line (each task)

Each GREEN completion confirmed by the explicit `uv run pytest -k <marker>` invocation in the task body. RED first-failures capture the missing schema field (T1), missing template `disabled` (T5), or missing warning copy (T7).

## Verification command (one-shot, run at end of 1b)

```bash
uv run pytest tests/test_compliance_penalty.py tests/test_compliance_models.py tests/test_compliance_refresh.py tests/test_admin_compliance.py tests/test_pages.py tests/test_pages_x402.py tests/test_score_api.py tests/test_compliance_api.py tests/test_agent_score.py -v && \
uv run pytest && \
git diff -- app/static/js/payment.js app/services/flagged_sync.py app/services/agent_score.py | wc -l
```

Expected final output: `0` (zero changed lines in `payment.js`, `flagged_sync.py`, `agent_score.py`).
