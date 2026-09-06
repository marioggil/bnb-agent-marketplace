# Tasks — Phase 1a-ii (service + admin router)

**Sub-PR:** `1a-ii`
**Branch:** `feat/compliance-flags-db-service`
**Base:** `main` (post-1a-i merge)
**Stack slot:** 2nd PR
**Depends on:** `1a-i` merged

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~382 |
| 400-line budget risk | Medium (4% headroom — TIGHT) |
| Chained PRs recommended | Yes (this is 2 of 3) |
| Suggested split | n/a (already split; §6.R-1 mitigation documented if apply-time TDD pushes over 380) |
| Delivery strategy | ask-on-risk |
| Chain strategy | stacked-to-main |

```text
Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: Medium
```

> **Operator check at end of T11:** if running `git diff --stat` shows >380 net lines, apply §6.R-1 mitigation — split `tests/test_admin_compliance.py` into auth-only (≤200 lines) + a separate `test_admin_compliance_refresh.py` for body-shape assertions (≤200 lines). Merge target stays the same branch.

## Dependency statement

This sub-PR **requires 1a-i merged to `main`** because `refresh_agent_compliance_flags()` writes `agent_cache.compliance_penalty` (column added by 1a-i) and UPSERTs `agent_compliance_flags` (table added by 1a-i). The pre-seeding fixture `_ensure_compliance_schema` (autouse) re-creates both via raw SQL with `IF NOT EXISTS` / try-except so the test suite is green even on a branch that rebased on a pre-merge fork — but production deploy still requires 1a-i shipped first.

## Task ordering rationale

Service functions before route handlers so the route tests target the real implementation not a mock-of-a-mock. T1–T3 ship `refresh_agent_compliance_flags` with its core happy-path + idempotence proof; T4–T5 add `flagged_data_stale` (independent helper); T6–T7 add the orchestrator that chains them (depends on both); T8–T13 add the admin router that exposes them; T14 mounts; T15 verifies. This is dependency-first order.

---

## Tasks

- [x] **T1 — RED: `test_refresh_writes_compliance_penalty_for_creator_match`.** In `tests/test_compliance_refresh.py` import `from app.services.compliance_refresh import refresh_agent_compliance_flags`. Seed: one `flagged_addresses` row at `("0xabc...", "ofac-bsc")`; one `agent_cache` row with `creator_address="0xabc..."` and `owner_address="0xdef..."`. Call `await refresh_agent_compliance_flags(db_session)` with the `_ensure_compliance_schema` autouse fixture from design §3.5. Assert the row in `agent_compliance_flags` has `creator_flagged=true`, `owner_flagged=false`, `creator_is_owner=false`; assert `agent_cache.compliance_penalty == Decimal("30.00")`. Run `uv run pytest tests/test_compliance_refresh.py::test_refresh_writes_compliance_penalty_for_creator_match -x`. Expected: `ImportError` on `refresh_agent_compliance_flags` (1a-i shipped only `compute_penalty`). Capture failure line — RED evidence. <!-- sdd-owner: implementation -->

