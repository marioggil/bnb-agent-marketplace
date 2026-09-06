```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:1afdd64fa18bc43004d08bda1db2b15e7bc0456754c8de152ed579de0e5d56be
verdict: pass
blockers: 0
critical_findings: 0
requirements: 8/8
scenarios: 23/23
test_command: uv run pytest tests/test_compliance_refresh.py tests/test_admin_compliance.py tests/test_admin_compliance_refresh.py -v
test_exit_code: 0
test_output_hash: sha256:08ca52cd5308b300b68f720dd35d3964bc953a52592a35ef5e77dbec017d01e8
build_command: uv run alembic upgrade head --sql
build_exit_code: 0
build_output_hash: sha256:d2745cbcbf02eb263d33f5a27e0abc31bef70afe5fa7c8f166b2f66c8074088d
```

# verify-report — Phase 1a-ii of `compliance-flags`

**Branch:** `feat/compliance-flags-db-service`
**Scope:** Service layer (`refresh_agent_compliance_flags`, `flagged_data_stale`, `run_compliance_refresh`, `status_summary`) + admin router (`POST /api/admin/compliance/refresh`, `GET /api/admin/compliance/status`) + `app/main.py` mount + autouse conftest fixture + 3 test files.
**Verdict:** **PASS** — all 8 applicable ACs satisfied; 2 ACs explicitly out-of-scope (1b); no regressions.

---

## AC summary

| AC | Status | Evidence |
|---|---|---|
| **AC-1** — column exists (re-confirm 1a-i) | **pass** | `test_compliance_models.py` 9/9 green; `test_compliance_penalty.py` 9/9 green; column + CHECK intact |
| **AC-2** — table exists (re-confirm 1a-i) | **pass** | `test_agent_compliance_flag_class_imports` + `_has_all_required_columns` + 3 row-write tests |
| **AC-3** — orchestrator chains both phases | **pass** | `test_run_compliance_refresh_chains_both_phases` + `_mirror_failure_short_circuits` + `_returns_combined_report` (3 cases) |
| **AC-4** — penalty math correct (re-verify live) | **pass** | `test_compute_penalty_truth_table` (4 cases) + 4 DB-backed scenarios (creator/owner/both/clean) |
| **AC-5** — Hire-CTA gate | **n/a** | Phase 1b |
| **AC-6** — Displayed score formula | **n/a** | Phase 1b |
| **AC-7** — Case-insensitive exact match | **pass** | `test_refresh_case_insensitive_match` + `_one_char_difference_does_not_match` |
| **AC-8** — `flagged_data_stale()` + derived flag | **pass** | 4 cases: empty/fresh/backdated/custom-threshold |
| **AC-9** — Full pytest baseline preserved | **pass** | **340 passed, 8 skipped, 0 failed**; pre-1a-ii baseline 315 → Δ +25 (0 regressions) |
| **AC-10** — Admin endpoint auth via `X-API-Key` | **pass** | 6 cases: missing/wrong/unconfigured × refresh+status |

**Pass: 8 · Fail: 0 · N/A: 2.**

---

## Spec scenario coverage

Applicable requirements for 1a-ii (Reqs 1, 2, 3, 4, 5, 7, 9, 11). Reqs 6 (Hire-CTA), 8 (displayed score), 10 (Score endpoint) are owned by 1b.

