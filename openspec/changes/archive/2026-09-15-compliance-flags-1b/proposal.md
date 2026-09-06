# Proposal: compliance-flags — wire OFAC signals into a dedicated compliance penalty

## Change name
`compliance-flags`

## Domain
`agent-compliance` (new spec)

## Status
Draft

---

## Problem

OFAC-sanctioned addresses are already mirrored into `flagged_addresses` by `app/services/flagged_sync.py::refresh_flagged_addresses()` (migration `0007_flagged_addresses`), and `_flagged_addresses_set()` (`app/routers/pages.py:260`) already renders `.badge.risk` markers next to the owner and payment wallet on the home and agent-detail pages. The trust signal is visible — but it has three gaps:

1. **No numeric penalty.** The owner badge is informational only. There is no place in the schema or in `GET /api/agents/{chain}/{token}/score` that records "this agent is owned by an OFAC-sanctioned wallet." Agents carrying flagged owners sit next to clean agents with identical scores.
2. **Per-call, not per-agent.** The payment-wallet badge fires on the agent detail page (`agent_detail.html:111`) but is not propagated to the score or surfaced in `/score` JSON. Two agents with identical activity can look identical to API consumers.
3. **No Hire gate.** The Hire CTA (`agent_detail.html:514`, wired to `app/static/js/payment.js`) is unconditionally enabled. A user can initiate a hire against an agent whose owner AND creator are both on the OFAC list — no upstream warning, no `disabled` state, no reason copy.

`explore.md` (§"Plan discrepancy") flagged that the original plan name (`agent_score`) does not exist as a column or a service. This change resolves that ambiguity by introducing a **separate** signal — `compliance_penalty` — that sits alongside (not inside) the existing `activity_score`.

## Existing evidence

- `openspec/changes/compliance-flags/explore.md` — full surface audit: `flagged_sync.py`, `flagged_address.py`, `agent.py`, `agent_score.py`, `agents.py`, `pages.py`, `agent_detail.html`, `payment.js`, `migrations/versions/`, `tests/`.
- `DESIGN.md` D8: T2 wallet flags shipped in commit 55318da; nightly cron still TODO at the org level.
- `tests/test_flagged.py` already exercises the surface this change extends (`refresh_flagged_addresses`, `POST /api/sync/flagged`, home + detail badge rendering).
- `tests/test_pages_x402.py` already pins `#hire-cta` HTML (`disabled`, `data-*` attributes); the new compliance gate is a direct extension of `test_cta_disabled_without_wallet`.

## What needs to change

### Model + migration (`migrations/versions/0012_compliance_penalty.py`, next head)

- Add `agent_cache.compliance_penalty: Numeric(5, 2) NOT NULL DEFAULT 0` (matches the existing `activity_score` / `wallet_score` shape — see `app/db/models/agent.py:194–199`). Existing rows backfill to 0.
- New table `agent_compliance_flags`:
  - `agent_id: String(255) PRIMARY KEY` (matches `AgentCache.agent_id` semantics).
  - `creator_flagged: Boolean NOT NULL DEFAULT false`.
  - `owner_flagged: Boolean NOT NULL DEFAULT false`.
  - `computed_at: DateTime(timezone=True) NOT NULL` (set on every upsert).
- `CHECK (compliance_penalty >= 0)` on `agent_cache` to enforce the penalty floor.
- `activity_score`, `wallet_score`, and all other scoring columns are **untouched**.

### Service — `app/services/compliance_refresh.py` (new module)

- `compute_penalty(creator_flagged: bool, owner_flagged: bool) -> Decimal` — pure helper: `min(30 * int(creator_flagged) + 30 * int(owner_flagged), 50)`. Lives next to `composite_score` in spirit (pure, TDD-friendly).
- `async def refresh_agent_compliance_flags(session) -> ComplianceRefreshReport` — reads the current `flagged_addresses` set, joins against every `agent_cache` row whose `creator_address` or `owner_address` matches (case-insensitive exact match; addresses compared lowercased on both sides), UPSERTs `agent_compliance_flags (creator_flagged, owner_flagged, computed_at)`, and writes `compliance_penalty = compute_penalty(...)` onto `agent_cache`. Returns counts (matched, upserted, penalty distribution).
- `async def run_compliance_refresh() -> ComplianceRefreshReport` — orchestrator: calls `refresh_flagged_addresses()` then `refresh_agent_compliance_flags(session)` in sequence. Idempotent. Returns a single report covering both phases.
- `def flagged_data_stale(session, threshold_hours: int = 24) -> bool` — derived boolean: `now() - max(flagged_addresses.updated_at) > threshold_hours`. No schema addition needed (`FlaggedAddress.updated_at` is already set on every `_replace_source` write — see `flagged_sync.py:91–101`). Threshold is a module constant; the explore recommended 24h.

