# Proposal: agent-score-integration — ranking endpoint + trust-compliance UI block

**Change:** `agent-score-integration`
**Domain:** `agent-detail-ui` (EXTENSION — Phase 3 of the compliance-flags → wallet-activity chain)
**Branch:** `feat/score-integration` (stacked on `feat/wallet-activity`, not yet merged to master)
**Status:** Draft

---

## Problem

Phase 1 (`compliance-flags`, archived 2026-09-15) added `compliance_penalty` and
`displayed_activity_score` to `ScoreOut`. Phase 2 (`wallet-activity`, archived
2026-09-15) layered `wallet_activity_score`, `wallet_activity_breakdown`, and
`creator_is_owner` on top. The data model is complete — three signals describe
every agent: canonical activity, OFAC compliance, and creator/owner wallet
footprint. Two product gaps remain.

First, there is no ranking surface that exposes `displayed_activity_score`.
`GET /api/agents` (`app/routers/agents.py:53-105`) sorts on `average_score` /
`activity_score` / `total_feedbacks` / `created_at` / `name` — none account
for the OFAC penalty. An `activity_score=85, compliance_penalty=30` agent
ranks the same as `activity_score=85, compliance_penalty=0`. Hiring users
cannot ask "show me the highest-displayed-scored agents in this category".

Second, the detail page renders the three signals in three disconnected
locations. `app/templates/pages/agent_detail.html` shows the OFAC banner near
the Hire CTA (lines 598-608), the activity score card near the top of the body
(line 179), and the wallet-activity chip inside the score card (lines 191-199).
A user correlating "this score dropped because of OFAC" has to scan the page
top-to-bottom. The three elements belong in one trust-compliance block, in
fixed order (OFAC first — security-critical; wallet next — context; score last
— the headline).

## Existing evidence

- `openspec/changes/score-integration/explore.md` §1 — current `ScoreOut` shape
  (8 fields + `pillars` + `breakdown`), three scattered render sites, no
  `/score` ranking variant.
- `openspec/changes/score-integration/explore.md` §3 — option (c) for the
  Phase 2 negative-assertion contract: ranking endpoint ranks on
  `displayed_activity_score` only; clients fetch full `ScoreOut` via the
  existing per-agent `/score` endpoint.
- `openspec/changes/archive/2026-09-15-compliance-flags-1b/proposal.md` —
  `compliance_penalty` column, `displayed_activity_score` field, OFAC banner,
  Hire-CTA `disabled` semantics, reused verbatim.
- `openspec/changes/archive/2026-09-15-wallet-activity/proposal.md` — wallet
  helper, `fetch_wallet_signals`, additive `ScoreOut` fields. Ranking endpoint
  MUST NOT call `fetch_wallet_signals`.
- `app/routers/agents.py:53-105` — existing `list_agents` whitelisted sort
  keys; new endpoint reuses the same `category` Literal.
- `app/schemas/score.py:56-82` — current `ScoreOut`. `ScoreOutMinimal` is a
  sibling model, not a replacement.
- `tests/test_wallet_activity_pages.py` — load-bearing negative assertion
  that the listing path does not aggregate wallet signals.

## What needs to change

1. **New endpoint** `GET /api/agents/score` in `app/routers/agents.py` →
   `Page[ScoreOutMinimal]`. Query params: `category` (Literal matching
   `list_agents`), `sort=displayed_activity_score` (single whitelisted value
   for v1), `limit` (default 20, max 100), `offset` (default 0). One SQL
   `SELECT` against `AgentCache` ordered by `displayed_activity_score DESC
   NULLS LAST`. The endpoint never calls `fetch_wallet_signals`; it never
   reads `OnchainTransfer`.

2. **New schema** `ScoreOutMinimal` in `app/schemas/score.py`:
   `{chain, token, name, activity_score, compliance_penalty,
   displayed_activity_score, creator_is_owner}`. Wallet signals and the heavy
   `pillars` / `breakdown` are deliberately omitted; clients follow up with
   `GET /api/agents/{chain}/{token}/score` for detail.

