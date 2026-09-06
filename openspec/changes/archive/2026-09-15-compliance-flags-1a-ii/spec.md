# Agent Compliance — OFAC Penalty Signal

**Change:** `compliance-flags`
**Domain:** `agent-compliance` (storage: `agent_cache.compliance_penalty` + new `agent_compliance_flags` table in `app/db/models/agent.py`/new compliance model; service: new `app/services/compliance_refresh.py`; admin router: new `app/routers/admin.py`; UI: `app/templates/pages/agent_detail.html` + `app/static/js/payment.js`)
**Scope:** Data + minimal UI. Add a dedicated `compliance_penalty` column and a per-agent `agent_compliance_flags` row, surface a display-side `displayed_activity_score = max(0, activity_score - compliance_penalty)`, gate the Hire CTA when both `creator_flagged` and `owner_flagged` are true, and expose an operator-triggered `POST /api/admin/compliance/refresh` endpoint that chains the existing OFAC mirror refresh with the new compliance-flag refresh. `activity_score`, `wallet_score`, and all other score columns stay untouched.

**Output path note:** This spec is written flat at `openspec/changes/compliance-flags/spec.md` per the parent task's explicit output path, instead of the nested `specs/{domain}/spec.md` layout. Archive will treat this as a full new domain spec and copy it verbatim to `openspec/specs/agent-compliance/spec.md`. No canonical spec for the `agent-compliance` domain exists today — this is a new catalog entry created on archive.

---

## Purpose

OFAC sanctions are already mirrored into `flagged_addresses` (migration `0007_flagged_addresses`, populated by `app/services/flagged_sync.py::refresh_flagged_addresses()`). Owner and payment-wallet addresses already render `.badge.risk` markers on the home and agent-detail pages via `_flagged_addresses_set()` in `app/routers/pages.py`. The *visibility* signal exists; the *numeric* and *gating* signals do not. As a result an agent whose creator and owner are both OFAC-sanctioned sits next to a clean agent with an identical score, and the Hire CTA stays enabled.

This change adds a separate, additive compliance signal that lives in its own column and table rather than mutating existing scores:

- `agent_cache.compliance_penalty` — a `0.00..50.00` `Numeric(5, 2)` scalar derived from how many of the agent's `creator_address` and `owner_address` are present in the `flagged_addresses` mirror. The penalty has a strict floor (`CHECK (compliance_penalty >= 0)`), `NOT NULL`, default `0.00`, backfilled for existing rows. It is a *display-side* adjustment: the persisted `activity_score` is **never mutated** by this change. Confining the compliance signal to its own column leaves `activity_score` immutable so Phase 2 (`wallet-activity-pillar`) and Phase 3 (`agent-score-integration`) of this SDD chain can re-use the column without rewriting materialized values.

- `agent_compliance_flags` — a per-agent row keyed by `agent_id`. It records `creator_flagged`, `owner_flagged`, the JSONB list of `flagged_addresses.source` strings that contributed each flag, whether the agent's creator and owner resolve to the same EVM address (`creator_is_owner`), a derived `flagged_data_stale` boolean, and the `refreshed_at` timestamp of the most recent successful refresh.

- A display-side formula `displayed_activity_score = max(0, activity_score - compliance_penalty)`. This is what users see in the Activity score card on the agent detail page, and what `GET /api/agents/{chain}/{token}/score` returns in the new additive `displayed_activity_score` field. When the penalty exceeds the activity, the displayed value clips to `0.00` and never goes negative.

- A Hire-CTA gate. When both `creator_flagged AND owner_flagged` are true, `#hire-cta` renders with `disabled` and `aria-disabled="true"`, and the detail page surfaces an OFAC-blocking banner ("Hiring is disabled while OFAC compliance is unresolved for this agent"). Single-flag agents get a visible warning copy but the CTA stays enabled — a single OFAC signal is informational, two are a hard block.

- An operator-triggered refresh. `POST /api/admin/compliance/refresh` calls `run_compliance_refresh()`, which chains the existing `refresh_flagged_addresses()` (the 0xB10C mirror fetch) with a new `refresh_agent_compliance_flags()` (the join + UPSERT + `compliance_penalty` write). Both phases must succeed for the refresh to be considered complete; mirror failure short-circuits the agent-flag phase. `GET /api/admin/compliance/status` returns `{ last_refreshed_at, flagged_data_stale, row_count }` so the operator can alert on staleness (`(now() - max(flagged_addresses.updated_at)) > 24h`).

