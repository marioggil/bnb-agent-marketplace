# Design: agent-score-integration — ranking endpoint + trust-compliance UI block

**Change:** `agent-score-integration` (Phase 3 of `compliance-flags` → `wallet-activity` chain)
**Branch:** `feat/score-integration` (stacked on `feat/wallet-activity`)
**Phase:** Design
**Inputs read:** `openspec/changes/score-integration/{explore,proposal,spec}.md`,
`app/routers/agents.py`, `app/schemas/score.py`, `app/db/models/agent.py`,
`app/db/models/agent_compliance.py`, `app/templates/pages/agent_detail.html`,
`app/schemas/pagination.py`, `tests/test_wallet_activity_pages.py`,
`tests/test_wallet_activity_api.py`, `migrations/versions/0012_compliance_penalty.py`,
`DESIGN.md` (Decisions D1–D11 + Agent activity score entry).

---

## 1. Architecture overview

The change adds a **ranking** surface (server-side `displayed_activity_score` ordering)
and **consolidates** three scattered UI render sites into one trust-compliance block.
The data model does not change — three pre-existing signals are reused.

```
┌──────────────────────┐
│  GET /api/agents/    │  FastAPI router  (new function `rank_agents_score`)
│  score?category=&    │
│  sort=displayed_…&   │
│  limit=&offset=      │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────┐
│  SQLAlchemy: ONE `SELECT` against AgentCache                 │
│    LEFT JOIN agent_compliance_flags ON agent_id              │
│  Computed label: `displayed_activity_score`                  │
│    = GREATEST(agent_cache.activity_score                     │
│               - agent_cache.compliance_penalty, 0)           │
│  ORDER BY displayed_activity_score DESC NULLS LAST           │
│  LIMIT :limit OFFSET :offset                                 │
└──────────┬───────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────┐
│  ScoreOutMinimal (7-field Pydantic projection)               │
│    chain, token, name, activity_score, compliance_penalty,   │
│    displayed_activity_score, creator_is_owner                │
│  ← wallet signals deliberately OMITTED (clients follow up     │
│    with per-agent GET /api/agents/{chain}/{token}/score)     │
└──────────┬───────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────┐
│  Page[ScoreOutMinimal]   (Pydantic envelope from             │
│    app/schemas/pagination.py — items, total, page, page_size)│
└──────────────────────────────────────────────────────────────┘

UI path (page-render only, no API surface):
  agent_detail.html — wrap 3 existing elements in `<section class="trust-compliance">`:
    1. .ofac-block | .ofac-warn   (lifted from line 598–608)
    2. .wallet-activity-chip       (lifted from line 191–199)
    3. .activity-score-value       (line 179 stays)
    Block sits above the existing #hire-cta (DOM order preserved).
```

The UI and API paths are independent; both reuse the three existing signals
without mutating them.

---

## 2. Module surface

Five touch-points; the first three are new, the last two are append-only.

