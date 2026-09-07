# Delta for `agent-detail-ui` — `agent-score-integration`

**Change:** `agent-score-integration` (Phase 3 of the compliance-flags → wallet-activity chain)
**Domain:** `agent-detail-ui` (EXTENSION — adds ranking endpoint + UI consolidation)
**Branch:** `feat/score-integration` (stacked on `feat/wallet-activity`)
**Scope:** One new ranking endpoint, one new Pydantic model, one UI block wrapper in the existing detail template, one `DESIGN.md` section. No migration in v1.
**Output path note:** This delta is emitted flat at `openspec/changes/score-integration/spec.md` per the parent task's explicit output path. On archive it merges with the existing canonical spec at `openspec/specs/agent-detail-ui/spec.md` (which itself originated from the `test-copy-fix` archive). The three existing CTA/hire requirements from that canonical spec MUST remain byte-identical after archive; this delta adds new requirements and does not modify them.

---

## Purpose

Phase 1 (`compliance-flags`) added `compliance_penalty` and `displayed_activity_score` to `ScoreOut`. Phase 2 (`wallet-activity`) layered `wallet_activity_score`, `wallet_activity_breakdown`, and `creator_is_owner` on top. The data model is complete — three signals describe every agent: canonical activity, OFAC compliance, and creator/owner wallet footprint.

Two product gaps remain. First, no ranking surface exposes `displayed_activity_score`: the existing `GET /api/agents` sorts on `average_score` / `activity_score` / `total_feedbacks` / `created_at` / `name`, none of which account for the OFAC penalty. Second, the detail page renders the three signals in three disconnected locations — the OFAC banner near the Hire CTA, the score card near the top of the body, and the wallet-activity chip inside the score card — forcing users to scan the page top-to-bottom to correlate "this score dropped because of OFAC".

This change adds a ranking endpoint `GET /api/agents/score` returning `Page[ScoreOutMinimal]`, and wraps the three scattered signals in a single contiguous `<section class="trust-compliance">` block in fixed order (OFAC → wallet → score).

---

## Non-Goals

- **NO listing-page N+1 wallet queries.** The ranking endpoint MUST issue exactly one SQL `SELECT` against `AgentCache` per request. It MUST NOT call `fetch_wallet_signals` and MUST NOT query `OnchainTransfer`. The Phase 2 negative assertion in `tests/test_wallet_activity_pages.py` continues to pin this invariant.
- **NO Alembic migration in v1.** The optional composite index `ix_agent_cache_category_displayed(category, displayed_activity_score DESC NULLS LAST)` is deferred. At <1000 agents per category the planner chooses an acceptable plan; the index is added when row counts cross that threshold (AC documents the threshold).
- **NO Phase 2 territory touched.** `fetch_wallet_signals`, the wallet chip's data path, the wallet helper, and the per-agent `GET /api/agents/{chain}/{token}/score` endpoint stay byte-identical. The `ScoreOut` schema (8 fields + `pillars` + `breakdown`) is unchanged. `creator_is_owner` is reused, not redefined.
- **NO new CSS classes.** The `<section class="trust-compliance">` wrapper reuses the existing `.ofac-block`, `.ofac-warn`, `.wallet-activity-chip`, `.activity-score-value`, and `.badge` classes. No stylesheet changes.

---

## Acceptance Criteria

The change MUST satisfy all of the following, verifiable from a clean checkout of `feat/score-integration`:

1. **AC-1 — Endpoint shape.** `GET /api/agents/score?category=other` returns HTTP 200 with a JSON body matching `Page[ScoreOutMinimal]` containing `total`, `page`, `page_size`, and `items`. Default `limit=20`. `limit=200` clamps to `100`. Unknown `sort` value returns HTTP 400. Unknown `category` value returns HTTP 400 (Literal validation). Missing `category` returns all agents.
2. **AC-2 — Sort + ordering.** `sort=displayed_activity_score` orders items by `displayed_activity_score DESC NULLS LAST`. The ordering is verified by seeding ≥3 agents with distinct displayed scores plus one with `NULL` and asserting the NULL row sorts last.
3. **AC-3 — Minimal projection.** Each item in the response contains exactly seven fields: `chain`, `token`, `name`, `activity_score`, `compliance_penalty`, `displayed_activity_score`, `creator_is_owner`. `wallet_activity_score`, `wallet_activity_breakdown`, `pillars`, and `breakdown` MUST NOT appear. The test pins via `ScoreOutMinimal.model_json_schema()` diff against the actual response item.
4. **AC-4 — Phase 2 negative assertion preserved.** During one full request to `GET /api/agents/score`, `fetch_wallet_signals` is invoked 0 times (mock count assertion), and no SQL statement touches the `onchain_transfers` table (verified via a SQLAlchemy `before_cursor_execute` listener registered for the test). The pre-existing `tests/test_wallet_activity_pages.py` continues to pass without modification.
5. **AC-5 — Trust-compliance block ordering.** Rendered `app/templates/pages/agent_detail.html` for an agent with `creator_flagged=True`, `owner_flagged=False`, and a wallet signal contains exactly one `<section class="trust-compliance">` element. Inside that section, the OFAC banner element (`.ofac-block` or `.ofac-warn`) precedes `.wallet-activity-chip`, which precedes the activity score card (`.activity-score-value`). Test asserts DOM order via `lxml.html` parent traversal.
6. **AC-6 — `DESIGN.md` section.** `DESIGN.md` contains a section heading `## Score architecture v2`, the section body is ≤50 lines, it names all three signals (activity, compliance, wallet), it distinguishes canonical vs derived columns (`agent_cache.compliance_penalty` is stored; `wallet_activity_score` is per-row derived in v1), and it contains the literal substring `listing pages MUST NOT aggregate wallet signals`.
7. **AC-7 — Baseline preserved.** `uv run pytest` reports no new failures and no new errors compared to the pre-change baseline of 388 passing tests. No existing assertion in `tests/test_pages.py`, `tests/test_pages_x402.py`, `tests/test_score_api.py`, or `tests/test_wallet_activity_pages.py` is weakened.

---

## Requirements

### Requirement: Ranking endpoint returns paginated `ScoreOutMinimal` projection

The system SHALL expose `GET /api/agents/score` returning `Page[ScoreOutMinimal]`. The endpoint SHALL accept `category` (Literal matching `list_agents`: `"rebalancing"`, `"other"`), `sort` (Literal value `"displayed_activity_score"` for v1), `limit` (int, default 20, max 100), and `offset` (int, default 0). The endpoint SHALL issue exactly one SQL `SELECT` against `AgentCache` ordered by `displayed_activity_score DESC NULLS LAST` and SHALL return HTTP 400 on any unknown `category` or `sort` value. The endpoint SHALL clamp `limit` to 100 silently when the request exceeds it and SHALL reject negative or non-integer `limit` values with HTTP 400.

(Previously: no ranking endpoint existed; `list_agents` was the only listing surface and ordered on `average_score` / `activity_score` / `total_feedbacks` / `created_at` / `name`, none of which applied the OFAC penalty.)

#### Scenario: default limit returns first 20 ranked agents

- GIVEN a seeded `AgentCache` with ≥25 agents in category `"other"`, each with distinct `displayed_activity_score` in the range `[0, 100]`
- WHEN the client issues `GET /api/agents/score?category=other&sort=displayed_activity_score`
- THEN the response status is 200
- AND `page_size` equals `20`
- AND `items` contains exactly 20 entries
- AND `items[0].displayed_activity_score` ≥ `items[1].displayed_activity_score` ≥ … ≥ `items[19].displayed_activity_score`
- AND no item's `displayed_activity_score` is `null` (all 20 are populated)

#### Scenario: limit above max is clamped to 100

- GIVEN a seeded `AgentCache` with ≥150 agents in category `"other"`
- WHEN the client issues `GET /api/agents/score?category=other&sort=displayed_activity_score&limit=200`
- THEN the response status is 200
- AND `page_size` equals `100`
- AND `items` contains exactly 100 entries

#### Scenario: unknown sort returns 400

- WHEN the client issues `GET /api/agents/score?category=other&sort=wallet_activity_score`
- THEN the response status is 400
- AND the response body contains a validation error referencing the `sort` field

#### Scenario: unknown category returns 400

- WHEN the client issues `GET /api/agents/score?category=defi&sort=displayed_activity_score`
- THEN the response status is 400
- AND the response body contains a Literal validation error referencing the `category` field