Penalty values are derived, never written by hand. `compute_penalty(creator_flagged, owner_flagged) -> Decimal` is a pure helper that returns `min(30 * int(creator_flagged) + 30 * int(owner_flagged), 50)` — `0.00` for no flags, `30.00` for one flag, capped at `50.00` for two. The same Decimal boundary matches the column type so unit tests can pin exact values.

---

## Non-Goals

- **No mutation of `activity_score`, `wallet_score`, or any other score column.** Compliance adjustment is display-only, computed against an immutable `compliance_penalty` column. `materialize_score()` in `app/services/agent_score.py` is untouched.
- **No scheduler work inside the repo.** No cron, no APScheduler, no n8n hook, no Celery beat, no `asyncio` background loop. The admin endpoint is the single trigger — the operator (or an external cron the org runs elsewhere) must call `POST /api/admin/compliance/refresh`. DESIGN.md D8's nightly-cron TODO is deferred to Phase 2+.
- **No heuristic / cluster / prefix / suffix OFAC matching.** The match is **exact, case-insensitive** against `flagged_addresses.address`. No prefix-of, no suffix-of, no substring, no Levenshtein, no LLM-based "related address" interpretation. The comparison lowercases both operands at compare time.
- **No `agent_wallet` (payment-wallet) coverage.** This slice covers `creator_address` and `owner_address` only. Extending penalty computation to `agent_wallet` is the Phase 2 (`wallet-activity-pillar`) decision.
- **No home or listing-page UI changes.** Existing `.badge.risk` rendering on the home/category pages stays as-is. UI consolidation across surfaces is Phase 3 (`agent-score-integration`).
- **No changes to `flagged_sync.py` itself** — no retry, etag, rate-limit, or metrics additions. No migration on `flagged_addresses` (the mirror table stays byte-equivalent).
- **No persistence of a compliance-audit log beyond `refreshed_at`** on `agent_compliance_flags`. No `first_seen_at` / `list_added_at` columns on `flagged_addresses`. No per-refresh event log.
- **No PayTo / x402 protocol change.** Hire flow (`POST /api/hires`, `payment.js` EIP-712 signing) is not modified; the gate is server-rendered `disabled` on the existing button.

---

## Acceptance Criteria

All MUST hold after the change is applied:

1. **AC-1 — `agent_cache.compliance_penalty` column exists.** The column is `Numeric(5, 2) NOT NULL DEFAULT 0` with a `CHECK (compliance_penalty >= 0)` server-side constraint. Existing rows backfill to `0.00`. `activity_score` and `wallet_score` are unchanged. Verified by introspecting the schema (`\d agent_cache`) and by negative-write tests asserting that `UPDATE agent_cache SET compliance_penalty = -1` raises an integrity error.
2. **AC-2 — `agent_compliance_flags` table exists** with columns `agent_id String(255) PRIMARY KEY`, `creator_flagged Boolean NOT NULL DEFAULT false`, `creator_flag_sources JSONB NOT NULL DEFAULT '[]'::jsonb`, `owner_flagged Boolean NOT NULL DEFAULT false`, `owner_flag_sources JSONB NOT NULL DEFAULT '[]'::jsonb`, `creator_is_owner Boolean NOT NULL DEFAULT false`, `flagged_data_stale Boolean NOT NULL DEFAULT false`, `refreshed_at timestamptz NOT NULL`. Exactly one row per `agent_id`. Verified by introspecting the schema and by a DB-backed test that asserts an UPSERT never produces two rows for the same `agent_id`.
3. **AC-3 — Orchestrator `run_compliance_refresh()` chains both phases.** Calling it invokes `refresh_flagged_addresses()` first and `refresh_agent_compliance_flags(session)` second, in that order, exactly once each. If `refresh_flagged_addresses()` raises, `refresh_agent_compliance_flags(session)` MUST NOT run and the original exception MUST propagate. On full success the orchestrator returns a single `ComplianceRefreshReport` carrying both phases' counts and the timestamp of the most recent successful mirror update.
4. **AC-4 — Penalty math is correct (table-driven).** `compute_penalty(creator_flagged, owner_flagged) -> Decimal`:
   - `(False, False)` → `Decimal("0.00")`
   - `(True,  False)` → `Decimal("30.00")`
   - `(False, True)`  → `Decimal("30.00")`
   - `(True,  True)`  → `Decimal("50.00")` (cap, **not** `60.00`)
   Pure-function tests in `tests/test_compliance_penalty.py`. The Decimal boundary is asserted literally (not via `pytest.approx`) so accidental float conversions fail loudly.