3. **UI consolidation** in `app/templates/pages/agent_detail.html` — wrap the
   three existing rendering sites in a single `<section class="trust-compliance">`
   block in fixed order: OFAC banner → wallet-activity chip → activity score
   card. No new CSS classes; reuse `.ofac-block`, `.ofac-warn`,
   `.wallet-activity-chip`, `.activity-score-value`, `.badge`.

4. **`DESIGN.md` addition** — new `## Score architecture v2` section (<50 lines)
   under "Decisions". Diagrams the three signals, names canonical vs derived
   columns (`agent_cache.compliance_penalty` is stored;
   `wallet_activity_score` is per-row derived), pins the Phase 2 listing-page
   negative-assertion invariant.

5. **Optional index** `ix_agent_cache_category_displayed(category,
   displayed_activity_score DESC NULLS LAST)` as a follow-on Alembic
   migration. **Deferrable** — at <1000 agents per category the planner is
   fine. AC documents the threshold for revisiting.

## Scope

### In scope

- `GET /api/agents/score` router + handler in `app/routers/agents.py`.
- `ScoreOutMinimal` Pydantic model in `app/schemas/score.py`.
- `<section class="trust-compliance">` wrapper in
  `app/templates/pages/agent_detail.html` (OFAC → wallet → score).
- `## Score architecture v2` section in `DESIGN.md`.
- Tests (strict TDD):
  - `tests/test_score_ranking_api.py` — `Page[ScoreOutMinimal]` shape, sort
    order, category filter, limit/offset, max-limit clamp, default limit,
    category-Literal validation, ordering `DESC NULLS LAST`.
  - `tests/test_score_ranking_negative.py` — ranking endpoint never calls
    `fetch_wallet_signals` and never reads `onchain_transfers` (mock count
    == 0 per request).
  - `tests/test_trust_compliance_block.py` — OFAC banner, wallet chip, and
    score card are all descendants of `.trust-compliance` in OFAC → wallet
    → score order.

### Out of scope

- Mutating `activity_score`, `pillars`, or `composite_score`.
- A wallet orchestrator that pre-computes `wallet_activity_score` into a new
  `agent_cache` column (option (b) from explore §3 — deferred).
- Listing-page chips on `/`, `/agents`, `/agents/{chain}` — the ranking
  endpoint is the listing surface for v1.
- `creator_is_owner`-based downranking in SQL — the field is in the minimal
  response so clients can apply it; the server does not.
- New `wallet_score` (8004scan mirror) column or recomputation logic.
- Real scheduler / cron for compliance refresh (per Phase 1 archive).
- Any change to `app/routers/pages.py::agent_detail` (template wraps existing
  locals; no new locals).

## Acceptance criteria

1. **AC-1 — Endpoint shape.** `GET /api/agents/score?category=other` returns
   `Page[ScoreOutMinimal]` with `total`, `page`, `page_size`, `items`. Default
   `limit=20`. `limit=200` clamps to 100. Unknown `sort` → `400`.
2. **AC-2 — Sort + category.** `sort=displayed_activity_score` orders results
   `DESC NULLS LAST`. Missing/empty `category` returns all agents; unknown
   `category` → `400` (Literal validation).
3. **AC-3 — Minimal projection.** Each item contains exactly the seven fields
   above. `wallet_activity_score`, `wallet_activity_breakdown`, `pillars`, and
   `breakdown` are NOT present. Test pins via `model_json_schema()` diff.
4. **AC-4 — Phase 2 negative assertion preserved.** During one full request
   to `GET /api/agents/score`, the test asserts `fetch_wallet_signals` is
   called **0** times and no SQL statement touches `onchain_transfers`.
   Existing `tests/test_wallet_activity_pages.py` continues to pass.
