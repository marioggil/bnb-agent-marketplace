# Exploration — `compliance-flags`

Phase: sdd-explore · change: `compliance-flags` · artifact: `explore.md`
Store: `openspec` · root: `openspec/` · change dir: `openspec/changes/compliance-flags/`

## Plan recap

> "Wire OFAC compliance signals into `agent_score` and the agent detail UI."

Phase 1 of a 3-change SDD chain; chained-PRs; stacked-to-main; strict TDD; review budget 400 lines.

---

## 1. `app/services/flagged_sync.py` — OFAC mirror (CONFIRMED)

Path: `app/services/flagged_sync.py` (161 lines).

Symbols & ranges:
- `FLAGGED_SOURCES: dict[str, str]` — lines 39–46. URL templates use the 0xB10C mirror:
  - `ofac-bsc` → `https://raw.githubusercontent.com/0xB10C/ofac-sanctioned-digital-currency-addresses/lists/sanctioned_addresses_BSC.json`
  - `ofac-eth` → `…/sanctioned_addresses_ETH.json`
- `SOURCE_LABELS`, `SOURCE_REPO_URL`, `FETCH_TIMEOUT_S = 15.0` — lines 49–60.
- `_normalize_addresses(raw)` — lowercase + dedupe per source (lines 73–88).
- `_replace_source(session, source, addresses)` — DELETE-then-INSERT per source (lines 91–101).
- `async def refresh_flagged_addresses() -> FlaggedSyncReport` — entry point (lines 115–143). Fetches every source in `FLAGGED_SOURCES`, REPLACEs rows, returns per-source counts and totals.

Trigger: **API only.** No n8n, no cron, no scheduler in this repo. The only caller is `POST /api/sync/flagged` in `app/routers/sync.py:148`, guarded by `_flag_sync_lock` and `X-API-Key`. Design doc note (D8 line in DESIGN.md): *"T2 wallet flags shipped in commit 55318da; /flagged page exists; production sync pending as of this writing."* So nightly cron is still TODO at the org level.

How this change will touch it: probably nothing in `flagged_sync.py` itself — it already produces a usable mirror. The change is downstream (use the mirror to penalize scores and render badges). Optionally: the proposal could decide to (a) keep the manual API trigger, (b) extend it with a retry/etag, or (c) wire it to a cron. The DB schema for `flagged_addresses` already exists (migration `0007_flagged_addresses`).

Open questions for proposal:
- Should we add a scheduled trigger for `refresh_flagged_addresses()` so the OFAC mirror stays current, or do we treat that as out-of-scope for this slice (the approved plan is "wire signals into agent_score and the UI", not "schedule the sync")?
- Do we need a column on `flagged_addresses` recording the `first_seen_at` / `list_added_at` for compliance audit purposes, or is the existing `created_at`/`updated_at` enough?

---

## 2. `app/db/models/agent.py` — `AgentCache` (CONFIRMED, with one ambiguity)

Path: `app/db/models/agent.py` (332 lines).

Symbols & ranges:
- `AgentCache` model — identity columns (`agent_id`, `chain_id`, `token_id`, `registry_address`) lines 61–67.
- **Owner columns** (lines 69–82):
  - `creator_address: Mapped[str | None]` — line 74 (the on-chain creator of the agent NFT).
  - `owner_id`, `owner_ens`, `owner_username`, `owner_avatar_url`, `owner_publisher_tier`, `owner_certified_name` — 74–80.
  - `owner_address: Mapped[str | None]` — line 81 (the EVM address used as the OFAC check anchor in the current badge logic).
- **Score columns** (lines 124–127, 188–200):
  - `average_score: Mapped[Decimal | None] = Numeric(6, 2)` — line 125 (upstream 8004scan snapshot).
  - `total_score`, `total_feedbacks`, `total_validations`, `successful_validations`, `rank`, `network_rank`, `scores` (JSONB) — 125–131.
  - **Quality scores block (0–100)** — lines 194–199:
    - `quality_score`, `popularity_score`, **`activity_score`**, **`wallet_score`**, `freshness_score`, `metadata_completeness_score` — all `Numeric(5, 2)`, nullable.