5. **AC-5 — Hire-CTA gate is server-rendered.** `GET /agents/{chain}/{token}` for an agent where both `creator_flagged` and `owner_flagged` are true renders `#hire-cta` with the `disabled` attribute set, plus an OFAC-blocking banner. Single-flag agents (creator only OR owner only) render a warning banner copy but `disabled` is **not** present on `#hire-cta`. Verified by `tests/test_pages_x402.py` extending the existing `test_cta_disabled_without_wallet` pattern — the new compliance cases are additive, not edits.
6. **AC-6 — Displayed score formula is correct.** For any agent, `displayed_activity_score = max(0, activity_score - compliance_penalty)`. The detail page Activity score card and `GET /api/agents/{chain}/{token}/score` both expose this. When `compliance_penalty >= activity_score`, the displayed value is `0.00` (never negative). The stored `activity_score` value remains the canonical value across the change — verified by reading the row pre- and post-change.
7. **AC-7 — Case-insensitive exact match.** An `agent_cache.creator_address` stored as `"0xAbC1234…"` (mixed-case EIP-55 checksum) matches a `flagged_addresses.address` stored as `"0xabc1234…"` (lowercase). Verified by seeding both sides with different casing in a DB-backed test and asserting `creator_flagged=true`. The match comparison lowercases both operands; the stored mirror row is never mutated by this change.
8. **AC-8 — Stale-data flag derives from mirror freshness.** `flagged_data_stale() -> bool` returns `True` iff `(now() - max(flagged_addresses.updated_at)) > threshold_hours` (default 24h). After a successful `refresh_flagged_addresses()` the function returns `False`; after backdating every `flagged_addresses.updated_at` to >24h ago, it returns `True`. The boolean is also written into `agent_compliance_flags.flagged_data_stale` on every refresh, and surfaced on `GET /api/admin/compliance/status`. Empty mirror counts as stale.
9. **AC-9 — Targeted pytest suite green; full baseline preserved.** `uv run pytest tests/test_compliance_penalty.py tests/test_flagged.py tests/test_pages_x402.py tests/test_score_api.py tests/test_pages.py tests/test_agent_score.py` reports `0 failed`. Full suite: `285 passed`, `8 skipped` (Postgres-only), `0 failed`. No pre-existing passing test regresses.
10. **AC-10 — Admin endpoints use the same `X-API-Key` auth as `POST /api/sync/flagged`.** Missing header → `401`. Wrong key → `401`. Unconfigured key (`settings.API_KEY` unset / None / empty) → `503`. Valid key on `POST /api/admin/compliance/refresh` → `200` with `ComplianceRefreshReport` JSON. Valid key on `GET /api/admin/compliance/status` → `200` with `{ last_refreshed_at, flagged_data_stale, row_count }`.

---

## Requirements

### Requirement: Storage — `compliance_penalty` column with non-negative floor

A new Alembic migration (`migrations/versions/0012_compliance_penalty.py`, head-of-chain on `0011_fix_onchain_null_array`) MUST add `agent_cache.compliance_penalty: Numeric(5, 2) NOT NULL DEFAULT 0` and a `CHECK (compliance_penalty >= 0)` constraint. The column shape matches the existing `activity_score` / `wallet_score` columns (`app/db/models/agent.py:194–199`). `activity_score` and `wallet_score` MUST NOT be altered by the migration. If a concurrent PR claims `0012_*`, this migration renumbers to `0013_*` and points `down_revision` at the new head.

#### Scenario: column exists with the documented type and floor

- GIVEN the migration has been applied
- WHEN the `agent_cache` schema is introspected
- THEN `compliance_penalty` MUST be present with type `Numeric(5, 2) NOT NULL DEFAULT 0`
- AND the CHECK constraint `compliance_penalty >= 0` MUST be present in the introspection
- AND the columns `activity_score` and `wallet_score` MUST be unchanged

#### Scenario: existing rows backfill to 0