5. **AC-5 — Trust-compliance block.** Rendered `agent_detail.html` for a
   flagged-creator + clean-owner + scored agent contains exactly one
   `<section class="trust-compliance">`. Inside that section, the OFAC banner
   precedes `.wallet-activity-chip` which precedes the activity score card.
   Test asserts DOM order via lxml/BeautifulSoup.
6. **AC-6 — `DESIGN.md` section.** `DESIGN.md` contains `## Score architecture
   v2`, ≤50 lines, names all three signals, names canonical vs derived columns,
   and contains the literal substring `listing pages MUST NOT aggregate wallet
   signals`.
7. **AC-7 — Baseline preserved.** All 348+ tests (current baseline after
   Phases 1+2) continue to pass. No existing assertion in `tests/test_pages.py`,
   `tests/test_pages_x402.py`, or `tests/test_score_api.py` is weakened.

## Risks

1. **R-1 (low) — Option (c) trade-off.** Ranking endpoint returns no wallet
   signals; a client that only calls `GET /api/agents/score` cannot see the
   creator/owner wallet footprint. **Mitigation:** `creator_is_owner` is in
   the minimal response, and the existing per-agent `/score` endpoint returns
   the full shape. Documented in AC-3 and the new `## Score architecture v2`.
2. **R-2 (low) — Listing payload size.** `limit=20` × 7 fields per item is
   bounded; `pillars` and `breakdown` are excluded to keep the payload small.
   At max `limit=100` the response stays under the existing `Page[AgentOut]`
   envelope. **Mitigation:** AC-3 pins the schema.
3. **R-3 (low) — Optional migration deferred.** Without
   `ix_agent_cache_category_displayed`, the planner falls back to a sort on
   the filtered subset. At <1000 agents per category the planner chooses an
   acceptable plan; the migration is added when row counts cross that
   threshold. **Mitigation:** AC documents the threshold.
4. **R-4 (medium) — Stale-spec risk on `agent-detail-ui`.** The existing
   `openspec/specs/agent-detail-ui/spec.md` (the `test-copy-fix` archive)
   describes the current scattered render sites with positional anchors.
   UI consolidation moves the OFAC banner up to the score card region,
   inverting the implicit "OFAC is a CTA concern" semantic.
   **Mitigation:** `sdd-spec` phase reads the current spec, emits a
   `## MODIFIED Requirements` delta against `agent-detail-ui`, reuses the
   spec's existing `## Requirements` / `## Scenarios` shape verbatim where
   unchanged.
5. **R-5 (low) — `ScoreOutMinimal` vs `AgentOut`.** The minimal response carries score-shaped fields (`compliance_penalty`, `displayed_activity_score`, `creator_is_owner`) that `AgentOut` does not. Reusing `AgentOut` would force schema bloat or a second router family. **Mitigation:** keep `ScoreOutMinimal` as a sibling Pydantic model in `app/schemas/score.py`.
6. **R-6 (info) — Cold-start wallet data.** Until the wallet orchestrator (a future change) populates a stored `wallet_activity_score` column, the ranking endpoint omits wallet signals entirely. This is the intentional v1 behaviour per option (c).

## Rollback

`git revert` touches only: one router handler, one Pydantic model, one `<section>` wrapper in `agent_detail.html`, and one section in `DESIGN.md`. No migration, no column, no orchestrator — reverts cleanly with no downstream schema consequences.

## Related changes

- **Phase 1: `compliance-flags`** (archived 2026-09-15-1b) — provides `compliance_penalty`, `displayed_activity_score`, OFAC banner, Hire-CTA gate.
- **Phase 2: `wallet-activity`** (archived 2026-09-15) — provides the wallet helper, `fetch_wallet_signals`, wallet chip, and the negative-assertion contract this change preserves.
- **`chore-close-stale-changes`** (archived) — pre-work that closed orphan change directories before this chain started.