| File | Change | LoC est. |
|---|---|---|
| `app/schemas/score.py` | Add `ScoreOutMinimal` Pydantic model (7 fields, defaults match `ScoreOut`'s additive fields). Re-export in `__all__`. | +25 |
| `app/routers/agents.py` | Add new `rank_agents_score` route handler (declared BEFORE `/{chain_id}/{token_id}` so the literal `score` segment never gets swallowed by the path-param routes — same key learning as `/compare` at line 211). Extend `_SORT_KEYS` with `displayed_activity_score`. Import `AgentComplianceFlag` and `func.greatest`. | +55 |
| `app/templates/pages/agent_detail.html` | Wrap three existing elements in `<section class="trust-compliance">`. No new CSS classes. | +10 (wrapping only) |
| `tests/test_score_ranking_api.py` | New file: shape + sort + category filter + limit clamp + validation tests. | +110 |
| `tests/test_score_ranking_negative.py` | New file: 0 calls to `fetch_wallet_signals` + no `onchain_transfers` SQL listener. | +50 |
| `tests/test_trust_compliance_block.py` | New file: DOM-order assertions on `.trust-compliance` section. | +60 |
| `DESIGN.md` | Append `## Score architecture v2` section (≤50 lines) under existing `## Decisions` / `## Agent activity score` region. See §10 below for the proposed verbatim content. | +45 |

**No** changes to: `app/db/models/*`, `migrations/versions/*`, `app/schemas/agent.py`,
`app/routers/pages.py`, `app/static/css/site.css`, `ScoreOut` (the per-agent model).

---

## 3. New endpoint SQL

The spec hard-constraints the SQL shape (AC-2). The current schema has
`agent_cache.{chain_id, token_id, name, activity_score, compliance_penalty}`
and `agent_compliance_flags.{agent_id, creator_is_owner, …}`, **but no
stored `displayed_activity_score` column** — it's computed at read time in
`app/routers/agents.py:320` and `app/routers/pages.py:992`.

Implementation maps the conceptual SELECT to SQLAlchemy expressions that
compile to the same shape on the wire (one round-trip, no per-row wallet work):

```python
displayed = func.greatest(
    AgentCache.activity_score - AgentCache.compliance_penalty, 0
).label("displayed_activity_score")

stmt = (
    select(
        AgentCache.chain_id, AgentCache.token_id, AgentCache.name,
        AgentCache.activity_score, AgentCache.compliance_penalty,
        displayed, AgentComplianceFlag.creator_is_owner,
    )
    .select_from(AgentCache)
    .outerjoin(
        AgentComplianceFlag,
        AgentComplianceFlag.agent_id == AgentCache.agent_id,
    )
    .where(AgentCache.category == category)               # omitted when None
    .order_by(displayed.desc().nullslast())
    .limit(limit).offset(offset)
)
```

Conceptual SQL (matches the spec's verbatim contract):

```sql
SELECT chain_id, token_id, name, activity_score, compliance_penalty,
       GREATEST(activity_score - compliance_penalty, 0) AS displayed_activity_score,
       acf.creator_is_owner AS creator_is_owner
FROM   agent_cache        ac
LEFT   JOIN agent_compliance_flags acf ON acf.agent_id = ac.agent_id
WHERE  ac.category = :cat                                 -- bound only when provided
ORDER  BY displayed_activity_score DESC NULLS LAST
LIMIT  :limit OFFSET :offset;
```

`agent_compliance_flags` is a 1:1 keyed table (PK = `agent_id`), so the
`LEFT JOIN` is cheap — no row fan-out, planner picks nested-loop on PK.

`fetch_wallet_signals` and `OnchainTransfer` are **never** referenced; the
single SELECT above is the only DB hit per request.

---

## 4. `ScoreOutMinimal` Pydantic model

New sibling model in `app/schemas/score.py`. Default values mirror the additive
fields on `ScoreOut` so a row without a compliance flag row (or with `NULL`
scores) round-trips cleanly.

```python
class ScoreOutMinimal(BaseModel):
    """GET /api/agents/score item — listing projection (spec A1 + AC-3).

    Deliberately omits `wallet_activity_score`, `wallet_activity_breakdown`,
    `pillars`, and `breakdown` to keep the listing payload bounded at
    `limit=100` × 7 fields. Clients needing detail follow up with
    `GET /api/agents/{chain}/{token}/score` (the full `ScoreOut`).
    """

    chain: int                          # mirrors ScoreOut.chain
    token: int                          # mirrors ScoreOut.token
    name: str | None = None             # AgentCache.name (nullable on cache)
    activity_score: Decimal             # AgentCache.activity_score (Numeric(5, 2))
    compliance_penalty: float = 0.0     # AgentCache.compliance_penalty
    displayed_activity_score: float = 0.0   # GREATEST(activity - penalty, 0)
    creator_is_owner: bool = False      # AgentComplianceFlag.creator_is_owner
                                       #  (LEFT JOIN → False when row absent)
```

Field-type contract:
- `chain` / `token` are `int` (mirrors `ScoreOut`); the spec draft says `str`
  but `ScoreOut.chain: int` and `AgentCache.chain_id: int` are the canonical
  shape. The test pins `model_json_schema()` directly.
- `activity_score: Decimal` matches `Numeric(5, 2)` and `ScoreOut.activity_score`.
- `displayed_activity_score: float` at the JSON boundary — `func.greatest`
  returns a SQL numeric coerced to float by Pydantic (same pattern as
  `get_agent_score` line 320).
- `creator_is_owner` defaults `False`; the LEFT JOIN fills absent rows with
  `null` which Pydantic coerces per the type. The DB column is
  `NOT NULL DEFAULT false`, so the runtime default is consistent.

Re-export in `__all__`. `ScoreOut` is byte-identical (no edits).

---

## 5. UI consolidation map

Three elements move; everything else stays put.

| # | Element | Current location | New location | DOM order inside `.trust-compliance` |
|---|---|---|---|---|
| 1 | OFAC banner (`.ofac-block` / `.ofac-warn` + `{% set both_flags %}` block) | `agent_detail.html:601–609` (immediately before `#hire-cta`) | Lifted **up** into the score region, wrapped in `<section class="trust-compliance">` | First |
| 2 | Wallet-activity chip (`.wallet-activity-chip` block) | `agent_detail.html:191–199` (inside `#activity-score` card) | Lifted out of the card, wrapped in same `<section>` | Second |
| 3 | Activity score value (`.activity-score-value` + compliance badge) | `agent_detail.html:179–181` (inside `#activity-score` card) | Stays at line 179; the `<section id="activity-score">` card wrapper is **replaced** by the new `<section class="trust-compliance">` wrapper | Third (closes the block) |

The new wrapper sits above the Hire CTA — the spec hard-constrains that
`.ofac-warn` precedes `#hire-cta` in DOM order (AC-5 last scenario).

Template shape (the diff against `agent_detail.html`):

```jinja
<section class="trust-compliance">
  {% if creator_flagged and owner_flagged %}
    <p class="ofac-block" role="alert">OFAC: hiring blocked — …</p>
  {% elif creator_flagged or owner_flagged %}
    <p class="ofac-warn" role="status">OFAC warning: … flagged. …</p>
  {% endif %}

  {% if wallet_activity_score is not none %}
    <div class="wallet-activity-chip">
      <span class="badge category">Wallet activity: …/100 (…)</span>
      {% if creator_is_owner_chip %}<span class="badge category">creator == owner</span>{% endif %}
    </div>
  {% endif %}

  <h2>Activity score</h2>
  <p class="activity-score-value">{{ displayed_activity_score or 'n/a' }}/100</p>
  {% if compliance_penalty is defined and compliance_penalty > 0 %}
    <span class="badge risk">⚠ Compliance: −{{ '%.2f'|format(compliance_penalty) }} pts</span>
  {% endif %}
  {# … local_breakdown + latest_probe dl blocks stay verbatim … #}
</section>
```

**No** new CSS classes; **no** stylesheet edits. The five existing classes
(`.ofac-block` / `.ofac-warn` / `.wallet-activity-chip` /
`.activity-score-value` / `.badge`) continue to apply — elements are
byte-identical, only their parent changes. `pages.py::agent_detail` is
untouched — the template wraps existing locals
(`creator_flagged`, `owner_flagged`, `wallet_activity_score`,
`displayed_activity_score`, `compliance_penalty`, `local_breakdown`,
`latest_probe`) verbatim.

---

## 6. Negative-assertion test design (Phase 2 contract preserved)

The Phase 2 invariant ("listing pages MUST NOT aggregate wallet signals") is
pinned by `tests/test_wallet_activity_pages.py::test_listing_pages_issue_zero_wallet_queries`
(lines 153–197). It uses a SQLAlchemy `before_cursor_execute` listener
attached to `session_module.engine.sync_engine` and counts statements
containing `onchain_transfers`.

**The new ranking endpoint reuses the listener pattern verbatim.** The only
difference is the assertion target — the request URL changes from the
listing pages to `GET /api/agents/score`. The new test lives in
`tests/test_score_ranking_negative.py` and pins both:

1. `fetch_wallet_signals` is **never imported or called** by the ranking
   route. A monkey-patched counter wrapper asserts call count == 0.
2. No SQL statement issued by the request touches `onchain_transfers`. The
   same `before_cursor_execute` listener pattern from the Phase 2 test is
   lifted verbatim (`from app.db import session as session_module` →
   `engine = session_module.engine.sync_engine`); assertion is
   `counter["count"] == 0`.

This guarantees `tests/test_wallet_activity_pages.py` continues to pass
byte-identically — required by AC-7. The listener test makes a per-row
`fetch_wallet_signals` enrichment loop load-bearing-failing, not silently
regressing.

---

## 7. Test strategy

Three new test files; all use the existing `client` + `db` fixtures from
`tests/conftest.py` (no new fixtures). Strict TDD: red → green → refactor.

### `tests/test_score_ranking_api.py` — shape, sort, validation

- `test_rank_default_limit_returns_top_20` — seed 25 agents with distinct
  `displayed_activity_score` in `[0, 100]` (set `activity_score` directly,
  `compliance_penalty=0` so `displayed = activity`); assert `page_size==20`,
  `len(items)==20`, monotonic non-increasing order.
- `test_rank_limit_clamped_to_100` — seed 150 agents; `limit=200` → 100 items.
- `test_rank_sort_desc_nulls_last` — seed A=90, B=NULL, C=50; assert order A, C, B.
- `test_rank_unknown_sort_returns_400` — `sort=wallet_activity_score` → 400
  with body referencing `sort` field.
- `test_rank_unknown_category_returns_400` — `category=defi` → 400 with body
  referencing `category` field (Literal validation).
- `test_rank_missing_category_returns_all` — no `category` param → all seeded
  agents returned.
- `test_rank_response_item_matches_ScoreOutMinimal_schema` — pin via
  `set(item.keys()) == {chain, token, name, activity_score, compliance_penalty,
  displayed_activity_score, creator_is_owner}` and assert no `wallet_activity_*`,
  `pillars`, `breakdown` keys.
- `test_rank_ScoreOutMinimal_roundtrips_json` — construct instance, dump,
  re-validate; assert equality.
- `test_rank_page_envelope_shape` — `total`, `page`, `page_size`, `items` keys
  present and well-typed.

### `tests/test_score_ranking_negative.py` — wallet-query isolation

- `test_rank_does_not_call_fetch_wallet_signals` — monkey-patch
  `app.routers.agents.fetch_wallet_signals` (raise-counter); call endpoint;
  assert counter == 0, status 200.
- `test_rank_no_sql_touches_onchain_transfers` — register
  `before_cursor_execute` listener (verbatim from
  `tests/test_wallet_activity_pages.py:172–186`); call endpoint; assert
  counter == 0, status 200.
- `test_phase2_listing_negative_still_passes` — import-and-run the existing
  `test_listing_pages_issue_zero_wallet_queries` to confirm no drift (or rely
  on the AC-7 baseline assertion in the same test run).

### `tests/test_trust_compliance_block.py` — UI DOM order

- `test_block_present_with_all_three_elements` — flagged creator + clean owner
  + wallet signal; render `agent_detail.html`; assert exactly one
  `<section class="trust-compliance">`; assert `.ofac-warn` precedes
  `.wallet-activity-chip` precedes `.activity-score-value` via
  `lxml.html` parent traversal.
- `test_both_flagged_renders_ofac_block_first` — both flagged → `.ofac-block`
  (not `.ofac-warn`) precedes chip.
- `test_clean_agent_renders_only_score_card` — no flags, no wallet → section
  contains `.activity-score-value` only; no `.ofac-block`, `.ofac-warn`, or
  `.wallet-activity-chip`.
- `test_ofac_banner_still_precedes_hire_cta` — flagged creator → `.ofac-warn`
  appears before `#hire-cta` in document order.
- `test_no_new_css_classes_introduced` — parse rendered HTML; assert the
  `class` attributes are drawn from the existing set
  `{trust-compliance, ofac-block, ofac-warn, wallet-activity-chip,
  activity-score-value, badge, agent-profile, ...}` only (no surprises).

---

## 8. Risks

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R-1 | Ranking endpoint omits wallet signals; a client that only calls `GET /api/agents/score` cannot see the creator/owner wallet footprint. | low | `creator_is_owner` is in the response; the per-agent `/score` endpoint returns the full shape. Documented in `## Score architecture v2` and AC-3. |
| R-2 | `displayed_activity_score` is computed at read time via `func.greatest`; if `activity_score IS NULL` the label is `NULL` and the `NULLS LAST` clause matters. | low | AC-2 pins the NULL-last ordering with a seeded `B=NULL` agent. |
| R-3 | Optional migration `ix_agent_cache_category_displayed` is deferred; at <1000 agents per category the planner is fine, beyond that the planner falls back to a sort on the filtered subset. | low | AC documents the <1000-row threshold for revisiting. No migration in v1. |
| R-4 | Stale-spec risk: the canonical `agent-detail-ui/spec.md` describes the current scattered render sites with positional anchors. UI consolidation moves OFAC up to the score region. | medium | This delta adds new requirements; the three CTA requirements remain byte-identical (per spec `## Output path note`). No existing assertion weakened. |
| R-5 | New `ScoreOutMinimal` model duplicates two additive fields from `ScoreOut` (`compliance_penalty`, `displayed_activity_score`, `creator_is_owner`); keeping them in sync is the maintenance burden. | low | `ScoreOutMinimal` is the projection — it does not extend `ScoreOut`. Tests pin the seven fields; adding a field to `ScoreOut` does not silently leak into the ranking response. |
| R-6 | Cold-start wallet data: until a future wallet orchestrator populates `wallet_activity_score`, the ranking endpoint omits wallet signals entirely. | info | Intentional v1 behaviour per explore §3 option (c). |
| R-7 | The `displayed_activity_score` label is computed at SELECT time per row; the `ORDER BY` uses the same expression. PG plans this fine; sqlite (test engine) supports it via `MAX(a-b, 0)`. | low | The test DB is sqlite; the test asserts ordering via actual round-trip. No DB-engine-specific code. |

---

## 9. Rollout

Single PR, ~200–300 lines (router + schema + template wrap + DESIGN.md section +
3 new test files). No migration. No new CSS. No new fixtures. No changes to
`ScoreOut`, `app/routers/pages.py`, or `app/db/models/*`. `git revert` is a
clean one-commit revert with no downstream schema consequences.

The optional `ix_agent_cache_category_displayed` index lands in a follow-on
change when `AgentCache` row counts cross the <1000-per-category threshold
documented in the spec AC.

---

## 10. `DESIGN.md` addition — `## Score architecture v2`

Verbatim content to append to `DESIGN.md` under the `## Decisions` heading,
near the existing `## Agent activity score` entry. ≤50 lines, satisfies
spec AC-6.

```markdown
## Score architecture v2

Three signals describe every agent; two are stored, one is per-row derived in
v1. The ranking surface exposes the canonical pillar minus the OFAC
penalty; the per-agent detail surface exposes everything.

| Signal | Lives in | Source |
|---|---|---|
| `activity_score` (canonical pillar, 0–100) | `agent_cache.activity_score` | Phase 1 — composite `0.60 × probe + 0.40 × track_record` |
| `compliance_penalty` (stored derived scalar, 0–50) | `agent_cache.compliance_penalty` | Phase 1 — 30 per flag, capped at 50; populated by the compliance refresh orchestrator |
| `displayed_activity_score` (per-request derived, 0–100) | computed at read time | `GREATEST(activity_score - compliance_penalty, 0)` |
| `wallet_activity_score` (per-row derived in v1, 0–100) | computed at read time | Phase 2 — creator + owner 90-day on-chain footprint, Bayesian shrinkage at n<3 |

`activity_score` is canonical and NEVER mutated by compliance or wallet
signals. `compliance_penalty` is stored because it changes the public
ranking and must survive across reads without recomputation; it is refreshed
by the compliance orchestrator. `wallet_activity_score` is per-row derived
because the per-agent `/score` endpoint already aggregates it on demand and
the wallet orchestrator (a future change) will pre-compute it into a stored
column when listing-time aggregation becomes worth the storage.

**Listing-page invariant (Phase 2 contract, preserved):** listing pages
MUST NOT aggregate wallet signals. `GET /` / `GET /agents` /
`GET /agents/{chain}` and the new ranking endpoint `GET /api/agents/score`
all rank on `displayed_activity_score` from a single `SELECT` against
`agent_cache` (with a `LEFT JOIN` for `creator_is_owner`). No per-row
`fetch_wallet_signals`, no `OnchainTransfer` queries on listing paths.
Clients that need the wallet signal follow up with `GET
/api/agents/{chain}/{token}/score` (the per-agent endpoint).
```

This block is the exact content to land in `DESIGN.md` (verbatim, ≤50 lines).
It names all three signals, distinguishes canonical from derived, and
contains the literal substring `listing pages MUST NOT aggregate wallet
signals` (AC-6).
