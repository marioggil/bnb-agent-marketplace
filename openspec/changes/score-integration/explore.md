# Explore: agent-score-integration (Phase 3)

**Change:** `agent-score-integration`
**Branch:** `feat/score-integration` (stacked on `feat/wallet-activity`; wallet-activity pushed but not yet merged to master)
**Phase:** Explore
**Inputs read:** `app/routers/agents.py`, `app/schemas/score.py`, `app/templates/pages/agent_detail.html`, `app/templates/partials/hero.html`, `migrations/versions/`, `DESIGN.md`, `tests/test_wallet_activity_pages.py`.

---

## 1. Current state of the chained signals

Phase 1 (compliance-flags) + Phase 2 (wallet-activity) wired two new signals into `ScoreOut`:

```
ScoreOut fields (app/schemas/score.py:56-82):
  chain, token
  activity_score: Decimal                  (canonical, never mutated)
  compliance_penalty: float = 0.0          (1a-i: new column, 1a-ii: orchestrator)
  displayed_activity_score: float = 0.0    (max(0, activity - compliance))
  wallet_activity_score: float | None      (2: derived per-agent)
  wallet_activity_breakdown: dict | None   (2: {creator, owner, track_record})
  creator_is_owner: bool = False           (2: derived)
  pillars: Pillars                         (probe + track_record)
  breakdown: list[dict]                    ([{dimension, score, weight}])
```

The detail page (`agent_detail.html`) renders these in **three disconnected locations**:
- OFAC block banner (lines 598-608) near the Hire CTA.
- Activity score card with `displayed_activity_score` (line 179).
- Wallet activity chip (lines 191-199) just below the score card.

Visually, the OFAC banner is far from the score card. The user has to scan the page to correlate them. **Phase 3's job is to consolidate.**

## 2. Existing listing endpoint

`app/routers/agents.py:53-105`:
- `GET /api/agents` (no path param) → `list_agents` returns `Page[AgentOut]`.
- Filters: `category` (Literal of allowed values), `q` (search), plus pagination `limit`/`offset`.
- **No `/score` ranking variant exists** — Phase 3 adds it.
- The existing `category` filter on `AgentCache.category` is already used by `hero.html` for the listing.

## 3. The Phase 2 negative-assertion contract

`tests/test_wallet_activity_pages.py` pins that the listing page (`/`) **does not aggregate wallet signals** — i.e. no `OnchainTransfer` queries per agent on the listing path. This is the load-bearing test for the spec's "Listing pages MUST stay wallet-chip-free" non-goal.

Phase 3's ranking endpoint IS a listing query that returns full `ScoreOut` shapes including `wallet_activity_score`. **Three resolution options**:

| Option | Mechanism | Cost | Verdict |
|---|---|---|---|
| (a) Compute at query time | Loop agents, call `fetch_wallet_signals` per row | N+1 OnchainTransfer queries | **Breaks Phase 2 contract** |
| (b) Pre-compute into a new column | Add `wallet_activity_score` to `AgentCache` like `compliance_penalty` | Migration + orchestrator task | Consistent with compliance pattern, but adds storage |
| (c) Ranks on `displayed_activity_score` only; omit `wallet_activity_score` from list response | Clients fetch full ScoreOut via the per-agent `/score` endpoint | Free; degrades ranking fidelity | **Cleanest for a first slice** |

**Recommendation: option (c) for the ranking endpoint, option (b) deferred to a future change.** The ranking endpoint returns `ScoreOut` minus the wallet signals (which are None), and ranks on `displayed_activity_score`. Clients that need wallet detail call `GET /api/agents/{chain}/{token}/score` (the existing per-agent endpoint that already returns the full shape). The negative assertion is preserved because the listing ranking path never calls `fetch_wallet_signals`.

This preserves the "wallet signals are a per-agent detail signal" invariant from Phase 2 and keeps Phase 3's diff small.

## 4. UI consolidation plan

The current detail page has three independent rendering sites for compliance + score + wallet. Phase 3 wraps them in a single `<section class="trust-compliance">` block in this order (security-critical first):

```
<section class="trust-compliance">
  {# 1. OFAC banner (compliance-flags 1b) — security-critical, always first. #}
  {% if creator_flagged and owner_flagged %}
    <p class="ofac-block" role="alert">OFAC: hiring blocked — ...</p>
  {% elif creator_flagged or owner_flagged %}
    <p class="ofac-warn" role="status">OFAC warning: ...</p>
  {% endif %}

  {# 2. Wallet activity chip (wallet-activity 2) — context for the score. #}
  {% if wallet_activity_score is not none %}
    <div class="wallet-activity-chip">...</div>
  {% endif %}

  {# 3. Activity score card with displayed_activity_score (compliance 1b). #}
  <p class="activity-score-value">{{ displayed_activity_score or 'n/a' }}/100</p>
  ...
</section>
```

