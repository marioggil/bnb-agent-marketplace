# apply-progress — Phase 1a-i (DB schema + pure helper)

**Sub-PR:** `1a-i` of `compliance-flags`
**Branch:** `feat/compliance-flags-1a-i`
**Mode:** Strict TDD — RED → GREEN → TRIANGULATE → REFACTOR
**Test runner:** `uv run pytest`
**Phase order:** tasks-1a-i.md T1..T8

---

## Verification snapshot

| Surface | Status |
| --- | --- |
| `uv run pytest tests/test_compliance_penalty.py tests/test_compliance_models.py -v` | **18 passed** (9 helper + 9 model) |
| `uv run pytest` (full suite) | **315 passed, 8 skipped, 0 failed** |
| Baseline (pre-1a-i) | 297 passed, 8 skipped, 0 failed |
| Δ | **+18 passed**, **0 regressed**, **0 new skipped** |
| `git diff -- app/routers/{pages,agents,admin,sync}.py` | empty |
| `git diff -- app/schemas/` | empty |
| `git diff -- app/templates/` | empty |
| `git diff -- app/static/` | empty |
| `app/services/flagged_sync.py`, `app/services/agent_score.py` byte-identical | yes |
| `uv run alembic upgrade head --sql` (offline DDL emit) | clean |
| `uv run alembic downgrade 0012_compliance_penalty:0011_fix_onchain_null_array --sql` (offline) | clean |

---

## File surface — final diff

```
 M app/db/models/agent.py                                  (18 +, 0 -)
?? app/db/models/agent_compliance.py                       (new, 71 lines)
?? app/services/compliance_refresh.py                      (new, 75 lines)
?? migrations/versions/0012_compliance_penalty.py          (new, 136 lines)
?? tests/test_compliance_penalty.py                        (new, 101 lines)
?? tests/test_compliance_models.py                         (new, 172 lines)
```

**Authoured-line budget:** 573 added vs design estimate ~186 → **size:exception recommendation** (see Risks).

---

## T1 — RED: `test_zero_zero_returns_zero`

**Action:** wrote `tests/test_compliance_penalty.py` with `from app.services.compliance_refresh import compute_penalty`.

**Command:**

```bash
uv run pytest tests/test_compliance_penalty.py::test_zero_zero_returns_zero -x
```

**RED evidence (raw output):**

```
tests/test_compliance_penalty.py F
=================================== FAILURES ===================================
__________________________ test_zero_zero_returns_zero __________________________
    def test_zero_zero_returns_zero():
        """T1 RED: helper does not exist yet — expect ModuleNotFoundError."""
>       from app.services.compliance_refresh import compute_penalty
E       ModuleNotFoundError: No module named 'app.services.compliance_refresh'
```

✅ ModuleNotFoundError as expected — RED confirmed.

## T2 — GREEN: implement `compute_penalty`

**Action:** created `app/services/compliance_refresh.py` with constants, the `ComplianceRefreshReport` dataclass, and the pure helper.

**Command:**

```bash
uv run pytest tests/test_compliance_penalty.py::test_zero_zero_returns_zero -x
```

**GREEN evidence (raw output):**

```
collected 1 item
tests/test_compliance_penalty.py .                                       [100%]
=============================== 1 passed in 0.04s ===============================
```

## T3 — TRIANGULATE: full truth table + edge cases

**Action:** extended the test file with the parametrized truth table (4 cases), the negative cap assertion, the determinism check, the Decimal-not-float check, and a `ComplianceRefreshReport` dataclass smoke test.

**Command:**

```bash
uv run pytest tests/test_compliance_penalty.py -v
```

**GREEN evidence (last 12 lines):**

```
tests/test_compliance_penalty.py::test_compute_penalty_truth_table[none-none] PASSED [ 11%]
tests/test_compliance_penalty.py::test_compute_penalty_truth_table[creator-only] PASSED [ 22%]
tests/test_compliance_penalty.py::test_compute_penalty_truth_table[owner-only] PASSED [ 33%]
tests/test_compliance_penalty.py::test_compute_penalty_truth_table[both-cap] PASSED [ 44%]
tests/test_compliance_penalty.py::test_compute_penalty_cap_negative_assertion PASSED [ 55%]
tests/test_compliance_penalty.py::test_compute_penalty_deterministic PASSED [ 66%]
tests/test_compliance_penalty.py::test_compute_penalty_returns_decimal_not_float PASSED [ 77%]
tests/test_compliance_penalty.py::test_zero_zero_returns_zero PASSED     [ 88%]
tests/test_compliance_penalty.py::test_compliance_refresh_report_defaults PASSED [100%]
=============================== 9 passed in 0.12s ===============================
```

✅ Spec AC-4 (truth table + cap) + design §5.4 (Decimal return) verified.

## T4 — Alembic migration `0012_compliance_penalty.py`