- **No column named `agent_score`** exists. The model has `activity_score` (locally computed, written by `materialize_score`) and `wallet_score` (mirrored from upstream 8004scan's `scores.wallet`). See *Plan discrepancy* below.
- `agent_wallet: Mapped[str | None]` — line 95 (the payment wallet — also checked against OFAC today).
- `CheckConstraint` on `average_score` (line 303) — `0 ≤ average_score ≤ 100`. **No equivalent CHECK on `wallet_score`/`activity_score`** — the OFAC penalty needs to respect 0..100 if we materialize it.

How this change will touch it: no schema additions expected. The change will read `agent.owner_address`, `agent.creator_address`, and `agent.agent_wallet` and join against `flagged_addresses`. Optionally: if the proposal decides to persist a `wallet_risk_score` column or a `compliance_status` enum, that needs a migration (next filename: `0012_*`).

Open questions for proposal:
- Which wallet(s) trigger the penalty — `owner_address`, `creator_address`, or `agent_wallet` (payment)? The current T2 badge logic checks `owner_address` (card + detail) and `agent_wallet` (detail). `creator_address` is in the model but not yet in any badge.
- Do we add a CHECK constraint on the new "effective score" so the OFAC penalty can never push it below 0?

---

## 3. `app/services/agent_score.py` — Activity score service (CONFIRMED, with naming ambiguity)

Path: `app/services/agent_score.py` (273 lines).

Symbols & ranges:
- `ZERO_ADDRESS`, `TRACK_WINDOW_DAYS = 90` — lines 30–32.
- `_PROBE_WEIGHTS` (0.5/0.3/0.1/0.1) — lines 37–40.
- `_TRACK_WEIGHTS` (0.25 each) — lines 43–48.
- `latency_band_points`, `_probe_parts`, `_track_parts`, `compute_probe_pillar`, `compute_track_record_pillar`, `composite_score`, `build_breakdown` — pure helpers, lines 53–175.
- `TrackRecord` dataclass — lines 184–189.
- `async def fetch_track_record(session, agent_id) -> TrackRecord` — lines 192–227.
- **`async def materialize_score(session, agent_id, score)` — lines 230–233.** Writes to `agent_cache.activity_score` (`update(AgentCache).where(...).values(activity_score=...)`).

There is **no second service**. The module is named `agent_score.py` because it scores agents; the column it writes is `activity_score`. The full score pipeline is:
- `app/services/probe_worker.py:165–166` — recomputes probe + track pillars after each A2A probe and calls `materialize_score(... composite_score(probe, track))`.
- `app/routers/agents.py:264` — D5 lazy path: when `activity_score IS NULL`, recompute + materialize inside `GET /api/agents/{chain}/{token}/score`.
- `app/routers/pages.py:839–853` — agent detail page reads `row.activity_score` and `agent_score.build_breakdown(...)` for the local breakdown panel.

How this change will touch it: the most natural fit for the OFAC penalty is a thin wrapper that:
1. Computes `composite = composite_score(probe, track)` as today.
2. Looks up `agent.owner_address` (and/or `agent_wallet`, `creator_address`) in `flagged_addresses`.
3. Subtracts a fixed penalty (e.g. 25 points) and clamps to `[0, 100]`.
4. Calls `materialize_score(session, agent_id, effective)` so the persisted column reflects the compliance-adjusted score.

This keeps the OFAC logic out of the pure helpers (which are unit-tested for S4 / D6 / D7 / D8 math) and concentrated in a single new function with its own TDD. The `breakdown` shape that the detail page renders (`[{dimension, score, weight}]`) does **not** currently include a "compliance" dimension; adding one would change the JSON contract of `GET /api/agents/{chain}/{token}/score` (schema in `app/schemas/score.py`). That schema has no compliance field today.

Where the existing `wallet_score` lives: it is **only mirrored from upstream 8004scan** (see `app/services/sync_worker.py:316`, `app/services/client_8004scan.py:177`). There is no local computation. The 8004scan `wallet_score` is a 0–100 quality score (not a compliance flag). Wiring the OFAC penalty into `wallet_score` is one option; wiring it into `activity_score` (so the locally-computed score reflects compliance) is another. They have different blast radius.

Open questions for proposal:
- Confirm which column is "agent_score" in the approved plan. If it's `activity_score`, the OFAC penalty needs to be applied at every materialize site (probe worker + D5 lazy path + detail page). If it's a brand-new column, we need a migration and a CHECK constraint.
- Should the penalty be subtracted from `activity_score` before clamping to 0, or should the agent simply be hidden/disabled at the card/detail level when ANY of the three addresses is flagged? Today's badges are visible-but-warn; the plan could be either "warn" or "zero out" — different code paths.

---

## 4. Routers

### `app/routers/agents.py`

- Router: `router = APIRouter(prefix="/api/agents", tags=["agents"])` (line 26).
- `_SORT_KEYS` (lines 40–46) — `average_score`, `total_feedbacks`, `created_at`, `name`, `activity_score` (A3). No `agent_score` / no compliance sort key.
- `GET /api/agents` — `list_agents` (lines 51–100), paginated filterable listing.
- **`GET /api/agents/{chain_id}/{token_id}`** — `get_agent` (lines 122–131). Returns `AgentOut`. Already 404s on miss.
- Helpers `latest_probe_for`, `probe_pillar_from_row`, `track_pillar_from_record`, `pillars_for_agent`, `breakdown_for` (lines 135–184).
- `GET /api/agents/compare?ids=…` (lines 196–224).
- **`GET /api/agents/{chain_id}/{token_id}/score`** — `get_agent_score` (lines 234–273). Returns `ScoreOut` with pillars + breakdown; lazy-materializes `activity_score` if NULL.

How this change will touch it: if the penalty is subtracted at materialize time, the `/score` endpoint picks it up for free via `row.activity_score`. The detail page (`/agents/{chain}/{token}` HTMX page) already loads `activity_score` and computes `local_breakdown` from the probe/track record; a "compliance_penalty" row would slot into `breakdown_for(...)` but that means the `ScoreOut.breakdown` shape grows — possibly breaking clients/tests that pin the dimension list.

### `app/routers/pages.py`

- `_flagged_addresses_set() -> set[str]` (lines 357–371) — **already exists**. One query, lowercase addresses, used by the home and detail pages to render `.badge.risk` next to owners and payment wallets. **This is the single hook for "is this agent's owner/creator/wallet flagged today"** and is what we should reuse for the compliance flagging in the score path.
- `_list_agents_page(...)` (lines 137–191) — listing query used by the home page. `_flagged_addresses_set()` is fetched once per page render and passed into the template as `flagged_addresses`.
- `home(request, ...)` (lines 686–768) — passes `flagged_addresses` to both `pages/home.html` and `partials/agent_card_htmx.html`.
- `agent_detail(request, chain_id, token_id)` (lines 770–880) — fetches `flagged_addresses = await _flagged_addresses_set()` (line 856) and passes it to `pages/agent_detail.html`. This is where the existing `.badge.risk` markup at agent_detail.html:32 (owner) and :111 (payment wallet) is rendered.
- `flagged_page(request)` (lines 904–922) — public `/flagged` page, reads `FlaggedAddress` rows directly.

How this change will touch it: nothing structural — `_flagged_addresses_set()` is the right reuse point for any new template-side gating (e.g. disabling the Hire button). If we want to suppress the activity score entirely (not just subtract), the route handler would need to read `flagged_addresses` before computing `local_score`/`local_breakdown`.

### `app/routers/sync.py`

- `POST /api/sync/flagged` (line 148) — calls `refresh_flagged_addresses()` under `_flag_sync_lock`. Already returns the `FlaggedSyncReport` as JSON.

### `app/routers/hires.py` and `app/routers/onchain_hires.py`

- The Hire flow doesn't currently check OFAC. The hire CTA at agent_detail.html:514 is wired to `payment.js`, which calls `POST /api/hires`. For this slice we likely leave the API alone and only mark the UI (badges, score deduction).

---

## 5. `app/templates/pages/agent_detail.html`

Path: `app/templates/pages/agent_detail.html` (~660 lines).

Relevant regions:
- **Owner block (lines 27–34)** — owner address with link to `/?owner=<addr>` and the `.badge.risk` "flagged wallet" badge when the owner is in `flagged_addresses`. This is the existing compliance rendering for owners.
- **Score block (lines 36–44)** — currently shows `feedback_avg` (locally mirrored average) or upstream `agent.average_score`. The OFAC-aware score probably wants to live here OR in the Activity score card below.
- **Payment block (lines 105–115)** — `profile.wallet` rendered with the `.badge.risk` when the payment wallet is flagged. Existing pattern to mirror.
- **Activity score card (lines 287–304)** — `<section class="agent-profile activity-score-card" id="activity-score">` rendering `{{ local_score or 'n/a' }}/100` and `local_breakdown`. This is the natural place to surface the compliance-adjusted score; the label "Activity score" may need a sub-line like "(OFAC-adjusted)" or "(compliance penalty applied)".
- **Hire button (line ~514)** — `<button id="hire-cta" class="btn btn-primary" data-agent-id="…" data-agent-url="…" data-csrf="…">Hire for $X.XX</button>`. The `data-*` attributes are what `payment.js` consumes. **Disabling the Hire button when the owner/payment wallet is flagged is one of the natural extensions** — set `disabled` on the button and add a hint (same pattern as the `profile.hireable` else-branch below it).
- **`<script>` tags at end (lines ~643–644)** — `/static/js/ethers-6.14.min.js` + `/static/js/payment.js` (defer). No agent-detail-specific JS file.

How this change will touch it: at minimum, the score line and the hire button area need compliance state. We can drive it entirely from the template + `_flagged_addresses_set()` — no JS change needed.

Open questions for proposal:
- Should the OFAC-adjusted `local_score` carry a `.badge.risk` next to the number, like the existing owner/payment badges? Or just a tooltip?
- Should the Hire button be disabled when the OWNER is flagged (even though the payment wallet is clean)? Today's badges already warn in that case; the proposal may decide to keep warning rather than disabling.

---

## 6. `app/static/js/agent-detail.mjs` — **DOES NOT EXIST**

Verified via `find app/static/js/**/*.mjs` (no matches) and listing the directory:
```
app/static/js/auth.js
app/static/js/ethers-6.14.min.js   (vendored)
app/static/js/htmx-2.x.min.js      (vendored)
app/static/js/payment.js           (the Hire button handler)
```

The Hire button client-side handler lives at **`app/static/js/payment.js`** (~165 lines). It:
- Reads `data-agent-id`, `data-agent-url`, `data-csrf` off `#hire-cta`.
- POSTs to `/api/hires`, signs an EIP-712 `TransferWithAuthorization`, POSTs `/api/hires/{id}/pay`, redirects on success.
- Never reads or sets compliance flags.

How this change will touch it: probably nothing. The plan ("agent detail UI") is satisfied by template-level wiring (badge text, button disabled state, score label). If we want client-side gating (e.g. intercept the click before fetch), the cleanest extension point is a new lightweight `app/static/js/compliance.js` loaded alongside `payment.js`, OR a small block inside the existing `DOMContentLoaded` handler in `payment.js`. The budget (400 lines) and chained-PR discipline argue for the former.

Open question for proposal:
- Is the new compliance UI **purely declarative** (badge + disabled button in the template), or does the hire flow need to abort client-side when the wallet becomes flagged mid-session?

---

## 7. `migrations/versions/`

Pattern: `NNNN_short_snake_description.py`. Existing files:
```
0001_initial.py
0002_enrich_agent_cache.py
0003_health_status_jsonb.py
0004_hired_payment_cols.py
0005_onchain_index.py
0006_agent_probes.py
0007_flagged_addresses.py        ← OFAC mirror table already exists
0008_agent_feedbacks.py
0009_category_drop_x402_default.py
0010_onchain_array.py
0011_fix_onchain_null_array.py   ← current head
```

`0007_flagged_addresses.py` already created `flagged_addresses (address VARCHAR(42), source VARCHAR(64), created_at, updated_at, PRIMARY KEY (address, source))`. **`flagged_addresses` is fully migrated.** No migration needed for the OFAC mirror itself.

Next filename pattern: `0012_<topic>.py` with `revision = "0012_<topic>"` and `down_revision = "0011_fix_onchain_null_array"`.

How this change will touch it: only if the proposal introduces a new column on `agent_cache` (e.g. `compliance_status` enum, `wallet_risk_score`, or a generated column for the effective score). If the proposal sticks to **subtracting from `activity_score` at materialize time**, no migration is needed.

Open question for proposal:
- Do we materialize the OFAC-adjusted score into `agent_cache`, or compute it on read? Compute-on-read is cheaper to ship (no migration, no recompute job) but couples the detail page to the OFAC mirror. Materialize-on-write is correct long-term but needs the migration + a backfill strategy.

---

## 8. `tests/`

### `tests/test_flagged.py` (264 lines) — **already covers the surface this change extends**

Covers:
- `refresh_flagged_addresses()` — insert, normalize case/dedupe, replace on re-run, shared address in both sources (4 tests).
- `POST /api/sync/flagged` — 401 missing key, 401 wrong key, 503 unconfigured, 200 persists rows (4 tests).
- `GET /` (home card) — badge for flagged owner, no badge for clean owner (2 tests).
- `GET /agents/56/1` (detail) — badges for flagged creator AND payment wallet, no badges for clean agent (2 tests).
- `GET /flagged` — lists addresses + sources, empty state, public access (3 tests).

The compliance-penalty code this change adds would live alongside these tests — likely in `tests/test_agent_score.py` (for the math) and additional cases in `tests/test_pages.py` (for the badge/score rendering) and `tests/test_api_agents.py` (for the JSON shape).

### `tests/test_agent_score.py` (180+ lines) — pure-function tests for the activity score

Covers `latency_band_points`, `compute_probe_pillar`, `compute_track_record_pillar`, `composite_score`, `build_breakdown`; and DB-backed `fetch_track_record` (4 sqlite tests) + `materialize_score` (1 sqlite test).

How this change will touch it: if the OFAC penalty becomes a pure helper (e.g. `compliance_adjusted_score(base, owner_flagged, wallet_flagged, creator_flagged, penalty=25)`), the unit tests for that helper slot in here. If the penalty is implemented as an SQL UPDATE in `agent_score.py`, the integration tests go in `tests/test_agent_score.py` alongside `test_materialize_score_writes_activity_score`.

### `tests/test_pages.py` (~640 lines) — page rendering

Has `test_agent_detail_renders_hire_panel`, `test_agent_score_uses_feedback_average`, `test_agent_detail_rank_crosschain_endpoint`, plus all the filter/HTMX/owner tests. The "compliance badge on detail page" tests already live in `tests/test_flagged.py:test_detail_shows_badges_for_flagged_creator_and_payment_wallet` — no duplication needed unless we add a "compliance-adjusted score" assertion.

### `tests/test_pages_x402.py` (~100 lines) — Hire CTA rendering

Pins the rendered HTML for `id="hire-cta"`, `data-agent-url`, `data-agent-id`, `data-csrf`, `disabled`, the hint copy, and the status UI. If we add `disabled` to the Hire button under compliance, we need a test here mirroring `test_cta_disabled_without_wallet`.

### `tests/test_api_agents.py` — JSON shape coverage

This is where a new "compliance-adjusted activity_score" assertion would go if the proposal chooses to expose the compliance flag in the API (e.g. via `AgentOut.compliance_status`).

---

## Plan discrepancy

> ⚠️ The approved plan says *"wire OFAC compliance signals into `agent_score`"*, but **there is no `agent_score` column or service by that name** in the current codebase.

What exists:
- A module `app/services/agent_score.py` whose only score-writing function (`materialize_score`) targets the column `agent_cache.activity_score`.
- A column `agent_cache.wallet_score` currently populated **only** by mirroring the upstream 8004scan `scores.wallet` (in `app/services/sync_worker.py:316`); no local computation.
- No column named `agent_score`, no service named `agent_score` other than the module, no `ScoreOut.agent_score` field.

Two coherent interpretations the proposal must pick between (or call `human-review` to disambiguate):

1. **`activity_score` is the target** — wire the penalty into the locally-computed composite. Apply at every materialize site (probe worker + D5 lazy path). Pros: matches the module name; uses the existing breakdown + score endpoints. Cons: subtracts a compliance penalty from a *quality* signal, which can mislead users ("why is my agent's activity low?"); requires CHECK on the result.

2. **`wallet_score` is the target** — re-purpose the upstream-mirrored wallet_score into the local compliance-aware one. Pros: semantically right (wallet risk IS compliance). Cons: breaks the existing meaning of `wallet_score` (it's currently a 0–100 quality metric from 8004scan); requires migration + recompute; existing sort key `?sort=wallet_score` would change behavior.

3. **New column** — `wallet_risk_score` or `compliance_status` enum. Cleanest semantic split; needs migration `0012_*` and a CHECK; score endpoints can expose both columns.

The orchestrator should surface this to the user before the proposal phase if it cannot infer the intent from the surrounding 3-change plan context.

> ⚠️ The plan references `app/static/js/agent-detail.mjs` but **no such file exists**. The Hire button handler is `app/static/js/payment.js`. This is a doc-only mismatch — the surface to edit is well-defined either way.

> ⚠️ There is **no nightly cron** calling `refresh_flagged_addresses()` in this repo. It is API-triggered only. If the compliance-adjusted score is materialized on a schedule (rather than on every read), the scheduler needs to be added (or documented as operator-run).

---

## Summary for proposal phase

The smallest cohesive slice this change represents: **read the existing `flagged_addresses` mirror; subtract a fixed penalty from `activity_score` whenever `owner_address`, `creator_address`, or `agent_wallet` is in the mirror; render a compliance badge on the detail page; gate the Hire button when any of those three addresses is flagged.** This is one PR that:
- adds one pure helper `compliance_adjusted_score(...)` in `app/services/agent_score.py` (TDD: unit tests in `tests/test_agent_score.py`);
- plumbs the helper into the existing materialize sites (probe_worker + D5 lazy path) and the detail-page `local_score` path;
- adds a `flagged_owner_or_creator` boolean computed in `pages.py::agent_detail` from the existing `_flagged_addresses_set()` and passes it to the template;
- renders the existing `.badge.risk` next to the Activity score headline and adds `disabled` + hint to `#hire-cta`;
- reuses `tests/test_flagged.py`'s detail assertions and adds 1–2 new cases for the score + disabled-CTA.

No new migration is required (the OFAC mirror already exists from migration `0007_*`); no new JS file is required; the plan-discrepancy on the `agent_score` column name must be resolved (recommend "apply the penalty to `activity_score`" since that matches the module name and the only materialized column the system owns locally).

Within the 400-line review budget, this is achievable: ~40 lines pure helper + ~30 lines plumb + ~15 lines template/handler changes + ~80 lines tests. The chained-PR plan (this is phase 1 of 3) means future phases can stack on the helper without re-litigating the column choice.