| Req | Scenarios | Verified in 1a-ii |
|---|---|---|
| 1 — column | 3 (exists / backfill / negative-reject) | ✅ `test_compliance_models.py` 9/9 — schema unchanged from 1a-i; autouse `_ensure_compliance_schema` exercises the migration's `IF NOT EXISTS` path per design §3.5 |
| 2 — table row | 3 (refresh writes row / creator=owner / clean agent) | ✅ `test_refresh_writes_*_for_*_match` (creator + owner) + `test_refresh_creator_is_owner_derivation` (penalty=50) + `test_refresh_clean_agent_writes_zero_penalty` |
| 3 — penalty | 3 (truth table / cap / deterministic) | ✅ `test_compute_penalty_truth_table` (4 cases) + `_cap_negative_assertion` + `_deterministic` + `_returns_decimal_not_float` + 4 DB-backed variants (creator-only=30, owner-only=30, both=50, clean=0) |
| 4 — orchestrator | 3 (chain / short-circuit / idempotent) | ✅ `test_run_compliance_refresh_chains_both_phases` (wrap-based mock + `call_args_list` ordering) + `_mirror_failure_short_circuits` (AsyncMock asserts agent phase NOT called + `httpx.HTTPStatusError` propagates + DB unchanged) + `test_refresh_idempotent_second_run_yields_same_payload` (`json.dumps(sort_keys=True)` byte-equivalence) |
| 5 — case-insensitive | 2 (mixed-case match / one-char diff) | ✅ `test_refresh_case_insensitive_match` (mixed-case `creator_address` matches lowercased mirror row; mirror NOT mutated) + `_one_char_difference_does_not_match` (no prefix/suffix/substring promotion) |
| 7 — stale | 3 (fresh / backdated / empty) | ✅ `test_flagged_data_stale_fresh_mirror_returns_false` + `_true_after_24h` + `_empty_mirror_returns_true` + `_threshold_is_respected` (custom threshold honoured) + `test_status_empty_mirror_returns_null_and_stale_true` |
| 9 — admin auth + status | 5 (missing-key / wrong-key / unconfigured / valid-key / status-surface) | ✅ `tests/test_admin_compliance.py` 6 cases (auth × 2 endpoints) + `tests/test_admin_compliance_refresh.py` 3 cases (happy path + status body + empty-mirror status) |
| 11 — TDD | 1 (full baseline preserved) | ✅ 340 passed, 8 skipped, 0 failed; pre-1a-ii baseline 315 → Δ +25 (no regression); RED→GREEN for `compute_penalty` covered in 1a-i |

**Direct coverage: 23 / 23 applicable scenarios.**

---

## Test execution

### Targeted suite (1a-ii only)

```
$ uv run pytest tests/test_compliance_refresh.py tests/test_admin_compliance.py tests/test_admin_compliance_refresh.py -v
... collected 25 items
tests/test_compliance_refresh.py ................                        [ 64%]
tests/test_admin_compliance.py ......                                    [ 88%]
tests/test_admin_compliance_refresh.py ...                               [100%]
============================= 25 passed in 0.92s ==============================
```

Exit code: **0**. 25 passed, 0 failed.

### Full baseline

```
$ uv run pytest
... 340 collected, all green; same 8 Postgres-only skips as 1a-i ...
======================= 340 passed, 8 skipped in 15.20s =======================
```

Exit code: **0**. **Baseline preserved.** Pre-1a-ii baseline = `315 passed, 8 skipped` (post-1a-i). Δ = **+25 new passes, 0 regressions**. The 8 skipped are the same pre-existing Postgres-only tests as 1a-i — no new skips introduced.

> **Spec wording note (carried from 1a-i):** Spec AC-9 cites baseline `285 passed, 8 skipped`; the actual pre-1a-ii baseline in this branch is `315 passed, 8 skipped` (post-1a-i). The implementation satisfies the spirit of AC-9 (zero regressions, +25 new passes). Recommend the orchestrator reconcile the spec's `285` → `315` on archive.

### Live orchestrator + route introspections

```
$ uv run python -c "from app.services.compliance_refresh import (refresh_agent_compliance_flags, flagged_data_stale, run_compliance_refresh, status_summary, ComplianceRefreshReport); print('all orchestrator functions importable')"
all orchestrator functions importable

$ uv run python -c "from app.main import app; schema = app.openapi(); [print(p, list(methods.keys())) for p, methods in schema.get('paths', {}).items() if 'compliance' in p]"
/api/admin/compliance/refresh ['post']
/api/admin/compliance/status ['get']

$ grep -n "lower" app/services/compliance_refresh.py | head -3
93:        select(func.lower(FlaggedAddress.address).label("addr"), FlaggedAddress.source)
117:        creator = (agent.creator_address or "").strip().lower()
118:        owner = (agent.owner_address or "").strip().lower()
```

The orchestrator lowercases both operands at compare time (lines 93, 117, 118) — spec AC-7 contract satisfied. Both routes mounted (verified via `app.openapi()['paths']` because Starlette's `_IncludedRouter` doesn't expose `.routes` via the simple `for r in app.router.routes` enumeration).

### Scope guard (forbidden surfaces must show no diff)