- [x] **T2 — GREEN: implement `refresh_agent_compliance_flags(session)`.** Extend `app/services/compliance_refresh.py` (preserve 1a-i's `compute_penalty` + `ComplianceRefreshReport` byte-identical). New function reads `flagged_addresses` into `address_sources: dict[str, list[str]]` keyed by lowercased address; iterates every `AgentCache` row; computes per agent `creator_flagged` / `owner_flagged` / `creator_flag_sources` / `owner_flag_sources` / `creator_is_owner` / `compute_penalty(...)`; UPSERTs `agent_compliance_flags` (Postgres `pg_insert(...).on_conflict_do_update`, sqlite per-row delete-then-insert); then issues a bulk `UPDATE agent_cache SET compliance_penalty = :p WHERE agent_id IN :ids`. Returns `ComplianceRefreshReport(agents_matched=N, agents_upserted=N, last_refreshed_at=max(flagged_addresses.updated_at).isoformat() if any else None)`. Run `uv run pytest tests/test_compliance_refresh.py -k creator_match`. Expected: passes. <!-- sdd-owner: implementation -->

- [x] **T3 — RED+GREEN: `test_refresh_idempotent_second_run_yields_same_payload`.** Add to `tests/test_compliance_refresh.py`. Run `refresh_agent_compliance_flags(db)` twice on a stable fixture; assert `import json; json.dumps(before_creator_flag_sources, sort_keys=True) == json.dumps(after_creator_flag_sources, sort_keys=True)` for every row (DB-agnostic per design §5.3); assert the count of `agent_cache` rows where `compliance_penalty` actually changed between runs is `0` (dialect-agnostic SQL count). Expected RED: either `before == after` is trivially true on the first run (no prior state) — the test only meaningfully fails if a subsequent refresh mutates payload; second invocation must be stable. GREEN: passes once `refresh_agent_compliance_flags` is fully deterministic. Run `uv run pytest tests/test_compliance_refresh.py -k idempotent`. <!-- sdd-owner: implementation -->

- [x] **T4 — RED: `test_flagged_data_stale_true_after_24h` + `test_flagged_data_stale_fresh_mirror_false`.** Add two cases to `tests/test_compliance_refresh.py`:
  - Backdate every `flagged_addresses.updated_at` to `now - 25h`; assert `flagged_data_stale(session, threshold_hours=24) is True`.
  - Insert one row with `updated_at=now()`; assert `flagged_data_stale(session, threshold_hours=24) is False`.
  Also add `test_flagged_data_stale_empty_mirror` (zero rows → `True`).
  Run `uv run pytest tests/test_compliance_refresh.py -k flagged_data_stale -x`. Expected: `ImportError` on `flagged_data_stale`. <!-- sdd-owner: implementation -->

- [x] **T5 — GREEN: implement `flagged_data_stale(session, threshold_hours=_STALE_THRESHOLD_HOURS) -> bool`.** Extend `app/services/compliance_refresh.py`. Single SQL: `SELECT max(updated_at) FROM flagged_addresses`. If `None` (empty mirror) → `True`. Else → `(datetime.now(UTC) - max_ts).total_seconds() > threshold_hours * 3600`. Run `uv run pytest tests/test_compliance_refresh.py -k flagged_data_stale`. Expected: passes. <!-- sdd-owner: implementation -->

- [x] **T6 — RED: `test_run_compliance_refresh_chains_both_phases` + short-circuit + combined-report.** In `tests/test_compliance_refresh.py` (or a new `test_orchestrator.py` if imports get long):
  - Mock `refresh_flagged_addresses` and `refresh_agent_compliance_flags` with `unittest.mock.patch` (or via the existing pattern in `tests/test_flagged.py`). Call `await run_compliance_refresh()`. Assert `mock.call_args_list` shows mirror invoked first, agent flags second, each exactly once.
  - `test_run_compliance_refresh_mirror_failure_short_circuits`: make `refresh_flagged_addresses` raise `RuntimeError`; assert `refresh_agent_compliance_flags` was NOT invoked and the same `RuntimeError` propagates.
  - `test_run_compliance_refresh_returns_combined_report`: both phases succeed; report carries mirror totals + agent counts + `last_refreshed_at`.
  Run `uv run pytest tests/test_compliance_refresh.py -k run_compliance_refresh -x`. Expected: `ImportError` on `run_compliance_refresh`. <!-- sdd-owner: implementation -->

- [x] **T7 — GREEN: implement `run_compliance_refresh()`.** Extend `app/services/compliance_refresh.py`. Async function; calls `await refresh_flagged_addresses()` first; awaits the result; then `async with AsyncSessionLocal() as session: await refresh_agent_compliance_flags(session)` second. If phase 1 raises, do NOT enter phase 2 (no `try/except` swallowing — let the original exception propagate). Copies `sources_total_fetched` + `sources_total_inserted` from the mirror report into the combined `ComplianceRefreshReport`. Set `last_refreshed_at` from the agent-flag phase return. Also add `async status_summary(session) -> tuple[datetime|None, int]` (returns `(max(flagged_addresses.updated_at), count(agent_compliance_flags))`). Run `uv run pytest tests/test_compliance_refresh.py -k run_compliance_refresh or status_summary`. Expected: passes. <!-- sdd-owner: implementation -->

- [x] **T8 — RED: `test_admin_compliance_refresh_requires_api_key`.** Create `tests/test_admin_compliance.py`. Auth parity with `tests/test_flagged.py::test_sync_flagged_*` (reuses `require_sync_key` from `app/routers/sync.py`). Cases: missing `X-API-Key` → `401`; wrong key → `401`; unconfigured (`settings.API_KEY=None`) → `503`. No invocation of orchestrator on any rejection case (mock `run_compliance_refresh`). Run `uv run pytest tests/test_admin_compliance.py -x`. Expected: `404` on the route (router not registered yet). <!-- sdd-owner: implementation -->

- [x] **T9 — GREEN: scaffold `app/routers/admin.py` (auth-only, no body yet).** Create `app/routers/admin.py` with `router = APIRouter(prefix="/api/admin/compliance", tags=["admin-compliance"])`. Import `require_sync_key` from `app.routers.sync`. Define a stub `POST /refresh` and `GET /status` with `dependencies=[Depends(require_sync_key)]` but body raises `NotImplementedError` for now. Re-run T8 tests — expect `401`/`401`/`503` instead of `404`. <!-- sdd-owner: implementation -->

- [x] **T10 — RED: `test_refresh_success_returns_200_and_calls_orchestrator`.** In `tests/test_admin_compliance.py` mock `compliance_refresh.run_compliance_refresh` to return a fixed `ComplianceRefreshReport(...)`. Call `POST /api/admin/compliance/refresh` with valid `X-API-Key`. Assert `200`, body equals `report.as_dict()`, `Content-Type: application/json`, mock invoked exactly once. Run `uv run pytest tests/test_admin_compliance.py -k success_returns_200`. Expected: `500` (route body still raises `NotImplementedError`). <!-- sdd-owner: implementation -->

- [x] **T11 — GREEN: implement `POST /api/admin/compliance/refresh`.** Modify `app/routers/admin.py`. Open `async with AsyncSessionLocal() as session`, call `report = await compliance_refresh.run_compliance_refresh()`, return `report.as_dict()`. Run `uv run pytest tests/test_admin_compliance.py`. Expected: passes for the 4 cases so far. **Operator check at this point:** run `git diff --stat` — if the cumulative diff (extension + router + 2 test files) exceeds 380 lines, apply §6.R-1 mitigation before T12. <!-- sdd-owner: implementation -->

- [x] **T12 — RED: `test_status_returns_last_refreshed_at_and_stale_flag`.** In `tests/test_admin_compliance.py`. Seed one `agent_compliance_flags` row + one fresh `flagged_addresses` row; assert `GET /api/admin/compliance/status` returns `{last_refreshed_at: ISO-8601 or null, flagged_data_stale: bool, row_count: int}`. Run `uv run pytest tests/test_admin_compliance.py -k status`. Expected: `501` or `NotImplementedError` (route body not implemented). <!-- sdd-owner: implementation -->

- [x] **T13 — GREEN: implement `GET /api/admin/compliance/status`.** Modify `app/routers/admin.py`. Open session; `last_ts, row_count = await compliance_refresh.status_summary(session)`; `stale = await compliance_refresh.flagged_data_stale(session)`; return `{"last_refreshed_at": last_ts.isoformat() if last_ts else None, "flagged_data_stale": stale, "row_count": row_count}`. Run `uv run pytest tests/test_admin_compliance.py`. Expected: all admin cases pass. <!-- sdd-owner: implementation -->

- [x] **T14 — Mount admin router in `app/main.py`.** Add `from app.routers.admin import router as admin_router` and `app.include_router(admin_router)` inside `create_app()`, immediately after the existing `sync.router` mount. Re-run `uv run pytest tests/test_admin_compliance.py`. Expected: still passes; the route is now reachable at `/api/admin/compliance/{refresh,status}`. Files touched: `app/main.py` (~2 lines). <!-- sdd-owner: implementation -->

- [x] **T15 — Final verification.** Run:
  1. `uv run pytest tests/test_compliance_penalty.py tests/test_compliance_models.py tests/test_compliance_refresh.py tests/test_admin_compliance.py -v` — all green (1a-i tests untouched; 1a-ii tests all green).
  2. `uv run pytest` (full suite) — baseline `285 passed, 8 skipped` preserved + new tests pass.
  3. `git diff -- migrations/ app/db/models/` → empty (1a-i owns these).
  4. `git diff -- app/templates/ app/routers/{pages,agents}.py app/schemas/` → empty (1b's territory).
  5. `git diff -- app/services/flagged_sync.py app/services/agent_score.py app/static/` → empty.
  6. Cumulative diff stat per file: target ≤ 400 net lines per file (design §6.R-1 trigger). <!-- sdd-owner: implementation -->

---

## REFACTOR notes

- `refresh_agent_compliance_flags` UPSERT branch should use SQLAlchemy dialect detection; keep both branches in source for clarity (Postgres `INSERT ... ON CONFLICT` + sqlite `DELETE then INSERT`).
- `status_summary` and `flagged_data_stale` both query `flagged_addresses.updated_at` — extract a private `_max_flagged_updated_at(session)` helper only if it deduplicates, otherwise leave inline for readability.
- `run_compliance_refresh` should NOT silently swallow phase-1 errors. The design §3.1 forbids `try/except Exception`. Test T6 enforces this.

## Evidence line (each task)

Each GREEN completion confirmed by the explicit `uv run pytest -k <marker>` invocation in the task body. RED first-failure captures the missing symbol. T11 GREEN also confirmed by `git diff --stat` ≤ 380 lines gate.

## Verification command (one-shot, run at end of 1a-ii)

```bash
uv run pytest tests/test_compliance_penalty.py tests/test_compliance_models.py tests/test_compliance_refresh.py tests/test_admin_compliance.py -v && \
uv run pytest && \
git diff --stat -- app/ migrations/ tests/ | tail -5
```