#### Scenario: NULL displayed_activity_score sorts last

- GIVEN three seeded agents: A (`displayed_activity_score=90`), B (`displayed_activity_score=NULL`), C (`displayed_activity_score=50`)
- WHEN the client issues `GET /api/agents/score?category=other&sort=displayed_activity_score&limit=10`
- THEN `items[0].name` equals A's name
- AND `items[1].name` equals C's name
- AND `items[2].name` equals B's name

---

### Requirement: `ScoreOutMinimal` Pydantic model with exactly seven fields

The system SHALL define `ScoreOutMinimal` in `app/schemas/score.py` as a Pydantic model with exactly the following fields: `chain` (str), `token` (str), `name` (str), `activity_score` (Decimal), `compliance_penalty` (float, default 0.0), `displayed_activity_score` (float, default 0.0), `creator_is_owner` (bool, default False). The model SHALL NOT include `wallet_activity_score`, `wallet_activity_breakdown`, `pillars`, or `breakdown`. The existing `ScoreOut` model SHALL remain unchanged (this is a sibling model, not a replacement).

(Previously: `ScoreOut` was the only score-shaped model; no minimal projection existed for listing surfaces.)

#### Scenario: response item schema matches `ScoreOutMinimal` exactly

- GIVEN any seeded `AgentCache` row in category `"other"`
- WHEN the client issues `GET /api/agents/score?category=other&sort=displayed_activity_score&limit=1`
- THEN the JSON keys of `items[0]` equal the set `{chain, token, name, activity_score, compliance_penalty, displayed_activity_score, creator_is_owner}`
- AND no other keys are present

#### Scenario: `wallet_activity_score` and `pillars` are absent

- WHEN the client issues `GET /api/agents/score?category=other&sort=displayed_activity_score&limit=5`
- THEN no item contains the key `wallet_activity_score`
- AND no item contains the key `wallet_activity_breakdown`
- AND no item contains the key `pillars`
- AND no item contains the key `breakdown`

#### Scenario: `ScoreOutMinimal` is independently importable and round-trips JSON

- GIVEN a `ScoreOutMinimal` instance constructed in a test with all seven fields populated
- WHEN the instance is serialized via `model_dump_json()` and re-parsed via `model_validate_json()`
- THEN the re-parsed instance equals the original (excluding Decimal→float coercion handled by Pydantic)

---

### Requirement: Ranking endpoint never aggregates wallet signals

The system SHALL ensure that during any single request to `GET /api/agents/score`, `fetch_wallet_signals` is invoked exactly 0 times and no SQL statement issued by the request reads from the `onchain_transfers` table. The system SHALL implement this contract by reading `compliance_penalty` and `creator_is_owner` directly from the `AgentCache` columns populated by Phase 1's orchestrator and Phase 2's helper, in a single SQL query. The endpoint SHALL NOT import or invoke `fetch_wallet_signals`, `OnchainTransfer`, or any wallet-aggregation helper.

(Previously: this contract was established by `tests/test_wallet_activity_pages.py` for the `/` listing path; this requirement extends it to the new `/api/agents/score` ranking path.)

#### Scenario: `fetch_wallet_signals` call count is zero per request

- GIVEN a test that monkey-patches `app.routers.agents.fetch_wallet_signals` with a counter wrapper that raises if called
- WHEN the client issues `GET /api/agents/score?category=other&sort=displayed_activity_score&limit=10`
- THEN the wrapper's call count after the request equals `0`
- AND the response status is 200

#### Scenario: no SQL touches `onchain_transfers`

- GIVEN a test that registers a SQLAlchemy `before_cursor_execute` event listener capturing `(statement, parameters, context, executem)` for the request's engine
- WHEN the client issues `GET /api/agents/score?category=other&sort=displayed_activity_score&limit=10`
- THEN no captured statement contains the substring `onchain_transfers` (case-insensitive)
- AND the response status is 200

#### Scenario: pre-existing listing-page negative assertion still passes

- WHEN the client runs `uv run pytest tests/test_wallet_activity_pages.py`
- THEN all assertions in that file pass without modification

---

### Requirement: Trust-compliance block on the agent detail page