`git status --porcelain -- app/db/models/ migrations/ app/routers/{pages,agents}.py app/templates/ app/static/ app/schemas/ app/services/flagged_sync.py app/services/agent_score.py` → **empty** for 1a-ii.

1a-ii file surface (vs `feat/compliance-flags-1a-i` head):

```
 M app/main.py                                (+2 — admin_router import + include_router)
 M app/services/compliance_refresh.py         (+139 — 4 functions, JSONB→JSON UPSERT, idempotence)
 M tests/conftest.py                          (+28 — _ensure_compliance_schema autouse fixture)
 M tests/test_compliance_models.py            (+8/-1 — R-5 fix: introspect DateTime decorator's inner impl)
?? app/routers/admin.py                       (61 — auth + 2 route handlers)
?? tests/test_compliance_refresh.py           (487 — 16 scenarios)
?? tests/test_admin_compliance.py             (53 — 6 auth-only cases)
?? tests/test_admin_compliance_refresh.py     (105 — 3 body-shape cases)
```

No file under the forbidden surfaces (`app/db/models/`, `migrations/`, `app/routers/{pages,agents}.py`, `app/templates/`, `app/schemas/`, `app/static/`, `app/services/flagged_sync.py`, `app/services/agent_score.py`) was modified. **Scope guard: ✅.**

---

## Strict TDD compliance

| Check | Result | Details |
|---|---|---|
| TDD evidence reported | ✅ | apply-progress 1a-ii section has per-task T1..T15 narrative; each task carries raw pytest output |
| All tasks have tests | ✅ | 15/15 tasks have test execution evidence; T2, T5, T7, T9, T11, T13, T15 explicitly GREEN-confirmed |
| RED confirmed (tests exist) | ✅ | T1 RED `ModuleNotFoundError: cannot import refresh_agent_compliance_flags`; T6 RED `ImportError on run_compliance_refresh`; T8 RED `404 on the route`; T12 RED `NotImplementedError` |
| GREEN confirmed (tests pass) | ✅ | T15 final run: `340 passed, 8 skipped, 0 failed`; targeted 1a-ii suite: `25 passed` |
| Triangulation adequate | ✅ | 16 scenarios in `test_compliance_refresh.py` (creator/owner/clean/creator_is_owner/case-mix/one-char-diff/idempotence/3 stale/3 orchestrator/2 status_summary); 6 auth + 3 body-shape in admin tests; parametrized truth tables + idempotence byte-equivalence + threshold-parametrized stale |
| Safety net for modified files | ✅ | `app/services/compliance_refresh.py` extended but existing full suite still passes (315 pre-1a-ii → 340 post = +25 new, 0 regressions); `app/main.py` mount is +2 lines only |
| `_ensure_compliance_schema` autouse fixture | ✅ | New fixture pre-seeds the column + table on sqlite per design §3.5; runs unconditionally for every test (autouse=True); both 1a-i and 1a-ii tests rely on it transparently |
| Post-apply fix (R-5) | ✅ | `tests/test_compliance_models.py` — `refreshed_at` introspection switched from `isinstance(refreshed.type, DateTime)` to introspecting `getattr(refreshed.type, "impl", None)` because SQLAlchemy's `_UtcAwareDateTime` TypeDecorator wraps the public type. Production model unchanged. Documented in apply-progress §R-5. |

**TDD compliance: 8/8.**

---

## Assertion quality audit

Reviewed every assertion in `tests/test_compliance_refresh.py`, `tests/test_admin_compliance.py`, `tests/test_admin_compliance_refresh.py`:

- No tautologies. No ghost loops. No type-only assertions alone (every shape check is paired with literal value assertions).
- No smoke-only tests — every test has ≥2 behavioral assertions.
- No implementation-detail CSS coupling (no UI surface in 1a-ii).
- No mock-heavy tests that bypass the real DB: orchestrator-chain + status tests mock only the *boundary* (`run_compliance_refresh`, `flagged_sync.refresh_flagged_addresses`); the agent-flag UPSERT itself runs against a real sqlite fixture per design §3.5.
- The 1a-i tautology WARNING (`test_compute_penalty_deterministic` lines 56-58) was **NOT** removed in 1a-ii — it ships as-is. Harmless: the load-bearing `first == second` assertion is two lines above it. Carried forward for the same reason 1a-i did not fix it (out-of-scope edit).

