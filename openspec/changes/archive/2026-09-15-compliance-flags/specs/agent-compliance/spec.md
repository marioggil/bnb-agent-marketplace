# Delta spec — `agent-compliance` — Phase 1a-ii

**Change:** `compliance-flags`
**Sub-phase:** 1a-ii (2nd of 3 PRs in the chained PR stack: 1a-i → 1a-ii → 1b)
**Branch:** `feat/compliance-flags-db-service`
**Base:** `main` post-1a-i merge
**Output path note:** This is the nested delta spec for the 1a-ii archive. It is **not** a full domain spec — the canonical `openspec/specs/agent-compliance/spec.md` already holds the full domain contract (created by the 1a-i archive, byte-identical to the umbrella change spec). This file declares only the **`## MODIFIED Requirements` deltas** that the 1a-ii archive applies to the canonical spec.

The deltas below do **not** rewrite requirement bodies — the umbrella spec text was authored to describe the post-chain target state, and the requirement bodies remain accurate. What changes at 1a-ii archive is **which requirements are now implemented and verified**, plus the **file/symbol anchors** that the canonical spec references for the 1a-ii-owned slice.

---

## MODIFIED Requirements

The following canonical requirement blocks are marked **implemented-and-verified at 1a-ii archive time**. The requirement body content is unchanged from the canonical — the modification is the explicit audit-trail annotation that links each requirement to its concrete 1a-ii implementation surface.

### Requirement: Storage — per-agent flag table `agent_compliance_flags`

**1a-ii implementation evidence:**

- `app/services/compliance_refresh.py` — `refresh_agent_compliance_flags(session)` UPSERTs `agent_compliance_flags` rows (Postgres `INSERT ... ON CONFLICT DO UPDATE` + sqlite delete-then-insert). Per-row payload: `creator_flagged`, `creator_flag_sources`, `owner_flagged`, `owner_flag_sources`, `creator_is_owner`, `flagged_data_stale`, `refreshed_at`.
- `tests/test_compliance_refresh.py` — 16 scenarios across the row-write contract (creator-only / owner-only / both-cap / clean / creator-is-owner / case-mix / idempotent second-run byte-equivalence / 3 stale / 3 orchestrator / 2 status_summary).
- Scenario R2-1 (row exists after refresh with correct fields): `test_refresh_writes_compliance_penalty_for_creator_match` + `_for_owner_match` + `_for_both_match` cover this.
- Scenario R2-2 (creator and owner are the same address): `test_refresh_creator_is_owner_derivation`.
- Scenario R2-3 (clean agent produces a row with all flags false): `test_refresh_clean_agent_writes_zero_penalty`.

### Requirement: Penalty math — pure helper `compute_penalty`

**1a-ii implementation evidence (carried forward from 1a-i, plus live DB-backed variants verified at 1a-ii):**

- 1a-i shipped the helper and the unit tests; 1a-ii adds four DB-backed truth-table variants (`test_compute_penalty_truth_table_via_refresh[creator-only]` / `_via_refresh[owner-only]` / `_via_refresh[both]` / `_via_refresh[clean]`) that exercise the helper against the sqlite-pre-seeded `agent_compliance_flags` row, proving the orchestrator writes the same Decimal values the helper returns.
- `_cap_negative_assertion` + `_deterministic` + `_returns_decimal_not_float` carried forward unchanged.

### Requirement: Refresh orchestrator — `run_compliance_refresh()` chains both phases

**1a-ii implementation evidence:**

- `app/services/compliance_refresh.py` — `async def run_compliance_refresh() -> ComplianceRefreshReport` calls `await refresh_flagged_addresses()` first, then `async with AsyncSessionLocal() as session: await refresh_agent_compliance_flags(session)`. No `try/except Exception` swallowing — phase-1 errors propagate and phase-2 is never entered.
- `tests/test_compliance_refresh.py`:
  - `test_run_compliance_refresh_chains_both_phases` — wraps both phases with `unittest.mock.patch`, asserts `mock.call_args_list` ordering (mirror first, agent flags second, each exactly once).
  - `test_run_compliance_refresh_mirror_failure_short_circuits` — mirror raises `RuntimeError`; `AsyncMock` asserts agent phase NOT called; the original `RuntimeError` propagates; DB state unchanged.
  - `test_run_compliance_refresh_returns_combined_report` — both phases succeed; report carries mirror totals + agent counts + `last_refreshed_at`.
