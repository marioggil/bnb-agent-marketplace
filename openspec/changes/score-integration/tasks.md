# Tasks: score-integration

**Change:** `score-integration`
**Sub-PR:** single PR (independent slice, Phase 3 of `compliance-flags` → `wallet-activity` chain)
**Branch:** `feat/score-integration` (stacked on `feat/wallet-activity`, not yet merged to `master`)
**Base:** `feat/wallet-activity`
**Strategy:** stacked-to-main · strict TDD · `uv run pytest`
**Test discipline:** RED → GREEN → TRIANGULATE → REFACTOR with targeted `-k` invocations; full suite only at the end.

---

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~280 (1 schema + 1 router handler + 1 template wrap + DESIGN.md section + 3 new test files) |
| 400-line budget risk | Low (single PR well under 400-line budget) |
| Chained PRs recommended | No (single-slice additive change) |
| Suggested split | n/a — single PR |
| Delivery strategy | ask-on-risk |
| Chain strategy | stacked-to-main |

```text
Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: stacked-to-main
400-line budget risk: Low
```

> **Operator decision NOT required before apply** — single additive slice, low cross-cutting surface (one new endpoint, one new schema model, one UI wrapper, one doc section). The 400-line budget is comfortable; mitigation is to keep `tests/test_score_ranking_api.py` / `_negative.py` / `_block.py` lean and avoid reformatting existing files.

---

## Dependency statement

- **No new migration, no new `agent_cache` column, no new table.** Three pre-existing signals are reused: `agent_cache.{activity_score, compliance_penalty}`, `agent_compliance_flags.creator_is_owner`. `displayed_activity_score` is computed at SELECT time via `func.greatest(activity_score - compliance_penalty, 0)`.
- **No scheduler / cron work** — read-side at request time only.
- **No new fixture**: reuse `client` + `db` from `tests/conftest.py` and the seeders in `tests/test_wallet_activity_pages.py`.
- **Route order**: `rank_agents_score` MUST be declared BEFORE `/{chain_id}/{token_id}` in `app/routers/agents.py` (same key learning as `/compare` at line 211 — the literal `score` segment would otherwise be swallowed by the path-param route).

## Task ordering rationale

Schema first (RED → GREEN for `ScoreOutMinimal`), then router (RED → GREEN for shape + category + sort + limit + negative-assertion contract), then UI (RED → GREEN for section wrapper + OFAC ordering), then `DESIGN.md` (RED → GREEN for the new section), then full-suite verification. Each pair is one focused session.

---

## Tasks

- [ ] **T1 — RED: `tests/test_score_ranking_api.py::test_score_endpoint_returns_minimal_projection`.** New file `tests/test_score_ranking_api.py`. Seed one `AgentCache` row (chain 56, token 1, category `other`). Call `GET /api/agents/score?category=other&sort=displayed_activity_score&limit=5`. Assert response status 200 and `items[0]` keys ⊇ `{chain, token, name, activity_score, compliance_penalty, displayed_activity_score, creator_is_owner}`. Run `uv run pytest tests/test_score_ranking_api.py -k minimal_projection -x`. Expected: `AssertionError` or `ImportError` on missing `ScoreOutMinimal`. RED captured. <!-- sdd-owner: implementation -->

- [ ] **T2 — GREEN: add `ScoreOutMinimal` to `app/schemas/score.py`.** Append a new `BaseModel` subclass with exactly 7 fields: `chain: int`, `token: int`, `name: str | None = None`, `activity_score: Decimal`, `compliance_penalty: float = 0.0`, `displayed_activity_score: float = 0.0`, `creator_is_owner: bool = False`. Add `"ScoreOutMinimal"` to `__all__`. **Do NOT** touch `ScoreOut`. Re-run `uv run pytest tests/test_score_ranking_api.py -k minimal_projection`. Expected: passes. Files touched: `app/schemas/score.py` (~15 lines). <!-- sdd-owner: implementation -->