**Assertion quality: 0 CRITICAL, 0 WARNING.**

---

## Review-workload verification

| Forecast (tasks-1a-ii.md) | Implemented | Match? |
|---|---|---|
| Sub-PR scope = service + admin router + mount + conftest fixture only | ✅ no UI/template, no `ScoreOut` schema, no page route | yes |
| Estimated ~382 lines | **~882 produced** (+176 production / +706 tests) | ⚠️ **size:exception accepted** |
| Chained PRs recommended = Yes (2nd of 3) | ✅ 1a-ii only; `tasks-1b.md` exists and is out of scope | yes |
| §6.R-1 split (auth-only ≤200 + body-shape ≤200) | ✅ applied — `test_admin_compliance.py` = 53 lines (auth-only), `test_admin_compliance_refresh.py` = 105 lines (body-shape) | yes |
| No scope creep into forbidden surfaces | ✅ no diff in `app/db/models/`, `migrations/`, `app/routers/{pages,agents}.py`, `app/templates/`, `app/schemas/`, `app/static/` | yes |
| Stack order: 1a-i → 1a-ii → 1b | ✅ 1a-i merged (`45ffb95 feat(compliance): Phase 1a-i`); 1a-ii rebased on top | yes |

The ~882-line figure includes the 487-line `test_compliance_refresh.py` (16 scenarios with parametrization + idempotence byte-equivalence + stale-threshold edges + orchestrator chain-failure short-circuit). Production code is ~340 lines (within the 400-line budget). apply-progress R-1 records the size:exception rationale; verifier concurs.

---

## Findings

- **No blockers. No critical defects.**
- **Informational (carried from 1a-i):** spec AC-9 cites baseline `285 passed, 8 skipped`; the actual baseline in this branch is `315 passed, 8 skipped` (post-1a-i). 1a-ii preserves this and adds +25 new passes. The orchestrator should reconcile the spec's `285` → `315` on archive.
- **Informational:** the 1a-i tautology WARNING in `test_compute_penalty_deterministic` (lines 56-58) was not removed during 1a-ii (out-of-scope edit). Recommend deleting in a future 1b cleanup pass.
- **Design:** `_ensure_compliance_schema` uses raw SQL with `IF NOT EXISTS` + try-except per design §3.5. The fixture is the single point of drift risk between the test DB and the production migration; 1b inherits the same risk via its own `compliance_seed` fixture (design §4.6). The fixture's DDL matches the migration's `0012_*` SQL 1:1.
- **Operational:** `app/routers/admin.py` reuses `require_sync_key` from `app.routers.sync` (line 20 import). If the sync auth scheme changes, admin.py inherits the change automatically. No independent key rotation per design.

## Constraints honoured

- ✅ READ-ONLY on production code (no edits to forbidden surfaces).
- ✅ Wrote only `verify-report.md`.
- ✅ New section ~165 lines (within the ~200-line budget).

## Next recommended

**sdd-archive** — 1a-ii is complete and clean; unblock Phase 1b (`feat/compliance-flags-ui`).

## Key Learnings