The system SHALL render the OFAC banner, the wallet-activity chip, and the activity score card as direct or descendant children of exactly one `<section class="trust-compliance">` element in `app/templates/pages/agent_detail.html`. The children SHALL appear in fixed DOM order: (1) OFAC banner (`.ofac-block` if both creator and owner are flagged, `.ofac-warn` if exactly one is flagged, omitted otherwise); (2) wallet-activity chip (`.wallet-activity-chip`, present iff `wallet_activity_score is not none`); (3) activity score card containing `.activity-score-value` displaying `{{ displayed_activity_score or 'n/a' }}/100`. The wrapper SHALL NOT introduce new CSS classes; existing classes `.ofac-block`, `.ofac-warn`, `.wallet-activity-chip`, `.activity-score-value`, and `.badge` SHALL be reused unchanged. The OFAC banner, when present, SHALL precede the Hire CTA in DOM order.

(Previously: the three elements were rendered at three separate, non-contiguous positions in `agent_detail.html` — OFAC near the Hire CTA (lines ~598–608), score card near the top of the body (line ~179), wallet chip inside the score card (lines ~191–199).)

#### Scenario: flagged creator + clean owner + wallet signal renders all three in fixed order

- GIVEN an `AgentCache` row with `creator_flagged=True`, `owner_flagged=False`, and a non-null `wallet_activity_score` accessible via the existing per-agent helper
- WHEN the page-render path produces HTML for the detail page
- THEN the rendered HTML contains exactly one `<section class="trust-compliance">`
- AND inside that section, `.ofac-warn` appears before `.wallet-activity-chip`
- AND `.wallet-activity-chip` appears before `.activity-score-value`
- AND the activity score text contains the substring `displayed_activity_score/100` (formatted, e.g. `85.0/100`)

#### Scenario: clean creator + flagged owner renders OFAC banner + score card (chip omitted)

- GIVEN an `AgentCache` row with `creator_flagged=False`, `owner_flagged=True`, and `wallet_activity_score is None`
- WHEN the page-render path produces HTML
- THEN the rendered HTML contains exactly one `<section class="trust-compliance">`
- AND inside that section, `.ofac-warn` is present
- AND `.wallet-activity-chip` is absent
- AND `.activity-score-value` is present and follows `.ofac-warn`

#### Scenario: both creator and owner flagged renders `.ofac-block` first

- GIVEN an `AgentCache` row with `creator_flagged=True`, `owner_flagged=True`, and a non-null `wallet_activity_score`
- WHEN the page-render path produces HTML
- THEN the section contains `.ofac-block` (not `.ofac-warn`)
- AND `.ofac-block` precedes `.wallet-activity-chip`

#### Scenario: clean agent with no wallet signal renders only the score card

- GIVEN an `AgentCache` row with `creator_flagged=False`, `owner_flagged=False`, and `wallet_activity_score is None`
- WHEN the page-render path produces HTML
- THEN the section contains `.activity-score-value` only
- AND no `.ofac-block`, no `.ofac-warn`, and no `.wallet-activity-chip` are present inside it

#### Scenario: OFAC banner still precedes the Hire CTA

- GIVEN an `AgentCache` row with `creator_flagged=True`
- WHEN the page-render path produces HTML
- THEN in document order, the `.ofac-warn` element appears before the `#hire-cta` element

---

### Requirement: `DESIGN.md` documents the three-signal score architecture v2

The system SHALL include a `## Score architecture v2` section in `DESIGN.md`. The section SHALL be ≤50 lines, SHALL identify `activity_score` as the canonical pillar, SHALL identify `agent_cache.compliance_penalty` as a stored derived column and `wallet_activity_score` as a per-row derived value in v1, SHALL name all three signals (activity, compliance, wallet), and SHALL contain the literal substring `listing pages MUST NOT aggregate wallet signals`. The section SHALL appear under the existing `## Decisions` heading near the `## Agent activity score` entry.

(Previously: `DESIGN.md` documented only the single-signal `## Agent activity score` entry; `## Compliance flags` and `## Wallet activity` covered the new signals in isolation without naming the ranking contract or the listing-page invariant.)

#### Scenario: section is present and bounded in length

- WHEN the file `DESIGN.md` is read
- THEN it contains the line `## Score architecture v2`
- AND the number of lines between that heading and the next `## ` heading (or end of file) is ≤50