- [ ] **T3 — RED: `test_score_endpoint_filters_by_category`.** In `tests/test_score_ranking_api.py` add: seed agents in `category="other"` and `category="rebalancing"`. Call `GET /api/agents/score?category=other&sort=displayed_activity_score`. Assert `items` contains only `other` rows. Run `uv run pytest tests/test_score_ranking_api.py -k filters_by_category -x`. Expected: fails (no endpoint yet — T4 covers handler). RED captured. <!-- sdd-owner: implementation -->

- [ ] **T4 — GREEN: implement `rank_agents_score` in `app/routers/agents.py` BEFORE `/{chain_id}/{token_id}`.** Add the handler near `list_agents` (around line 53). Extend `_SORT_KEYS` with `"displayed_activity_score": <expr>`. Import `AgentComplianceFlag` and `func`. Build the `SELECT` from design §3: `func.greatest(AgentCache.activity_score - AgentCache.compliance_penalty, 0).label("displayed_activity_score")`, `LEFT JOIN agent_compliance_flags ON agent_id`, `WHERE category = :cat` (omitted when None), `ORDER BY displayed.desc().nullslast()`, `LIMIT/OFFSET`. Literal-validate `category` (`"rebalancing" | "other"`, empty → no filter, unknown → 400) and `sort` (`"displayed_activity_score"` only, unknown → 400). `limit` default 20, clamp to 100, reject negative/non-int with 400. Return `Page[ScoreOutMinimal]`. **Route order critical**: declare `@router.get("/score", ...)` BEFORE any `/{chain_id}/{token_id}` route in the file. Re-run `uv run pytest tests/test_score_ranking_api.py -k filters_by_category`. Expected: passes. Files touched: `app/routers/agents.py` (~55 lines). <!-- sdd-owner: implementation -->

- [ ] **T5 — RED: `test_score_endpoint_orders_by_displayed_activity_score_desc`.** In `tests/test_score_ranking_api.py` add: seed A=`displayed=90`, B=NULL, C=`50` (set `activity_score` + `compliance_penalty=0` for simplicity). Call `GET /api/agents/score?category=other&sort=displayed_activity_score&limit=10`. Assert `items[0].name == A`, `items[1].name == C`, `items[2].name == B` (NULL last). Run `uv run pytest tests/test_score_ranking_api.py -k orders_by_displayed -x`. Expected: fails (T4 handler may sort but NULL handling unverified). RED captured. <!-- sdd-owner: implementation -->

- [ ] **T6 — RED: `test_score_endpoint_default_limit_20_max_100`.** In `tests/test_score_ranking_api.py` add two cases: (a) seed 25 agents → default `limit` returns 20; (b) seed 150 agents, call with `limit=200` → 100 items returned and status 200 (silent clamp). Run `uv run pytest tests/test_score_ranking_api.py -k default_limit -x`. Expected: fails if handler clamps `limit` wrongly. RED captured. <!-- sdd-owner: implementation -->