**Action:** confirmed `0012_*` is the next free slot (`ls migrations/versions/` → 0011 is head), wrote the migration with both schema additions.

**Verify command (Postgres DDL emit, no live DB needed):**

```bash
uv run alembic upgrade head --sql
```

**GREEN evidence (last 16 lines — shows the new 0012 step):**

```
INFO  [alembic.runtime.migration] Running upgrade 0011_fix_onchain_null_array -> 0012_compliance_penalty, Add `agent_cache.compliance_penalty` + `agent_compliance_flags` table.
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
UPDATE alembic_version SET version_num='0012_compliance_penalty' WHERE alembic_version.version_num='0011_fix_onchain_null_array';
COMMIT;
```

**Symmetric reverse (downgrade 0012 → 0011):**

```bash
uv run alembic downgrade --sql 0012_compliance_penalty:0011_fix_onchain_null_array
```

```
DROP INDEX ix_agent_compliance_flags_owner_flagged;
DROP INDEX ix_agent_compliance_flags_creator_flagged;
DROP TABLE agent_compliance_flags;
ALTER TABLE agent_cache DROP CONSTRAINT ck_agent_cache_compliance_penalty_nonneg;
ALTER TABLE agent_cache DROP COLUMN compliance_penalty;
UPDATE alembic_version SET version_num='0011_fix_onchain_null_array' WHERE alembic_version.version_num='0012_compliance_penalty';
COMMIT;
```

✅ Both directions verified. Alembic auto-prefixed `ck_agent_cache_compliance_penalty_nonneg` because the underlying type is `sa.CheckConstraint`; the migration also adds it via `op.create_check_constraint` with the unprefixed name so the names match on both sides.

> **No live Postgres available locally** — verification used `--sql` offline mode which emits the exact DDL alembic would run. The `_create_schema` fixture in `tests/conftest.py` exercises the same DDL via `Base.metadata.create_all` against sqlite (see T7), which is what the test suite actually depends on.

## T5 — `compliance_penalty` on `AgentCache`

**Action:** inserted the column (Numeric(5, 2), NOT NULL, server_default=0, default=Decimal("0")) right after `metadata_completeness_score`, and appended a matching `CheckConstraint("compliance_penalty >= 0", name="compliance_penalty_nonneg")` to `__table_args__`.

**Verify command:**

```bash
uv run python -c "from app.db.models.agent import AgentCache; print(AgentCache.compliance_penalty)"
```

**Output:** `AgentCache.compliance_penalty`

✅ Column descriptor resolves. `activity_score` and `wallet_score` not touched (regression-guarded in `test_compliance_models.py::test_agent_cache_activity_and_wallet_scores_unchanged`).

## T6 — `AgentComplianceFlag` model

**Action:** created `app/db/models/agent_compliance.py` mirroring the migration 1:1. **Not** imported in `app/db/models/__init__.py` (design §2.2 — lazy discovery).

**Verify command:**

```bash
uv run python -c "from app.db.models.agent_compliance import AgentComplianceFlag; print([c.name for c in AgentComplianceFlag.__table__.columns])"
```

**Output:** `['agent_id', 'creator_flagged', 'creator_flag_sources', 'owner_flagged', 'owner_flag_sources', 'creator_is_owner', 'flagged_data_stale', 'refreshed_at']`

✅ All 8 columns match design §2.1 / spec AC-2.

## T7 — `tests/test_compliance_models.py` (RED+GREEN)

**Action:** wrote 9 tests covering `AgentComplianceFlag` import + column inventory + the `compliance_penalty` column + the CHECK constraint + regression guards on existing score columns.

**Command:**

```bash
uv run pytest tests/test_compliance_models.py -v
```

**GREEN evidence (last 12 lines):**

```
tests/test_compliance_models.py::test_agent_compliance_flag_class_imports PASSED [ 11%]
tests/test_compliance_models.py::test_agent_compliance_flag_has_all_required_columns PASSED [ 22%]
tests/test_compliance_models.py::test_agent_cache_has_compliance_penalty_column PASSED [ 33%]
tests/test_compliance_models.py::test_agent_cache_compliance_penalty_check_constraint PASSED [ 44%]
tests/test_compliance_models.py::test_agent_cache_activity_and_wallet_scores_unchanged PASSED [ 55%]
tests/test_compliance_models.py::test_agent_cache_existing_score_columns_have_no_compliance_default[activity_score] PASSED [ 66%]
tests/test_compliance_models.py::test_agent_cache_existing_score_columns_have_no_compliance_default[wallet_score] PASSED [ 77%]
tests/test_compliance_models.py::test_agent_cache_existing_score_columns_have_no_compliance_default[quality_score] PASSED [ 88%]
tests/test_compliance_models.py::test_agent_cache_existing_score_columns_have_no_compliance_default[popularity_score] PASSED [100%]
=============================== 9 passed in 0.11s ===============================
```