- GIVEN an `agent_cache` row that exists before the migration with no `compliance_penalty` value
- WHEN the migration completes
- THEN that row's `compliance_penalty` MUST equal `Decimal("0.00")` on read

#### Scenario: negative penalty is rejected at the DB layer

- GIVEN an `agent_cache` row with `compliance_penalty=0`
- WHEN a `UPDATE agent_cache SET compliance_penalty = -1 WHERE ...` is executed
- THEN the database MUST raise an integrity error (CHECK violation)
- AND the row's prior value MUST remain intact

---

### Requirement: Storage — per-agent flag table `agent_compliance_flags`

The migration MUST create a new table `agent_compliance_flags` keyed by `agent_id` (one row per agent). The table records which addresses triggered the flag (with JSONB source lists), whether the creator and owner resolve to the same EVM address, a derived stale-data flag, and the timestamp of the most recent refresh that touched this row.

| Column | Type | Nullable | Default |
| --- | --- | --- | --- |
| `agent_id` | `String(255)` | NOT NULL | — (PRIMARY KEY) |
| `creator_flagged` | `Boolean` | NOT NULL | `false` |
| `creator_flag_sources` | `JSONB` | NOT NULL | `'[]'::jsonb` |
| `owner_flagged` | `Boolean` | NOT NULL | `false` |
| `owner_flag_sources` | `JSONB` | NOT NULL | `'[]'::jsonb` |
| `creator_is_owner` | `Boolean` | NOT NULL | `false` |
| `flagged_data_stale` | `Boolean` | NOT NULL | `false` |
| `refreshed_at` | `timestamptz` | NOT NULL | — |

#### Scenario: row exists for an agent after refresh

- GIVEN an `agent_cache` row at `(chain_id=56, token_id=1)` with `creator_address="0xabc…"` and `owner_address="0xdef…"`
- AND a `flagged_addresses` row with `address="0xabc…"` under `source="ofac-bsc"`
- WHEN `refresh_agent_compliance_flags(session)` runs
- THEN exactly one `agent_compliance_flags` row exists for that agent's `agent_id`
- AND `creator_flagged` MUST be `true`
- AND `creator_flag_sources` MUST contain `"ofac-bsc"` (as a JSON list element)
- AND `owner_flagged` MUST be `false`
- AND `owner_flag_sources` MUST be `[]`
- AND `creator_is_owner` MUST be `false`
- AND `refreshed_at` MUST be within the last few seconds (set on every upsert)

#### Scenario: creator and owner are the same address

- GIVEN an `agent_cache` row where `creator_address == owner_address == "0xabc…"` under lowercased comparison
- AND `flagged_addresses` contains `"0xabc…"`
- WHEN `refresh_agent_compliance_flags(session)` runs
- THEN `agent_compliance_flags.creator_is_owner` MUST be `true`
- AND `creator_flagged` and `owner_flagged` MUST both be `true`
- AND `agent_cache.compliance_penalty` MUST equal `Decimal("50.00")` (cap from `(True, True)`)

#### Scenario: clean agent produces a row with all flags false

- GIVEN an `agent_cache` row whose lowercased `creator_address` and lowercased `owner_address` are NOT present in `flagged_addresses`
- WHEN `refresh_agent_compliance_flags(session)` runs
- THEN the upsert writes `creator_flagged=false`, `creator_flag_sources=[]`, `owner_flagged=false`, `owner_flag_sources=[]`, `creator_is_owner=false`, `compliance_penalty=Decimal("0.00")` for that row

---

### Requirement: Penalty math — pure helper `compute_penalty`

The function `compute_penalty(creator_flagged: bool, owner_flagged: bool) -> Decimal` MUST live in `app/services/compliance_refresh.py` as a pure helper (no I/O, no module-level mutation, no logging). The formula is `min(30 * int(creator_flagged) + 30 * int(owner_flagged), 50)`. The result MUST be a `Decimal` at the boundary to match the existing `Numeric(5, 2)` column.

#### Scenario: zero, single, and double flag cases

- GIVEN the helper is invoked with the four boolean combinations
- WHEN each pair `(creator_flagged, owner_flagged)` is `(False, False)`, `(True, False)`, `(False, True)`, `(True, True)`
- THEN the returned values MUST be `Decimal("0.00")`, `Decimal("30.00")`, `Decimal("30.00")`, `Decimal("50.00")` respectively
- AND assertions MUST use exact `Decimal` equality (`==`), not `pytest.approx`