- [ ] **T7 — RED: `tests/test_score_ranking_negative.py::test_score_endpoint_negative_assertion_zero_wallet_queries`.** New file `tests/test_score_ranking_negative.py`. Reuse the `before_cursor_execute` listener pattern from `tests/test_wallet_activity_pages.py:172-186`: `from app.db import session as session_module; engine = session_module.engine.sync_engine; events: list[str] = []; @event.listens_for(engine, "before_cursor_execute") def _cap(...): events.append(statement)`. Also monkey-patch `app.routers.agents.fetch_wallet_signals` with a counter wrapper. Call `GET /api/agents/score?category=other&sort=displayed_activity_score&limit=10`. Assert: `counter == 0` AND no captured statement contains substring `onchain_transfers` (case-insensitive) AND response status 200. Run `uv run pytest tests/test_score_ranking_negative.py -k zero_wallet_queries -x`. Expected: listener fires with 0 wallet hits already (handler doesn't import `fetch_wallet_signals`), but counter-wrapper assertion MUST also be green — the test pins the contract. RED captured if monkey-patch surfaces a missing import. <!-- sdd-owner: implementation -->

- [ ] **T8 — GREEN: ensure `rank_agents_score` does NOT import or call `fetch_wallet_signals`.** In `app/routers/agents.py` verify the new handler has no `from app.services.wallet_activity import …` and no `fetch_wallet_signals(...)` call. The handler reads `compliance_penalty` and `creator_is_owner` directly from columns populated by Phase 1 + Phase 2. No new imports needed. Re-run `uv run pytest tests/test_score_ranking_negative.py`. Expected: passes; the listener captures 0 wallet-related statements. <!-- sdd-owner: implementation -->

- [ ] **T9 — RED: `tests/test_trust_compliance_block.py::test_detail_page_renders_three_elements_in_trust_compliance_section`.** New file `tests/test_trust_compliance_block.py`. Seed agent with `creator_flagged=True`, `owner_flagged=False`, `wallet_activity_score` populated. Call `GET /agents/{chain}/{token}`. Parse response body via `lxml.html.fromstring`. Assert exactly one `<section class="trust-compliance">` exists. Assert `.ofac-warn`, `.wallet-activity-chip`, `.activity-score-value` are all descendants. Run `uv run pytest tests/test_trust_compliance_block.py -k three_elements_in_section -x`. Expected: zero `.trust-compliance` matches (current scattered layout — T10 covers wrap). RED captured. <!-- sdd-owner: implementation -->

- [ ] **T10 — GREEN: edit `app/templates/pages/agent_detail.html` to wrap the 3 elements in `<section class="trust-compliance">`.** Lift the OFAC banner block (current lines ~598-608) up to sit immediately above the existing score card (current line ~179). Lift the wallet chip (lines ~191-199) out of the inner `#activity-score` card. Wrap the OFAC banner + chip + score card `<h2>Activity score</h2>` + `.activity-score-value` + compliance badge + `local_breakdown` + `latest_probe` blocks in a single `<section class="trust-compliance">` opening + closing pair. **No new CSS classes**. `pages.py::agent_detail` stays byte-identical — the template wraps existing locals verbatim. Re-run `uv run pytest tests/test_trust_compliance_block.py -k three_elements_in_section`. Expected: passes. Files touched: `app/templates/pages/agent_detail.html` (~10 lines net — wrapping only). <!-- sdd-owner: implementation -->

- [ ] **T11 — RED: `test_trust_compliance_block_ofac_banner_first`.** In `tests/test_trust_compliance_block.py` add: assert DOM order inside `.trust-compliance` is `.ofac-warn` → `.wallet-activity-chip` → `.activity-score-value`. Use `lxml.html` parent traversal: walk children of `.trust-compliance` in document order, verify the relative positions. Also assert `.ofac-warn` precedes `#hire-cta` in full document (DOM order check). Run `uv run pytest tests/test_trust_compliance_block.py -k ofac_banner_first -x`. Expected: ordering depends on T12 — currently OFAC lives near `#hire-cta` so it precedes chip but is outside the section. RED captured. <!-- sdd-owner: implementation -->

- [ ] **T12 — GREEN: move OFAC banner block to be the FIRST child of `.trust-compliance`.** In `app/templates/pages/agent_detail.html` reorder the three children inside the new section so the `{% if creator_flagged and owner_flagged %}…{% elif creator_flagged or owner_flagged %}…{% endif %}` block is the first child, then the `{% if wallet_activity_score is not none %}<div class="wallet-activity-chip">…</div>{% endif %}` block, then the score card heading + value + breakdown. The wallet chip and score card order is preserved from the current layout (chip sits above score card). Re-run `uv run pytest tests/test_trust_compliance_block.py -k "ofac_banner_first or three_elements_in_section"`. Expected: passes; section now contains exactly OFAC → chip → score in that DOM order. <!-- sdd-owner: implementation -->

- [ ] **T13 — RED: `test_design_md_has_score_architecture_v2_section`.** In `tests/test_trust_compliance_block.py` (or a new `tests/test_design_doc.py`) add: read `DESIGN.md`, assert it contains the literal line `## Score architecture v2`, assert the section body (lines from that heading to the next `## ` heading or EOF) is ≤50 lines, and contains the substring `listing pages MUST NOT aggregate wallet signals`. Run `uv run pytest -k score_architecture_v2 -x`. Expected: `AssertionError` — section absent (T14 covers append). RED captured. <!-- sdd-owner: implementation -->

- [ ] **T14 — GREEN: append `## Score architecture v2` section to `DESIGN.md`.** Append the verbatim block from design.md §10 under the existing `## Decisions` heading near `## Agent activity score`. ≤50 lines. Names all three signals (activity / compliance / wallet), distinguishes `agent_cache.compliance_penalty` (stored) from `wallet_activity_score` (per-row derived in v1), and contains the literal substring `listing pages MUST NOT aggregate wallet signals`. Re-run `uv run pytest -k score_architecture_v2`. Expected: passes. Files touched: `DESIGN.md` (~45 lines append). <!-- sdd-owner: implementation -->

- [ ] **T15 — Final verification.** Run from repo root:
  1. `uv run pytest tests/test_score_ranking_api.py tests/test_score_ranking_negative.py tests/test_trust_compliance_block.py -v` → all green.
  2. `uv run pytest` (full suite) → baseline `388 passed` preserved + new tests grow the count without flaking; no existing test weakened.
  3. `git diff -- migrations/ app/services/wallet_activity.py app/services/compliance_refresh.py app/services/agent_score.py` → empty (scope guard: Phase 1 + Phase 2 + `agent_score` untouched).
  4. `git diff --stat -- app/ tests/ DESIGN.md` → ≤ 400 net lines total; total diff within forecast (~280).
  5. **Manual smoke**: `GET /api/agents/score?category=other&sort=displayed_activity_score&limit=5` returns 7-field items; `GET /api/agents/score?limit=200` clamps to 100; `GET /agents/{chain}/{token}` renders the `.trust-compliance` section with OFAC → chip → score order; existing `/` and `/agents` listings unchanged.
  6. **Scope guard**: `migrations/versions/`, `app/db/models/agent.py`, `app/services/wallet_activity.py`, `app/services/compliance_refresh.py`, `app/services/agent_score.py`, `app/static/css/site.css`, `ScoreOut` model all byte-identical to `feat/wallet-activity` — confirm via `git diff -- feat/wallet-activity -- <paths>`. <!-- sdd-owner: implementation -->

---

## REFACTOR notes

- `ScoreOutMinimal` is a **sibling** of `ScoreOut`, not a subclass. Adding a field to `ScoreOut` MUST NOT silently leak into the ranking response. Tests pin the seven fields; AC-3 contract holds.
- `app/routers/agents.py::rank_agents_score` reads `compliance_penalty` from `AgentCache.compliance_penalty` (Phase 1 column) and `creator_is_owner` from `agent_compliance_flags.creator_is_owner` (Phase 2 column). The LEFT JOIN is the load-bearing detail — `creator_is_owner` lives in the per-agent table, not on `agent_cache`.
- `displayed_activity_score` is computed at SELECT time via `func.greatest(activity_score - compliance_penalty, 0)` — NOT a stored column. The same label is used in `ORDER BY`. SQLite supports `MAX(a-b, 0)`; PG supports `GREATEST`; the test engine is sqlite.
- `agent_detail.html`: ONE new wrapper class `.trust-compliance`. The existing `.ofac-block` / `.ofac-warn` / `.wallet-activity-chip` / `.activity-score-value` / `.badge` classes are reused unchanged. **Do NOT touch** the stylesheet.
- `DESIGN.md`: append-only — do NOT reformat or rewrite existing entries. Place the new section under `## Decisions` near `## Agent activity score`.

## Evidence line (each task)

Each GREEN completion confirmed by the explicit `uv run pytest -k <marker>` invocation in the task body. RED first-failures capture the missing schema (T1), missing handler (T3), missing sort handling (T5), missing limit clamp (T6), missing listener wiring (T7), missing section wrapper (T9), missing OFAC ordering (T11), and missing DESIGN.md section (T13).

## Verification command (one-shot, run at end)

```bash
uv run pytest tests/test_score_ranking_api.py tests/test_score_ranking_negative.py tests/test_trust_compliance_block.py -v && \
uv run pytest && \
git diff -- migrations/ app/services/wallet_activity.py app/services/compliance_refresh.py app/services/agent_score.py app/db/models/agent.py | wc -l
```

Expected final output: `0` (zero changed lines in `migrations/`, the three Phase 1+2 service modules, and the agent model — scope guard held).