### Endpoint — `app/routers/admin.py` (new) or extend existing admin surface

- `POST /api/admin/compliance/refresh` — guarded by `X-API-Key` like the existing `POST /api/sync/flagged`. Calls `run_compliance_refresh()`. Returns the combined report as JSON. **This is the operator trigger** — there is no in-process scheduler in this repo (per explore §1, §7) and adding one is out of scope.
- `GET /api/admin/compliance/status` — returns `{ last_refreshed_at, flagged_data_stale, row_count }` for ops visibility.

### Templates + handler — `app/routers/pages.py` + `app/templates/pages/agent_detail.html`

- Extend `agent_detail(request, chain_id, token_id)` (`pages.py:793`) to read the agent's `agent_compliance_flags` row and pass `creator_flagged`, `owner_flagged`, `compliance_penalty`, `displayed_activity_score` (= `max(0, activity_score - compliance_penalty)`) to the template alongside the existing `flagged_addresses` set.
- Render a new compliance badge next to the activity score headline (`agent_detail.html:287–304`) labelled "Compliance: −N pts" when `compliance_penalty > 0`. Visible warning when only one of `{creator, owner}` is flagged.
- Render an explicit "OFAC: hiring blocked" banner on the detail page when `creator_flagged AND owner_flagged` are both true.
- Add `disabled` and `aria-disabled="true"` to `#hire-cta` (`agent_detail.html:514`) **only** when both flags are true. Reason copy: "Hiring is disabled while OFAC compliance is unresolved for this agent."

### Score endpoint contract

- `GET /api/agents/{chain}/{token}/score` (`app/routers/agents.py:234`) — additive fields on `ScoreOut` (`app/schemas/score.py`):
  - `compliance_penalty: float` (raw stored penalty, 0.0..50.0).
  - `displayed_activity_score: float` (= `max(0, activity_score - compliance_penalty)`).
- `activity_score` field on `ScoreOut` **stays unchanged** — the stored value is the canonical activity score, and `compliance_penalty` is the canonical penalty. Clients compute display themselves if they need to; the convenience field is additive.
- `materialize_score()` (`app/services/agent_score.py:230`) is **untouched**.

### Client — `app/static/js/payment.js`

- No new client-side gating. The `disabled` attribute on `#hire-cta` is server-rendered; `payment.js` already short-circuits on `cta.disabled` (existing pattern). If a future change wants in-session re-evaluation, that lands in `payment.js` (not a new file — `agent-detail.mjs` does not exist per explore §6).

## Scope

**In**

- New `agent_cache.compliance_penalty` column + CHECK constraint.
- New `agent_compliance_flags` table.
- Migration `0012_compliance_penalty.py`.
- `app/services/compliance_refresh.py` (new module).
- `app/routers/admin.py` (new) with `POST /api/admin/compliance/refresh` + `GET /api/admin/compliance/status`.
- `ScoreOut.compliance_penalty` + `displayed_activity_score` additive fields.
- `app/routers/pages.py::agent_detail` reads compliance row + renders warning + disables `#hire-cta`.
- `app/templates/pages/agent_detail.html` template changes (score badge, OFAC banner, hire-cta `disabled`).
- Tests: `tests/test_compliance_penalty.py` (pure helper math), extensions in `tests/test_flagged.py` (refresh-job idempotence + Hire gate), extensions in `tests/test_pages_x402.py` (compliance-disabled Hire CTA), extensions in `tests/test_score_api.py` (new `ScoreOut` fields).

**Out**