#### Scenario: cap holds exactly at 50

- GIVEN `creator_flagged=True` and `owner_flagged=True`
- WHEN the helper is invoked
- THEN the returned value MUST equal `Decimal("50.00")` exactly (not `60.00`)
- AND a separate test MUST assert that `30 * 2 == 60` is a value the helper never returns (negative assertion on the helper's output space)

#### Scenario: helper is deterministic and side-effect-free

- GIVEN the same inputs
- WHEN the helper is invoked twice
- THEN both invocations MUST return equal `Decimal` values
- AND the helper MUST NOT read or write any module-level state, must NOT perform I/O, and must NOT log

---

### Requirement: Refresh orchestrator — `run_compliance_refresh()` chains both phases

The orchestrator MUST invoke `refresh_flagged_addresses()` first (the existing function in `app/services/flagged_sync.py`) and then `refresh_agent_compliance_flags(session)`. If the first phase raises, the second phase MUST NOT run and the original exception propagates. The orchestrator returns a combined `ComplianceRefreshReport` carrying both phases' counts plus the timestamp of the most recent successful mirror update.

#### Scenario: both phases succeed and the orchestrator chains them

- GIVEN a working DB and a reachable 0xB10C mirror (or stubbed mirror in tests)
- WHEN `run_compliance_refresh()` runs end-to-end
- THEN `refresh_flagged_addresses()` MUST be invoked exactly once
- AND `refresh_agent_compliance_flags(session)` MUST be invoked exactly once, **after** the mirror phase (ordering enforced by a `mock.call_args_list` assertion or equivalent in tests)
- AND the returned report MUST carry both phases' counts

#### Scenario: mirror phase failure short-circuits

- GIVEN `refresh_flagged_addresses()` raises (e.g. mirror HTTP 500, timeout, parse error)
- WHEN `run_compliance_refresh()` runs
- THEN `refresh_agent_compliance_flags(session)` MUST NOT be invoked
- AND the original exception MUST propagate to the caller (no swallowed `Exception` blocks)
- AND the agent-flag state in the DB MUST remain unchanged

#### Scenario: refresh is idempotent on a stable mirror

- GIVEN `run_compliance_refresh()` has run once successfully and no mirror or agent row has changed
- WHEN it runs a second time
- THEN `agent_compliance_flags` row contents MUST be unchanged (byte-equivalent JSON for the JSONB columns after a normalization step that respects key order)
- AND the count of rows whose `compliance_penalty` actually changed MUST be zero on the second run

---

### Requirement: Case-insensitive exact match against `flagged_addresses.address`

For every `agent_cache` row, the comparison between `creator_address` / `owner_address` and `flagged_addresses.address` MUST be exact equality after both sides are lowercased. Heuristic, prefix, suffix, or substring matching is forbidden. The match comparison lowercases both operands at compare time; `flagged_addresses.address` is stored as it arrived from the mirror and is never mutated by this change.

#### Scenario: mixed-case agent matches lowercase mirror row

- GIVEN an `agent_cache` row with `creator_address="0xAbC1234DEF…"` (mixed case)
- AND a `flagged_addresses` row with `address="0xabc1234def…"` (lowercase)
- WHEN `refresh_agent_compliance_flags(session)` processes that row
- THEN `agent_compliance_flags.creator_flagged` MUST be `true`
- AND `creator_flag_sources` MUST include the source(s) whose mirror row contains the lowercased address

#### Scenario: one-char difference does not promote to a match

- GIVEN an `agent_cache.creator_address="0xabc…"` and a `flagged_addresses.address="0xabd…"` (a single character different)
- WHEN the match runs
- THEN `creator_flagged` MUST be `false`
- AND no prefix-of, suffix-of, or substring interpretation MAY promote this to `true`

---

### Requirement: Hire-CTA gate — server-rendered `disabled` when both flags are true

`app/routers/pages.py::agent_detail(request, chain_id, token_id)` MUST read the agent's `agent_compliance_flags` row and pass `creator_flagged`, `owner_flagged`, and `compliance_penalty` into the template alongside the existing `flagged_addresses` set. `app/templates/pages/agent_detail.html` MUST render:

- An OFAC-blocking banner (above or near the Activity score card) labelled "OFAC: hiring blocked" with reason copy — `"Hiring is disabled while OFAC compliance is unresolved for this agent."` — when `creator_flagged AND owner_flagged` are both true.
- A visible warning copy when only one of `{creator_flagged, owner_flagged}` is true. The warning is informational — banner copy differs from the block-banner copy.
- `#hire-cta` with the `disabled` attribute AND `aria-disabled="true"` ONLY when both flags are true.

The client-side handler in `app/static/js/payment.js` is unchanged: it already short-circuits on `cta.disabled`. No new JS file is introduced. `agent-detail.mjs` does not exist in this repo per explore §6 — do not create it.

#### Scenario: dual-flag agent renders disabled CTA + block banner

- GIVEN an `agent_cache` row whose `agent_compliance_flags` has `creator_flagged=true` AND `owner_flagged=true`
- WHEN `GET /agents/{chain}/{token}` is rendered
- THEN the response body MUST contain `id="hire-cta"` with `disabled` present on the same element
- AND the body MUST contain `aria-disabled="true"` on the same element
- AND the body MUST contain the OFAC-blocking banner string `Hiring is disabled while OFAC compliance is unresolved for this agent`
- AND the body MUST contain a `Compliance: -50 pts` (or equivalent) badge on the Activity score card

#### Scenario: single-flag agent renders warning copy only

- GIVEN an `agent_cache` row whose `agent_compliance_flags` has `creator_flagged=true` and `owner_flagged=false`
- WHEN `GET /agents/{chain}/{token}` is rendered
- THEN the response body MUST contain a warning copy (e.g. `OFAC warning: creator address flagged`) but MUST NOT contain the OFAC-blocking banner string
- AND `#hire-cta` MUST NOT have the `disabled` attribute
- AND `aria-disabled="true"` MUST NOT be set on `#hire-cta`

#### Scenario: no-flag agent renders neither banner nor disabled CTA

- GIVEN an `agent_cache` row whose `agent_compliance_flags` has `creator_flagged=false` AND `owner_flagged=false`
- WHEN `GET /agents/{chain}/{token}` is rendered
- THEN the response body MUST NOT contain the OFAC-blocking banner string
- AND MUST NOT contain the single-flag warning string
- AND `#hire-cta` MUST NOT have the `disabled` attribute
- AND the Activity score card MUST NOT contain a `Compliance: -N pts` badge

---

### Requirement: Displayed score formula on detail page and `/score` endpoint

The Activity score card on the detail page MUST render `displayed_activity_score = max(0, activity_score - compliance_penalty)`. The `GET /api/agents/{chain}/{token}/score` response MUST include two new additive fields on `ScoreOut`:

- `compliance_penalty: float` (the raw stored penalty, in `0.00..50.00`).
- `displayed_activity_score: float` (= `max(0, activity_score - compliance_penalty)`).

The existing `activity_score` field on `ScoreOut` is **not** mutated: its value stays canonical (whatever `materialize_score()` last wrote). The formula is computed at read time.

#### Scenario: penalty smaller than activity — straightforward subtraction

- GIVEN an agent with `activity_score=Decimal("72.50")` and `compliance_penalty=Decimal("30.00")`
- WHEN the detail page renders and `GET /api/agents/{chain}/{token}/score` is called
- THEN both surfaces MUST expose `displayed_activity_score = 42.50`
- AND the JSON response's `activity_score` MUST still equal `72.50`
- AND the JSON response's `compliance_penalty` MUST equal `30.00`

#### Scenario: penalty exceeds activity — clipped to zero

- GIVEN an agent with `activity_score=Decimal("20.00")` and `compliance_penalty=Decimal("30.00")` (penalty exceeds activity)
- WHEN the detail page renders and `GET /api/agents/{chain}/{token}/score` is called
- THEN both surfaces MUST expose `displayed_activity_score = 0.00`
- AND the displayed value MUST NOT be negative

#### Scenario: clean agent — displayed equals stored

- GIVEN an agent with `compliance_penalty=Decimal("0.00")`
- WHEN the detail page renders and `GET /api/agents/{chain}/{token}/score` is called
- THEN `displayed_activity_score` MUST equal `activity_score` exactly

---

### Requirement: Stale-data flag derived from `flagged_addresses.updated_at`

The function `flagged_data_stale(session, threshold_hours: int = 24) -> bool` MUST return `True` iff `(now() - max(flagged_addresses.updated_at)) > threshold_hours`. No migration on `flagged_addresses` is needed (`updated_at` is already set on every `_replace_source` write — see `app/services/flagged_sync.py:91–101`). The boolean is also written into `agent_compliance_flags.flagged_data_stale` on every refresh.

#### Scenario: fresh mirror is not stale

- GIVEN `flagged_addresses` has at least one row with `updated_at` within the last 24h (or `now()`)
- WHEN `flagged_data_stale(session)` is called with the default 24h threshold
- THEN the function MUST return `False`
- AND `agent_compliance_flags.flagged_data_stale` for refreshed agents MUST be `false`

#### Scenario: backdated mirror becomes stale

- GIVEN every `flagged_addresses` row has `updated_at` backdated to >24h ago
- WHEN `flagged_data_stale(session)` is called
- THEN the function MUST return `True`
- AND `agent_compliance_flags.flagged_data_stale` MUST be `true` after the next refresh

#### Scenario: empty mirror counts as stale

- GIVEN `flagged_addresses` has zero rows
- WHEN `flagged_data_stale(session)` is called
- THEN the function MUST return `True`
- AND the status endpoint MUST surface `flagged_data_stale: true` and `row_count: 0`

---

### Requirement: Admin endpoints guarded by `X-API-Key`

`POST /api/admin/compliance/refresh` and `GET /api/admin/compliance/status` MUST use the same `X-API-Key` header check that `POST /api/sync/flagged` already uses (see `app/routers/sync.py`). The auth contract is:

- Missing header → `401`
- Wrong key → `401`
- `settings.API_KEY` unset / None / empty → `503`
- Valid key → `200` with JSON

Both endpoints are in `app/routers/admin.py` (new module).

#### Scenario: missing X-API-Key is rejected

- GIVEN `settings.API_KEY` is configured
- AND `POST /api/admin/compliance/refresh` is called without an `X-API-Key` header
- WHEN the request reaches the route handler
- THEN the response MUST have status `401`
- AND `run_compliance_refresh()` MUST NOT be invoked

#### Scenario: wrong X-API-Key is rejected

- GIVEN `settings.API_KEY="correct"`
- AND the request sets `X-API-Key: "wrong"`
- WHEN `POST /api/admin/compliance/refresh` is called
- THEN the response MUST have status `401`
- AND `run_compliance_refresh()` MUST NOT be invoked

#### Scenario: unconfigured key returns 503

- GIVEN `settings.API_KEY` is unset / None / empty
- WHEN either admin endpoint is called with any `X-API-Key` header
- THEN the response MUST have status `503`, matching the existing `/api/sync/flagged` 503 contract for unconfigured auth

#### Scenario: valid key runs the orchestrator

- GIVEN `settings.API_KEY="valid"`
- AND the request sets `X-API-Key: "valid"`
- WHEN `POST /api/admin/compliance/refresh` is called
- THEN the response MUST have status `200`
- AND the body MUST be the `ComplianceRefreshReport` JSON per AC-3
- AND the response Content-Type MUST be `application/json`

#### Scenario: status endpoint surfaces mirror freshness

- GIVEN a valid `X-API-Key`
- WHEN `GET /api/admin/compliance/status` is called
- THEN the response MUST have status `200`
- AND the JSON body MUST include `last_refreshed_at` (ISO-8601 UTC string or `null`), `flagged_data_stale` (bool), and `row_count` (int — the count of `agent_compliance_flags` rows)

---

### Requirement: Score endpoint contract — additive fields on `ScoreOut`

`app/schemas/score.py` MUST be extended with two additive fields on `ScoreOut`: `compliance_penalty: float` and `displayed_activity_score: float`. Existing fields (`activity_score`, `breakdown`, pillar sub-scores, etc.) MUST NOT change. The schema growth is additive and does not break clients that ignore unknown fields.

#### Scenario: /score returns the new additive fields

- GIVEN an `agent_cache` row with `activity_score=Decimal("80.00")` and `compliance_penalty=Decimal("30.00")`
- WHEN `GET /api/agents/{chain}/{token}/score` is called
- THEN the JSON body MUST include `compliance_penalty: 30.0`
- AND MUST include `displayed_activity_score: 50.0`
- AND MUST still include `activity_score: 80.0` (unchanged from pre-change behavior)

#### Scenario: existing JSON clients ignoring unknown fields still work

- GIVEN a JSON client that only reads the `activity_score` key from the response
- WHEN `/score` is called after this change is applied
- THEN the client's view of the response MUST be unchanged (the new fields are additive and the existing serializer MUST accept the existing field set without modification)
- AND no deserialization error MUST be raised on the client

---

### Requirement: Strict TDD discipline at apply time

The apply phase MUST follow RED → GREEN → TRIANGULATE → REFACTOR using `uv run pytest`. Each pure helper (e.g. `compute_penalty`) MUST have failing tests written first. Each DB-backed behaviour MUST use the fixture style consistent with the existing `tests/` suite (sqlite-compatible; the existing Postgres-only 8 skipped tests are unchanged). Targeted pytest invocation MUST be used during development; the full suite is run only at the end of the apply phase.

#### Scenario: RED → GREEN for `compute_penalty`

- GIVEN the migration has not run yet and `compute_penalty` does not exist in `app/services/compliance_refresh.py`
- WHEN `tests/test_compliance_penalty.py` is written first and `uv run pytest tests/test_compliance_penalty.py` is invoked
- THEN the test MUST fail with `ImportError` or `NameError` on the missing symbol (RED)
- AND only after the helper is implemented MUST the test pass (GREEN)

#### Scenario: full baseline preserved at the end of apply

- GIVEN the change has been fully applied and all targeted suites are green
- WHEN `uv run pytest` is run from the repo root
- THEN the final summary MUST report `285 passed`, `8 skipped`, `0 failed`
- AND the 8 skipped MUST be the pre-existing Postgres-only tests, unchanged in count and names
- AND no previously-passing test in the pre-change baseline may regress

---

## Risks

- **R-1 (medium):** If `POST /api/admin/compliance/refresh` is not called, the compliance state grows stale. Mitigated by `flagged_data_stale` on the status endpoint and the `row_count` field; the operator (or an external cron per DESIGN.md D8) is responsible for triggering the refresh on a cadence. Long-term scheduler is Phase 2+.
- **R-2 (low):** Phase 2 (`wallet-activity-pillar`) extends penalty to `agent_wallet`. Because `activity_score` stays immutable here and `compliance_penalty` lives in its own column, the two changes can land in either order without stomping each other.
- **R-3 (low):** Migration filename `0012_compliance_penalty.py` is the next slot after `0011_fix_onchain_null_array`. A concurrent PR claiming `0012_*` forces renumbering to `0013_*`; the apply phase re-checks `migrations/versions/` immediately before branching.
- **R-4 (low):** `ScoreOut` grows by two additive fields. Snapshot-style consumers (`tests/test_score_api.py`) need the new fields accounted for; existing JSON clients that ignore unknown fields are unaffected.
- **R-5 (low):** `tests/test_pages_x402.py` pins `#hire-cta` HTML; adding `disabled` as a new possible state requires new cases. The existing `test_cta_disabled_without_wallet` continues to pass unchanged (wallet-absent is orthogonal to OFAC). The new compliance-disabled cases are additive.
- **R-6 (info):** Spec written flat (`openspec/changes/compliance-flags/spec.md`); archive treats it as a full new domain spec and copies it to `openspec/specs/agent-compliance/spec.md`.

---

## References

- Proposal / explore: `openspec/changes/compliance-flags/{proposal.md,explore.md}`
- Format precedent: `openspec/specs/agent-detail-ui/spec.md` (archived as `openspec/changes/archive/2026-09-15-test-copy-fix/spec.md`)
- Existing surfaces: `app/services/flagged_sync.py` (mirror), `app/services/agent_score.py` (`materialize_score`, untouched), `app/routers/pages.py` (detail page + `_flagged_addresses_set`), `app/routers/sync.py` (`X-API-Key` pattern), `app/routers/agents.py` (`/score`), `app/db/models/agent.py:194–199`, `app/schemas/score.py` (`ScoreOut`), `app/static/js/payment.js` (unchanged)
- Existing tests: `tests/test_flagged.py`, `tests/test_pages.py`, `tests/test_pages_x402.py`, `tests/test_agent_score.py`, `tests/test_score_api.py`
- Migrations: `0007_flagged_addresses.py` (no change), `0011_fix_onchain_null_array.py` (current head — next slot is `0012_*`)
- Design + chain context: `DESIGN.md` D8, Phase 2 `wallet-activity-pillar`, Phase 3 `agent-score-integration`