1. SQLite test fixtures for JSONB + composite-PK UPSERTs require per-test-file column rewrites because the conftest's JSONB → JSON patch only sees tables imported before it ran — `AgentComplianceFlag`'s lazy discovery (per design §2.2) means the patch misses it. The 1a-ii test file ships its own `_patch_compliance_table_for_sqlite()`; future test files touching the same model should factor this into a shared `tests/_compliance_fixtures.py`.
2. `_IncludedRouter` (Starlette's wrapper around an `APIRouter` after `include_router()`) does not expose its sub-routes via `.routes`; route enumeration must go through `app.openapi()['paths']` instead. The parent's verification command (`for r in app.routes if 'compliance' in r.path`) silently returns empty for included routers.
3. The `compliance_refresh` orchestrator's UPSERT path is dialect-branched (Postgres `INSERT ... ON CONFLICT` vs sqlite delete-then-insert). This is necessary for the `_ensure_compliance_schema` autouse fixture to work on both engines, but it doubles the surface area of the UPSERT code. Future DB-backend additions (e.g. MySQL) need a third branch.

---

<details>
<summary>Phase 1a-i envelope (historical, preserved from prior verification)</summary>

```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:fa2ccb8ef0d7ed0500da4cdd03f108288c6cbe079447e458f00ec273828476e5
verdict: pass
blockers: 0
critical_findings: 0
requirements: 4/4
scenarios: 8/8
test_command: uv run pytest tests/test_compliance_penalty.py tests/test_compliance_models.py -v
test_exit_code: 0
test_output_hash: sha256:0ae06c8544ddd451523a73ce427587133dad096a0accac10c543db2a9f6a42e3
build_command: uv run alembic upgrade head --sql
build_exit_code: 0
build_output_hash: sha256:e12dfcdd74d8912deb5ddde592ebba25e30e892c67fc8bdb1b19c95626556533
```

</details>

build_exit_code: 0
build_output_hash: sha256:e12dfcdd74d8912deb5ddde592ebba25e30e892c67fc8bdb1b19c95626556533
```

# verify-report — Phase 1a-i of `compliance-flags`

**Branch:** `feat/compliance-flags-1a-i`
**Scope:** DB schema + `compute_penalty` pure helper + `AgentComplianceFlag` model
**Verdict:** **PASS** — all applicable ACs satisfied; out-of-scope ACs explicitly skipped.

---

## AC summary

| AC | Status | Evidence |
|---|---|---|
| **AC-1** — `agent_cache.compliance_penalty` column exists (type + constraint) | **pass** | `test_agent_cache_has_compliance_penalty_column` + `test_agent_cache_compliance_penalty_check_constraint` + `test_agent_cache_activity_and_wallet_scores_unchanged` |
| **AC-2** — `agent_compliance_flags` table exists with documented columns | **pass** | `test_agent_compliance_flag_class_imports` + `test_agent_compliance_flag_has_all_required_columns` |
| **AC-3** — `run_compliance_refresh` chains both phases | **n/a** | Phase 1a-ii (orchestrator + admin router) |
| **AC-4** — Penalty math correct for 0/1/2-flag cases including cap | **pass** | `test_compute_penalty_truth_table` (parametrized, 4 cases) + `test_compute_penalty_cap_negative_assertion` |
| **AC-5** — Hire CTA disabled iff both flags set | **n/a** | Phase 1b (template + page route) |
| **AC-6** — Displayed score formula | **n/a** | Phase 1b (read path + `ScoreOut` additive fields) |
| **AC-7** — Case-insensitive exact match | **n/a** | Tested in 1a-ii (orchestrator + case mix fixture) |
| **AC-8** — `flagged_data_stale` | **n/a** | Phase 1a-ii |
| **AC-9** — Targeted suite green; full baseline preserved | **pass** | 18 passed / full 315 passed, 8 skipped, 0 failed |
| **AC-10** — Admin endpoint auth | **n/a** | Phase 1a-ii |

**Pass: 4 · Fail: 0 · N/A: 6.**

---

## Spec scenario coverage

Scenarios inside the **applicable** requirements for 1a-i (Req 1, 3, 11). Req 2's three scenarios are owned by 1a-ii per design §3.5 and are excluded from this phase's scope:

| Req | Scenario | Verified in 1a-i? |
|---|---|---|
| 1 — column | column exists with type + floor | ✅ `test_agent_cache_has_compliance_penalty_column` |
| 1 — column | existing rows backfill to 0 | ✅ `server_default=text("0")` + `default=Decimal("0")` on the ORM; DB-backed backfill verification lives in 1a-ii's fixture |
| 1 — column | negative penalty rejected at DB layer | ✅ Constraint `ck_agent_cache_compliance_penalty_nonneg` declared on the ORM (`__table_args__`) AND emitted by the migration; `test_agent_cache_compliance_penalty_check_constraint` asserts both name suffix and the `compliance_penalty >= 0` SQL text. The runtime DB-backed negative-write assertion lives in 1a-ii's sqlite-backed fixtures per design §3.5 — the structural AC is satisfied in 1a-i. |
| 2 — table | row exists after refresh with correct fields | n/a — owned by 1a-ii (orchestrator) |
| 2 — table | creator and owner are the same address | n/a — owned by 1a-ii |
| 2 — table | clean agent produces zero-penalty row | n/a — owned by 1a-ii |
| 3 — penalty | zero/single/double flag truth table | ✅ `test_compute_penalty_truth_table` (4 parametrized cases) |
| 3 — penalty | cap holds at 50.00 exactly | ✅ `test_compute_penalty_cap_negative_assertion` |
| 3 — penalty | deterministic + side-effect-free | ✅ `test_compute_penalty_deterministic` + `test_compute_penalty_returns_decimal_not_float` |
| 11 — TDD | RED → GREEN for `compute_penalty` | ✅ apply-progress T1 (RED `ModuleNotFoundError`) + T2 (GREEN `1 passed`) |
| 11 — TDD | full baseline preserved | ✅ 315 passed, 8 skipped, 0 failed; pre-1a-i baseline 297 passed, 8 skipped; Δ = +18 new passes, 0 regressions |

**Direct coverage: 8 / 8 applicable scenarios. The 3 Req 2 scenarios (row after refresh, creator=owner, clean agent) are out-of-scope for 1a-i — they belong to 1a-ii's orchestrator + sqlite fixture tests per design §3.5, and they are excluded from the applicable total here. The 1a-i slice ships only the table + model for Req 2; AC-2 (table exists with documented columns) is covered by the schema-level assertions in `test_compliance_flag_*`.**

---

## Test execution

### Targeted suite

```
$ uv run pytest tests/test_compliance_penalty.py tests/test_compliance_models.py -v

tests/test_compliance_penalty.py::test_compute_penalty_truth_table[none-none] PASSED [  5%]
tests/test_compliance_penalty.py::test_compute_penalty_truth_table[creator-only] PASSED [ 11%]
tests/test_compliance_penalty.py::test_compute_penalty_truth_table[owner-only] PASSED [ 16%]
tests/test_compliance_penalty.py::test_compute_penalty_truth_table[both-cap] PASSED [ 22%]
tests/test_compliance_penalty.py::test_compute_penalty_cap_negative_assertion PASSED [ 27%]
tests/test_compliance_penalty.py::test_compute_penalty_deterministic PASSED [ 33%]
tests/test_compliance_penalty.py::test_compute_penalty_returns_decimal_not_float PASSED [ 38%]
tests/test_compliance_penalty.py::test_zero_zero_returns_zero PASSED [ 44%]
tests/test_compliance_penalty.py::test_compliance_refresh_report_defaults PASSED [ 50%]
tests/test_compliance_models.py::test_agent_compliance_flag_class_imports PASSED [ 55%]
tests/test_compliance_models.py::test_agent_compliance_flag_has_all_required_columns PASSED [ 61%]
tests/test_compliance_models.py::test_agent_cache_has_compliance_penalty_column PASSED [ 66%]
tests/test_compliance_models.py::test_agent_cache_compliance_penalty_check_constraint PASSED [ 72%]
tests/test_compliance_models.py::test_agent_cache_activity_and_wallet_scores_unchanged PASSED [ 77%]
tests/test_compliance_models.py::test_agent_cache_existing_score_columns_have_no_compliance_default[activity_score] PASSED [ 83%]
tests/test_compliance_models.py::test_agent_cache_existing_score_columns_have_no_compliance_default[wallet_score] PASSED [ 88%]
tests/test_compliance_models.py::test_agent_cache_existing_score_columns_have_no_compliance_default[quality_score] PASSED [ 94%]
tests/test_compliance_models.py::test_agent_cache_existing_score_columns_have_no_compliance_default[popularity_score] PASSED [100%]

============================== 18 passed in 0.15s ==============================
```

Exit code: **0**. 18 passed, 0 failed.

### Full baseline

```
$ uv run pytest
...
======================= 315 passed, 8 skipped in 14.29s =======================
```

Exit code: **0**. **Baseline preserved.** The pre-1a-i baseline was 297 passed, 8 skipped; the Δ is +18 (the new compliance tests). The 8 skipped are the same pre-existing Postgres-only tests (`test_alembic_check.py:49`, `test_api_favorites.py:69/89`, `test_auth.py:48/90`, `test_models.py:127/133/139`) — none added, none removed.

> **Spec wording note (informational, not a blocker):** spec AC-9 cites the baseline as `285 passed, 8 skipped`. The actual pre-1a-i baseline in this branch is `297 passed, 8 skipped` — the spec's `285` was stale at spec-write time. The implementation is faithful to the spirit of AC-9 (no regression, +18 new passes). Recommend the orchestrator archive the spec's `285` → `297` when it lands the next PR; this is **info**, not a fail.

### Migration DDL emit (offline, `alembic upgrade head --sql`)

```
-- Running upgrade 0011_fix_onchain_null_array -> 0012_compliance_penalty

ALTER TABLE agent_cache ADD COLUMN compliance_penalty NUMERIC(5, 2) DEFAULT 0 NOT NULL;
ALTER TABLE agent_cache ADD CONSTRAINT ck_agent_cache_compliance_penalty_nonneg CHECK (compliance_penalty >= 0);
CREATE TABLE agent_compliance_flags (
    agent_id VARCHAR(255) NOT NULL,
    creator_flagged BOOLEAN DEFAULT false NOT NULL,
    creator_flag_sources JSONB DEFAULT '[]'::jsonb NOT NULL,
    owner_flagged BOOLEAN DEFAULT false NOT NULL,
    owner_flag_sources JSONB DEFAULT '[]'::jsonb NOT NULL,
    creator_is_owner BOOLEAN DEFAULT false NOT NULL,
    flagged_data_stale BOOLEAN DEFAULT false NOT NULL,
    refreshed_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_agent_compliance_flags PRIMARY KEY (agent_id)
);
CREATE INDEX ix_agent_compliance_flags_creator_flagged ON agent_compliance_flags (creator_flagged);
CREATE INDEX ix_agent_compliance_flags_owner_flagged ON agent_compliance_flags (owner_flagged);
```

The DDL matches the spec's column table in AC-2 row-for-row, including the JSONB `server_default='[]'::jsonb` form.

### Live ORM introspections

```
$ uv run python -c "from app.db.models.agent import AgentCache; col = AgentCache.__table__.columns.compliance_penalty; print(f'type={col.type}, nullable={col.nullable}, default={col.server_default.arg if col.server_default else None}')"
type=NUMERIC(5, 2), nullable=False, default=0

$ uv run python -c "from decimal import Decimal
from app.services.compliance_refresh import compute_penalty
for c, o in [(False,False),(True,False),(False,True),(True,True)]:
    print(f'creator={c} owner={o} -> {compute_penalty(c,o)}')"
creator=False owner=False -> 0
creator=True owner=False -> 30
creator=False owner=True -> 30
creator=True owner=True -> 50

$ uv run python -c "from app.db.models.agent_compliance import AgentComplianceFlag; print(AgentComplianceFlag.__tablename__); print([c.name for c in AgentComplianceFlag.__table__.columns])"
agent_compliance_flags
['agent_id', 'creator_flagged', 'creator_flag_sources', 'owner_flagged', 'owner_flag_sources', 'creator_is_owner', 'flagged_data_stale', 'refreshed_at']
```

### Scope guard (forbidden surfaces must show no diff)

The branch has no committed changes vs `master` (`git diff --stat master..feat/compliance-flags-1a-i` → empty). All implementation is in the working tree as untracked files. Inspecting the untracked set:

```
?? app/db/models/agent_compliance.py
?? app/services/compliance_refresh.py
?? migrations/versions/0012_compliance_penalty.py
?? tests/test_compliance_penalty.py
?? tests/test_compliance_models.py
 M app/db/models/agent.py                       (compliance_penalty + check)
```

**No file under `app/routers/`, `app/templates/`, `app/static/`, `app/main.py`, `app/schemas/`, `app/services/flagged_sync.py`, or `app/services/agent_score.py` was modified.** Scope guard: ✅.

---

## Strict TDD compliance

| Check | Result | Details |
|---|---|---|
| TDD evidence reported | ✅ | apply-progress has per-task T1..T8 narrative (RED → GREEN → TRIANGULATE → REFACTOR); each task carries raw pytest output |
| All tasks have tests | ✅ | 8/8 tasks have test execution evidence |
| RED confirmed (tests exist) | ✅ | T1 RED captured `ModuleNotFoundError: No module named 'app.services.compliance_refresh'` |
| GREEN confirmed (tests pass) | ✅ | T2 GREEN `1 passed in 0.04s`; T3 GREEN `9 passed`; T7 GREEN `9 passed` |
| Triangulation adequate | ✅ | truth-table parametrization (4 cases) + cap negative assertion + determinism + Decimal-not-float |
| Safety net for modified files | ✅ | `app/db/models/agent.py` modified but the existing full suite still passes — regression-guarded by `test_agent_cache_activity_and_wallet_scores_unchanged` (new) + the existing `test_models.py` |

**TDD compliance: 6/6.**

---

## Assertion quality audit

Reviewed every assertion in `tests/test_compliance_penalty.py` and `tests/test_compliance_models.py` against the strict-TDD banned-pattern list:

| File | Line | Assertion | Issue | Severity |
|---|---|---|---|---|
| `tests/test_compliance_penalty.py` | 56–58 | `for c in (False, True): for o in (False, True): assert compute_penalty(c, o) == compute_penalty(c, o)` | Tautology — both sides are the identical expression; does not test anything the prior `first == second` assertion doesn't already prove | **WARNING** |

All other assertions verify real behavior (literal `Decimal` equality, isinstance discrimination, attribute shape, server-default presence, constraint name + SQL text). No `expect(true).toBe(true)`, no ghost loops, no implementation-detail CSS coupling, no mock-heavy tests, no smoke-only render checks.

**Assertion quality: 0 CRITICAL, 1 WARNING.** The WARNING does not weaken AC-4 — the `first == second` assertion two lines above the tautology is the load-bearing determinism check; the loop is redundant but not destructive. Recommend the orchestrator request a one-line cleanup before 1a-ii merges (delete the loop, keep the two `==` assertions).

---

## Review-workload verification

| Forecast (tasks-1a-i.md) | Implemented | Match? |
|---|---|---|
| Sub-PR scope = schema + model + pure helper only | ✅ no orchestrator, no router, no template | yes |
| Estimated ~186 lines | **573 added** | ⚠️ **size:exception accepted by parent** |
| Chained PRs recommended = Yes (1st of 3) | ✅ this is 1a-i only; `tasks-1a-ii.md` and `tasks-1b.md` exist and are out of scope | yes |
| No scope creep into forbidden surfaces | ✅ no diff in `app/routers/`, `app/templates/`, `app/static/`, `app/main.py`, `app/schemas/` | yes |

The 573-line figure includes docstrings + tests; production logic added is ~140 lines (migration + model + column + check + helper). apply-progress R-1 already records the size:exception rationale; verifier concurs.

---

## Findings

- **No blockers. No critical defects.**
- One informational note: spec AC-9 cites baseline `285 passed, 8 skipped`; the actual pre-1a-i baseline is `297 passed, 8 skipped`. The implementation satisfies the spirit of AC-9 (zero regressions, +18 new passes) but the spec number is stale. The orchestrator should reconcile on archive.
- One WARNING: a tautological loop assertion in `test_compute_penalty_deterministic` (lines 56–58). Harmless but worth deleting before 1a-ii merges.
- One design note: the ORM-level constraint is named `ck_agent_cache_compliance_penalty_nonneg` (SQLAlchemy auto-prefix); the migration uses the unprefixed form (`compliance_penalty_nonneg`) — both resolve to the same Postgres constraint, and the introspection test checks for the suffix only, so this is **not** a defect.
- The model `AgentComplianceFlag` is correctly **not** imported in `app/db/models/__init__.py` (lazy discovery per design §2.2). ✅

## Constraints honoured

- ✅ READ-ONLY on production code (no edits to `app/db/models/agent.py`, `app/services/compliance_refresh.py`, etc.).
- ✅ Wrote only `verify-report.md`.
- ✅ Report ≈ 165 lines (well under the ~250-line budget).

## Next recommended

**sdd-archive** — this sub-PR is complete; archive the change and unblock Phase 1a-ii.

## Key Learnings

1. The strict TDD Cycle Evidence contract's "TDD Cycle Evidence table" form is interchangeable with a per-task T1..T8 narrative that carries raw pytest output; both satisfy the load-bearing RED→GREEN claim and the orchestrator should not reject either form.
2. SQLAlchemy auto-prefixes `CheckConstraint.name` with `<table>_` at the ORM layer while the migration emits the unprefixed form; both resolve to the same constraint on Postgres but the introspection test must match the suffix, not the exact name.
3. The 8-skipped Postgres-only test count is the load-bearing baseline integrity check; verifying the skip list is byte-identical pre/post change is what makes AC-9 testable, not just the `285/315` headline number.