- Any mutation to `activity_score` (stored value stays canonical).
- Any mutation to `wallet_score` (Phase 2's `wallet-activity-pillar` concern).
- Real scheduler (cron, n8n, Celery beat, APScheduler). Operator-triggered via admin endpoint only.
- UI changes on the home/listing page (badges there stay as-is; compliance score is detail-page only in this slice).
- Migration changes to `flagged_addresses` itself (the explore flagged an optional `first_seen_at` column; deferred).
- Phase 2 (`wallet-activity-pillar`) and Phase 3 (`agent-score-integration`) of the chain.

## Acceptance criteria

- **AC-1 — Penalty math.** `compute_penalty(False, False) == 0`, `compute_penalty(True, False) == 30`, `compute_penalty(False, True) == 30`, `compute_penalty(True, True) == 50` (capped). Pure-function tests in `tests/test_compliance_penalty.py`.
- **AC-2 — Hire gate.** `GET /agents/{chain}/{token}` (detail page render) for an agent where both `creator_flagged` and `owner_flagged` are true renders `#hire-cta` with `disabled` set and an OFAC banner. Single-flag agents render a warning copy but `disabled` is **not** set.
- **AC-3 — Refresh job is idempotent.** Running `run_compliance_refresh()` twice in a row produces the same `agent_compliance_flags` + `agent_cache.compliance_penalty` state and a stable count. `last_refreshed_at` (derived from `max(flagged_addresses.updated_at)`) advances after each call.
- **AC-4 — Case-insensitive exact match.** An agent with `creator_address="0xAbC..."` (mixed case) is matched against a `flagged_addresses` row stored as `"0xabc..."`. Heuristic / cluster / prefix matching is **not** permitted. Address normalization happens on read (lowercase) only.
- **AC-5 — Targeted pytest suite green, baseline preserved.** `uv run pytest tests/test_compliance_penalty.py tests/test_flagged.py tests/test_pages_x402.py tests/test_score_api.py tests/test_pages.py tests/test_agent_score.py` all pass. No previously-passing test in the 282-test baseline regresses.
- **AC-6 — Displayed score formula.** For any agent with `activity_score=72.50` and `compliance_penalty=30.00`, the detail page and `GET /api/agents/{chain}/{token}/score` both render `displayed_activity_score = 42.50`. When `compliance_penalty >= activity_score`, the displayed value is `0.00` (clipped, never negative). Stored `activity_score` remains `72.50`.
- **AC-7 — `flagged_data_stale` flag.** After `refresh_flagged_addresses()` runs, `flagged_data_stale()` returns `False`. After the threshold elapses without a refresh (simulated by backdating `flagged_addresses.updated_at`), it returns `True`. Visible on `GET /api/admin/compliance/status`.

## Risks

- **Stale-data risk.** If `POST /api/admin/compliance/refresh` is not called, the `agent_compliance_flags` table grows stale. Mitigated by `flagged_data_stale` derived flag (AC-7) exposed on the status endpoint; the operator dashboard can alert on it. Long-term mitigation (real scheduler) is Phase 2+ work.
- **Phase 2 collision.** Phase 2 (`wallet-activity-pillar`) modifies scoring math. By keeping `activity_score` immutable here and confining this change to a separate `compliance_penalty` column + display-side subtraction, the two changes can land in either order without one stomping the other.
- **Migration ordering risk.** Migration filename `0012_compliance_penalty.py` is the next slot after `0011_fix_onchain_null_array`. If a concurrent PR lands a `0012_*` first, this migration renumbers to `0013_*`. Mitigated by using the standard Alembic `down_revision` chain and checking `migrations/versions/` immediately before branching the apply phase.
- **Score endpoint contract.** Adding `compliance_penalty` + `displayed_activity_score` to `ScoreOut` is additive and cannot break existing JSON clients, but downstream consumers that pin `ScoreOut` schema in tests need new fields accounted for. Mitigated by updating `tests/test_score_api.py` alongside the change.
- **Hire-CTA test brittleness.** `tests/test_pages_x402.py` pins `#hire-cta` HTML. Adding `disabled` as a new possible state requires new cases; the existing `test_cta_disabled_without_wallet` continues to pass unchanged because the absence of a wallet is orthogonal to OFAC compliance.

## Out of scope (non-goals)

- Real in-process scheduler or n8n integration (org-level concern per DESIGN.md D8).
- Changes to `flagged_sync.py` itself (retry, etag, metrics).
- Listing-page UI changes (home + category pages stay as-is).
- Audit / evidence-of-refresh persistence beyond `computed_at` on the new table.
- `first_seen_at` / `list_added_at` columns on `flagged_addresses`.
- Payment-wallet (`agent_wallet`) compliance — owner + creator only in this slice; payment-wallet coverage is a Phase 2 decision.

## Related changes

- **Phase 2: `wallet-activity-pillar`** — depends on this change landing first; uses the same `compliance_penalty` signal but extends penalty computation to `agent_wallet`.
- **Phase 3: `agent-score-integration`** — consolidates Phase 1 + 2 surfaces in the UI; assumes `compliance_penalty` column exists and `agent_compliance_flags` table is populated.
- **`chore-close-stale-changes`** — adjacent pre-work that closes orphan change directories before this chain starts; not modified by this change.