- Idempotence invariant: `test_refresh_idempotent_second_run_yields_same_payload` — second `refresh_agent_compliance_flags(session)` invocation on a stable mirror yields byte-equivalent `agent_compliance_flags` row contents (verified via `json.dumps(sort_keys=True)`) AND zero rows with a changed `compliance_penalty`. DB-agnostic (works on sqlite and Postgres).
- `async def status_summary(session) -> tuple[datetime|None, int]` — returns `(max(flagged_addresses.updated_at), count(agent_compliance_flags))`. Consumed by `GET /api/admin/compliance/status`.

### Requirement: Case-insensitive exact match against `flagged_addresses.address`

**1a-ii implementation evidence:**

- `app/services/compliance_refresh.py` — both the mirror `SELECT` (line ~93) and the per-agent compare (lines ~117–118) lowercase both operands at compare time: `func.lower(FlaggedAddress.address).label("addr")` for the mirror side; `(agent.creator_address or "").strip().lower()` and `(agent.owner_address or "").strip().lower()` for the agent side. `flagged_addresses.address` is **never mutated** by 1a-ii; the existing row stays exactly as the mirror wrote it.
- `tests/test_compliance_refresh.py`:
  - `test_refresh_case_insensitive_match` — seeds `creator_address="0xAbC…"` (mixed case) and `flagged_addresses.address="0xabc…"` (lowercase); asserts `creator_flagged=true`; asserts the mirror row's stored address is unchanged (post-refresh readback).
  - `test_refresh_one_char_difference_does_not_match` — `creator_address="0xabc…"` vs `flagged_addresses.address="0xabd…"`; asserts `creator_flagged=false` and no prefix-of / suffix-of / substring interpretation fires.
- No `func.lower` mutation on the persisted row; verified by `grep -n "lower" app/services/compliance_refresh.py` showing the lowercase only at compare sites, not at write sites.

### Requirement: Stale-data flag derived from `flagged_addresses.updated_at`

**1a-ii implementation evidence:**

- `app/services/compliance_refresh.py` — `def flagged_data_stale(session, threshold_hours=_STALE_THRESHOLD_HOURS=24) -> bool`. Single SQL: `SELECT max(updated_at) FROM flagged_addresses`. Returns `True` if the mirror is empty (`max` is `None`) or `(datetime.now(UTC) - max_ts).total_seconds() > threshold_hours * 3600`.
- The boolean is **also written into `agent_compliance_flags.flagged_data_stale`** on every `refresh_agent_compliance_flags()` upsert, so the per-row state is consistent with the global mirror freshness check.
- `tests/test_compliance_refresh.py`:
  - `test_flagged_data_stale_fresh_mirror_returns_false` — `updated_at=now()` → `False`.
  - `test_flagged_data_stale_true_after_24h` — backdate to `now - 25h` → `True`.
  - `test_flagged_data_stale_empty_mirror_returns_true` — zero rows → `True`.
  - `test_flagged_data_stale_threshold_is_respected` — parametrized over `12h` / `24h` / `48h`; honours the threshold argument exactly.
- The status endpoint (`GET /api/admin/compliance/status`) surfaces `flagged_data_stale: bool` so operators can alert on staleness.

### Requirement: Admin endpoints guarded by `X-API-Key`

**1a-ii implementation evidence:**