The three existing elements move from their scattered positions to a contiguous block immediately before the Hire CTA. The order is fixed by the spec: OFAC → wallet → score.

## 5. Ranking endpoint contract (proposed)

```
GET /api/agents/score?category=<string>&sort=<field>&limit=<int>
  category:  Literal["rebalancing", "other"]   (matches existing list_agents filter)
  sort:      Literal["displayed_activity_score"]   (single value for v1)
  limit:     int = 20, max 100
  → Page[ScoreOutMinimal]
```

`ScoreOutMinimal` is a new Pydantic model:
```
chain, token, name, activity_score, compliance_penalty,
displayed_activity_score, creator_is_owner
```

This **omits** the wallet signals and the heavy `pillars`/`breakdown` to keep the listing payload small. Clients that need detail follow up with the per-agent `/score` endpoint.

The query is a single SQL `SELECT` against `AgentCache` with `WHERE category = :cat ORDER BY displayed_activity_score DESC LIMIT :limit OFFSET :offset`. The `compliance_penalty` is read directly from the column (populated by Phase 1's orchestrator). No joins, no per-row wallet computation.

A small migration may add `Index("ix_agent_cache_category_displayed", "category", text("displayed_activity_score DESC NULLS LAST"))` to keep the query plan cheap. **Deferrable**: at <1000 agents per category the planner is fine without it.

## 6. DESIGN.md update

Append a `## Score architecture v2` section under the existing "Agent activity score" entry. The section should:
- Diagram the three signals (activity, compliance, wallet) and where they live.
- Note that `activity_score` is the canonical pillar; `compliance_penalty` and `wallet_activity_score` are derived.
- Pin the Phase 2 negative-assertion invariant for listing pages.

Keep the section under 50 lines; the existing `## Compliance flags` and `## Wallet activity` sections (from prior SDD chains) already cover the detail.

## 7. Risks

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R-1 | Ranking endpoint omits wallet signals — clients must follow up per-agent | low | Document the contract in the spec; the existing per-agent endpoint already returns the full shape |
| R-2 | Listing ranking with `displayed_activity_score` is dominated by agents with `compliance_penalty=0` (clean) | low | Surface the `creator_is_owner` flag in the minimal response so clients can detect self-deployed agents and downrank if desired |
| R-3 | UI consolidation touches OFAC banner + wallet chip + score card — three sites, one block | low | Strict TDD: write a single template test that asserts all three elements are children of `.trust-compliance` |
| R-4 | Possible stale-spec risk if the existing `agent-detail-ui` spec already encodes the current scattered layout | low | Read the spec during the proposal phase; if it conflicts, the `## MODIFIED Requirements` delta in the proposal notes the change |
| R-5 | New `ScoreOutMinimal` model vs reusing `AgentOut` | low | Keep separate; the minimal response is not a `Page[AgentOut]` payload |
| R-6 | No live wallet data when ranking is first queried (the orchestrator hasn't run yet) | info | Document in the proposal: ranking is best-effort until the wallet orchestrator (a future change) populates the column |

## 8. Summary for proposal phase

The smallest cohesive slice:
- One new ranking endpoint `GET /api/agents/score` returning `Page[ScoreOutMinimal]`.
- One UI consolidation: wrap OFAC + wallet + score in a `<section class="trust-compliance">` block.
- One `DESIGN.md` section (50 lines) explaining the three-signal architecture.

All three are independent enough to land in one PR (under the 400-line budget for a single change). The Phase 2 negative-assertion contract is preserved by option (c) above.

## Key Learnings

1. The `ScoreOut` schema after Phase 1 + Phase 2 has **8** fields plus the heavy `pillars` and `breakdown`. The new ranking endpoint needs a leaner projection (`ScoreOutMinimal`) to avoid 20 × `pillars` payloads in a single response.
2. Phase 2's listing-page negative assertion in `tests/test_wallet_activity_pages.py` is the load-bearing guard for the "no N+1 wallet signals on listing" invariant — Phase 3 must not silently break it; option (c) is the resolution that preserves it.
3. The `category` filter on the existing `list_agents` endpoint already accepts `Literal["rebalancing", "other"]` and the `AgentCache.category` column has `server_default "other"` from the compliance-flags migration — Phase 3 reuses both without schema change.
4. The OFAC block banner currently sits near the Hire CTA (line 598-608) and the score card sits near the top of the body (line 179). UI consolidation moves the OFAC banner **up** to the score card region, reversing the implicit "OFAC is a CTA concern" semantic to "OFAC is a trust concern".
5. The existing `get_agent_score` endpoint is per-agent and returns the full `ScoreOut` including wallet signals. Phase 3's ranking endpoint is **not** a list-of-per-agent calls — it is a single SQL query that pre-joins/aggregates at the DB level and returns a minimal projection. This keeps the ranking endpoint O(1) DB queries regardless of limit.