#### Scenario: section names all three signals and the canonical/derived distinction

- WHEN the section is read
- THEN it contains the substrings `activity`, `compliance`, and `wallet` (case-insensitive, in the section body)
- AND it contains the substring `compliance_penalty` and identifies it as stored
- AND it contains the substring `wallet_activity_score` and identifies it as per-row derived

#### Scenario: section pins the listing-page invariant

- WHEN the section is read
- THEN it contains the exact substring `listing pages MUST NOT aggregate wallet signals`

---

## Out of Scope

- A new Alembic migration adding `ix_agent_cache_category_displayed` — deferred until `AgentCache` row counts cross the threshold where the planner degrades.
- A wallet orchestrator that pre-computes `wallet_activity_score` into a new `agent_cache` column (option (b) from explore §3) — a future change.
- Listing-page chips on `/`, `/agents`, `/agents/{chain}` — the ranking endpoint is the v1 listing surface.
- `creator_is_owner`-based downranking in SQL — the field is in the minimal response so clients can apply it; the server does not.
- Mutating `activity_score`, `pillars`, `composite_score`, or any other field of `ScoreOut`.
- A new `wallet_score` (8004scan mirror) column or recomputation logic.
- Real scheduler / cron for compliance refresh (already out of scope per Phase 1 archive).
- Any change to `app/routers/pages.py::agent_detail` — the template wraps existing locals; no new locals.
- New CSS classes, stylesheet edits, or design-token changes.
- The existing per-agent `GET /api/agents/{chain}/{token}/score` endpoint — unchanged, continues to return the full `ScoreOut` shape for clients that need wallet detail.

---

## Risks

- **R-1 (low) — Option (c) trade-off.** The ranking endpoint returns no wallet signals; a client that only calls `GET /api/agents/score` cannot see the creator/owner wallet footprint. Mitigation: `creator_is_owner` is in the minimal response, and the existing per-agent `/score` endpoint returns the full shape. Documented in AC-3 and the new `## Score architecture v2`.
- **R-2 (low) — Listing payload size.** `limit=20` × 7 fields per item is bounded; `pillars` and `breakdown` are excluded to keep the payload small. At max `limit=100` the response stays under the existing `Page[AgentOut]` envelope. Mitigation: AC-3 pins the schema.
- **R-3 (low) — Optional migration deferred.** Without `ix_agent_cache_category_displayed`, the planner falls back to a sort on the filtered subset. At <1000 agents per category the planner chooses an acceptable plan; the migration is added when row counts cross that threshold. AC documents the threshold.
- **R-4 (medium) — Stale-spec risk on `agent-detail-ui`.** The existing canonical spec describes the CTA/hire render sites; UI consolidation moves the OFAC banner up to the score card region. Mitigation: this delta does NOT modify the three CTA requirements — they remain byte-identical. New requirements are ADDED in their own sections.
- **R-5 (low) — `ScoreOutMinimal` vs `AgentOut`.** The minimal response carries score-shaped fields (`compliance_penalty`, `displayed_activity_score`, `creator_is_owner`) that `AgentOut` does not. Reusing `AgentOut` would force schema bloat or a second router family. Mitigation: keep `ScoreOutMinimal` as a sibling Pydantic model in `app/schemas/score.py`.
- **R-6 (info) — Cold-start wallet data.** Until a wallet orchestrator (a future change) populates a stored `wallet_activity_score` column, the ranking endpoint omits wallet signals entirely. This is the intentional v1 behaviour per option (c).

## Key Learnings

1. The Phase 2 negative-assertion contract in `tests/test_wallet_activity_pages.py` is the load-bearing guard for the "no N+1 wallet signals on listing" invariant; option (c) — ranking on `displayed_activity_score` only with a minimal projection — is what preserves it across the new `/api/agents/score` path.
2. `ScoreOutMinimal` deliberately omits the heavy `pillars` and `breakdown` so the listing payload stays bounded at `limit=100` × 7 fields, which is materially cheaper than 100 × `ScoreOut` items each carrying a full pillar breakdown.
3. The OFAC → wallet → score ordering encodes a security-first reading: the most security-critical signal renders first regardless of how the page is laid out, and the headline score is the closing element so users always finish reading on the number that drives ranking.