- `app/routers/admin.py` (new, 61 lines) — `router = APIRouter(prefix="/api/admin/compliance", tags=["admin-compliance"])`. Imports `require_sync_key` from `app.routers.sync` (reusing the existing `X-API-Key` dependency that already guards `POST /api/sync/flagged`). Both endpoints declare `dependencies=[Depends(require_sync_key)]`.
- `POST /api/admin/compliance/refresh` — opens `async with AsyncSessionLocal() as session`, calls `await run_compliance_refresh()`, returns `report.as_dict()` as JSON with `Content-Type: application/json`.
- `GET /api/admin/compliance/status` — opens session, calls `last_ts, row_count = await compliance_refresh.status_summary(session)` and `stale = await compliance_refresh.flagged_data_stale(session)`, returns `{ last_refreshed_at: last_ts.isoformat() if last_ts else None, flagged_data_stale: stale, row_count: row_count }`.
- `app/main.py` (+2 lines) — `from app.routers.admin import router as admin_router` + `app.include_router(admin_router)` inside `create_app()`, immediately after the existing `sync.router` mount.
- `tests/test_admin_compliance.py` (new, 53 lines) — auth-only contract: missing `X-API-Key` → 401, wrong `X-API-Key` → 401, unconfigured `settings.API_KEY=None` → 503, on both endpoints. Verifies `run_compliance_refresh()` is NOT invoked on any rejection case (mock pattern).
- `tests/test_admin_compliance_refresh.py` (new, 105 lines) — happy path body-shape contract: `POST /refresh` returns 200 + JSON of the mock `ComplianceRefreshReport`; `GET /status` returns the expected `{last_refreshed_at, flagged_data_stale, row_count}` shape; empty mirror yields `row_count=0, flagged_data_stale=true, last_refreshed_at=null`.

### Requirement: Strict TDD discipline at apply time

**1a-ii implementation evidence (carried forward + extended):**

- `apply-progress.md` carries the per-task T1..T15 narrative with RED → GREEN → TRIANGULATE → REFACTOR evidence for every 1a-ii task. RED captured at T1 (`ImportError` on `refresh_agent_compliance_flags`), T6 (`ImportError` on `run_compliance_refresh`), T8 (`404` on the not-yet-mounted route), T12 (`NotImplementedError` on the status handler body).
- `_ensure_compliance_schema` autouse fixture in `tests/conftest.py` pre-seeds `agent_cache.compliance_penalty` + `agent_compliance_flags` on sqlite via raw SQL with `IF NOT EXISTS` + try-except per design §3.5. The fixture is the single point of drift risk between test DB and the production migration; the SQL matches `migrations/versions/0012_compliance_penalty.py` byte-for-byte.
- Full baseline preservation: pre-1a-ii `315 passed, 8 skipped, 0 failed` → post-1a-ii `340 passed, 8 skipped, 0 failed`. Δ = **+25 new passes, 0 regressions**. The 8 skipped are the same pre-existing Postgres-only tests from 1a-i; no new skips introduced.

---

## REMOVED Requirements

None. No canonical requirement is being dropped — the chain contract (R1–R11) remains intact for 1b to complete against.

---

## Out of scope for 1a-ii (deferred to 1b — NOT modified here)

The following canonical requirements are **not** touched by this delta and remain deferred to the 1b archive:

- **R6 — Hire-CTA gate** — server-rendered `disabled` on `#hire-cta` when both flags are true. Owned by 1b's `pages.py::agent_detail` + `agent_detail.html` template edits.
- **R7 — Displayed score formula on detail page and `/score` endpoint** — owned by 1b's `ScoreOut` additive fields + `get_agent_score` populate.
- **R10 — Score endpoint contract — additive fields on `ScoreOut`** — owned by 1b's `schemas/score.py` + `agents.py::get_agent_score` edits.

The 1a-ii work provides the **read-side primitives** these 1b requirements need (the populated `agent_compliance_flags` row, the computed `compliance_penalty` column, the `displayed_activity_score` formula inputs) but does not yet wire them into the page or `/score` endpoint. 1b will read those primitives.

---

## Destructive Merge Guard

Not triggered — only `MODIFIED` operations against existing canonical requirement blocks, and every modification is the **audit-trail annotation** described above. No `REMOVED` operations, no replaced blocks that would silently drop scenarios, no destructive canonical content changes.

---

## Active Same-Domain Change Warnings

None. The active umbrella `openspec/changes/compliance-flags/` directory remains in place for 1b to continue. The 1b sub-PR's own delta spec (when it lands) will be written to `openspec/changes/compliance-flags/specs/agent-compliance/spec.md` as a **follow-up delta** that merges R6 / R7 / R10 into the canonical. No collision risk on `agent-compliance` between this archive and the active 1b work because R6 / R7 / R10 are not touched by this delta.