> **Subtle implementation note:** the conftest's `_patch_metadata_for_sqlite()` patches every JSONB → JSON at conftest-import time, but it only sees tables that were imported before it ran. Because `AgentComplianceFlag` is **not** in `app/db/models/__init__.py` (lazy discovery), the conftest patch never sees it. The new test file imports the model, then runs the same JSONB → JSON + DefaultClause swap in a tiny module-level helper (`_patch_compliance_table_for_sqlite`) so `Base.metadata.create_all` on sqlite accepts the JSONB columns. This is **test-only overhead** — 1a-ii's orchestrator doesn't need to do this because it ships its own conftest fixture per design §3.5.

## T8 — Final verification

**Commands:**

```bash
# Targeted suite
uv run pytest tests/test_compliance_penalty.py tests/test_compliance_models.py -v
# Full suite
uv run pytest
# Scope guardrails
git diff --stat -- app/routers/ app/schemas/ app/templates/ app/static/ app/services/flagged_sync.py app/services/agent_score.py
# File surface
git diff --stat -- app/ migrations/ tests/
```

**Targeted suite (last 6 lines):**

```
tests/test_compliance_models.py::test_agent_cache_existing_score_columns_have_no_compliance_default[quality_score] PASSED [ 94%]
tests/test_compliance_models.py::test_agent_cache_existing_score_columns_have_no_compliance_default[popularity_score] PASSED [100%]
=============================== 18 passed in 0.16s ===============================
```

**Full suite (last 3 lines):**

```
======================= 315 passed, 8 skipped in 14.07s ========================
```

**Scope guardrails:** all six `git diff` checks returned empty.

---

## Deviations from design

None of substance. Three notes for the orchestrator:

1. **Line budget:** design estimated 186 lines; we shipped 573 added lines (size:exception — see Risks). The growth is in **docstrings + tests**, not in production logic. Tests carry the spec ACs as live assertions (e.g. the `Decimal-not-float` check, the `compliance_penalty_nonneg` constraint check, the regression guard on `activity_score`/`wallet_score`). Docstrings preserve the design's invariants as code comments so 1a-ii and 1b have them at the call site.

2. **Test-side JSONB patch:** the `AgentComplianceFlag` model's JSONB columns are swapped to sqlite `JSON` at test-module import time. This is purely a sqlite-vs-postgres accommodation (the model definition itself is unchanged from the migration DDL). 1a-ii's service tests do not need to repeat this — design §3.5 already has the dedicated fixture for the schema-pre-seed pattern.

3. **`Activity_score` / `wallet_score` regression guard:** `test_compliance_models.py::test_agent_cache_existing_score_columns_have_no_compliance_default` (4 parametrized cases) asserts those columns are still nullable and untouched. This is not in the task list explicitly but is the cheapest way to enforce design §5.6 ("activity_score stays canonical").

---

## Remaining tasks

**None for this sub-PR.** All 8 tasks (T1..T8) are complete; downstream sub-PRs (`1a-ii`, `1b`) own the remaining work and are explicitly out of scope per the orchestrator's prompt.

---

## Risks (handover)

| # | Risk | Severity | Note |
| --- | --- | --- | --- |
| **R-1** | **Line budget exceeded (573 added vs 186 estimated → over 400).** | medium | Growth is in **docstrings + tests**, not production logic. Per the apply contract, code/comments/tests must NOT be compressed to hit a budget. Recommendation: **size:exception**. Alternative: trim docstrings by ~50% (the test files are the biggest contributors). |
| **R-2** | No live Postgres locally to run `alembic upgrade head` end-to-end. | low | Verified via `--sql` offline mode (deterministic DDL emit) and via sqlite `Base.metadata.create_all` during the test suite. The CI pipeline runs `alembic upgrade head` against a real Postgres instance; the DDL is well-formed standard Postgres. |
| **R-3** | Test-side JSONB → JSON patch is a one-off hack in `tests/test_compliance_models.py`. | low | 1a-ii's service tests do not need it (design §3.5). If a third test file wants the same model, factor the patch into `tests/_compliance_fixtures.py` per the design's pre-seeding convention. |
| **R-4** | `AlembicCheckConstraint` name auto-prefixing. Migration uses `op.create_check_constraint("compliance_penalty_nonneg", ...)` (unprefixed) — alembic stores it as `ck_agent_cache_compliance_penalty_nonneg` on Postgres. The ORM uses `sa.CheckConstraint(..., name="compliance_penalty_nonneg")` which SQLAlchemy stores as `ck_agent_cache_compliance_penalty_nonneg`. Both forms resolve to the same constraint on Postgres. | low | The introspection test (`test_agent_cache_compliance_penalty_check_constraint`) checks for the suffix, not the exact name, so it survives any future naming-convention tweaks. |

---

## Action context warnings

None — `mode: workspace-planning` is not in the prompt; this is `mode: apply` on branch `feat/compliance-flags-1a-i`. All edits stayed inside the repo root; no `allowedEditRoots` restriction.
