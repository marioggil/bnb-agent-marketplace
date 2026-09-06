# Tasks — Phase 1a-i (DB schema + pure helper)

**Sub-PR:** `1a-i`
**Branch:** `feat/compliance-flags-db-schema`
**Base:** `main`
**Stack slot:** 1st PR (no prior dependency)
**Depends on:** —

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~186 |
| 400-line budget risk | Low (53% headroom) |
| Chained PRs recommended | Yes (this is the 1st of 3) |
| Suggested split | n/a (already 1 of 3) |
| Delivery strategy | ask-on-risk |
| Chain strategy | stacked-to-main |

```text
Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: Low
```

## Dependency statement

None — this is the first sub-PR. Merge 1a-i to `main` before starting 1a-ii.

## Task ordering rationale

TDD order: pure helper first (zero infra), then the SQLAlchemy model that uses the column type, then the migration that lands in the DB, finally import/column-existence tests. Model before migration because the SQLAlchemy column type (`Numeric(5, 2)`) constrains the migration choice. Pure helper before model because the helper is the public surface 1a-ii and 1b both call — it must be defined on a module 1a-i ships.

---

## Tasks

- [ ] **T1 — RED: `test_zero_zero_returns_zero`.** In `tests/test_compliance_penalty.py` write `from app.services.compliance_refresh import compute_penalty` and assert `compute_penalty(False, False) == Decimal("0.00")`. Run `uv run pytest tests/test_compliance_penalty.py -x`. Expected: `ModuleNotFoundError` or `ImportError` on `app.services.compliance_refresh` (file does not exist yet). Capture the failure line; this is RED evidence. <!-- sdd-owner: implementation -->

- [ ] **T2 — GREEN: implement `compute_penalty(False, False) -> Decimal("0.00")`.** Create `app/services/compliance_refresh.py` with the module docstring, `_PENALTY_PER_FLAG = 30`, `_PENALTY_CAP = Decimal("50")`, the `ComplianceRefreshReport` dataclass, and `def compute_penalty(creator_flagged: bool, owner_flagged: bool) -> Decimal` returning `min(Decimal(_PENALTY_PER_FLAG * int(creator_flagged) + _PENALTY_PER_FLAG * int(owner_flagged)), _PENALTY_CAP)`. Run `uv run pytest tests/test_compliance_penalty.py::test_zero_zero_returns_zero`. Expected: passes. Files touched: `app/services/compliance_refresh.py` (new, ~50 lines including skeleton). <!-- sdd-owner: implementation -->

- [ ] **T3 — TRIANGULATE: full truth table + negative cap assertion + Decimal-not-float.** Add to `tests/test_compliance_penalty.py`: parametrized `test_compute_penalty_truth_table` covering all 4 cases (`(F,F)→0.00`, `(T,F)→30.00`, `(F,T)→30.00`, `(T,T)→50.00`), `test_compute_penalty_cap_negative_assertion` (assert `!= Decimal("60.00")`), `test_compute_penalty_deterministic` (two invocations equal), `test_compute_penalty_returns_decimal_not_float` (`isinstance(..., Decimal)` and `not isinstance(..., float)`). Use literal `==` Decimal equality throughout — never `pytest.approx`. Run `uv run pytest tests/test_compliance_penalty.py -v`. Expected: 5+ passes. Files touched: `tests/test_compliance_penalty.py` (extend). <!-- sdd-owner: implementation -->

- [ ] **T4 — Alembic migration `0012_compliance_penalty.py`.** Run `ls migrations/versions/` to confirm `0012_compliance_penalty` is the next free slot (`0011_fix_onchain_null_array` is current head). If `0012_*` is taken, renumber to `0013_*` and point `down_revision` at the new head (design §5.5). Create `migrations/versions/0012_compliance_penalty.py` with `revision = "0012_compliance_penalty"`, `down_revision = "0011_fix_onchain_null_array"`, `upgrade()` adding `compliance_penalty Numeric(5,2) NOT NULL DEFAULT 0` to `agent_cache`, the `CheckConstraint("compliance_penalty_nonneg", "agent_cache", "compliance_penalty >= 0")`, and creating `agent_compliance_flags` per the design §2.1 column table. Files touched: `migrations/versions/0012_compliance_penalty.py` (new, ~60 lines). Run `uv run alembic upgrade head` on the sqlite test DB to confirm green; rollback with `uv run alembic downgrade -1` and re-up to confirm symmetry. <!-- sdd-owner: implementation -->

- [ ] **T5 — Add `compliance_penalty` to `AgentCache`.** In `app/db/models/agent.py` after `metadata_completeness_score` (~line 199), add `compliance_penalty: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, server_default=text("0"), default=Decimal("0"))`. In `__table_args__` add `CheckConstraint("compliance_penalty >= 0", name="compliance_penalty_nonneg")`. Do NOT touch `activity_score` / `wallet_score`. Run `uv run pytest tests/test_compliance_models.py` (created in T7). Files touched: `app/db/models/agent.py` (~6 lines added). <!-- sdd-owner: implementation -->

- [ ] **T6 — New `AgentComplianceFlag` model.** Create `app/db/models/agent_compliance.py` with the `AgentComplianceFlag` declarative class mirroring the migration's table 1:1 — columns: `agent_id String(255) PK`, `creator_flagged Boolean NOT NULL DEFAULT false`, `creator_flag_sources JSONB NOT NULL DEFAULT '[]'::jsonb`, `owner_flagged Boolean NOT NULL DEFAULT false`, `owner_flag_sources JSONB NOT NULL DEFAULT '[]'::jsonb`, `creator_is_owner Boolean NOT NULL DEFAULT false`, `flagged_data_stale Boolean NOT NULL DEFAULT false`, `refreshed_at DateTime(timezone=True) NOT NULL`. Do NOT add to `app/db/models/__init__.py` (lazy discovery). Files touched: `app/db/models/agent_compliance.py` (new, ~40 lines). <!-- sdd-owner: implementation -->

- [ ] **T7 — RED+GREEN: `tests/test_compliance_models.py`.** Create `tests/test_compliance_models.py` with `test_agent_compliance_flag_class_imports` (imports `AgentComplianceFlag`, asserts the class exists) and `test_agent_cache_has_compliance_penalty_column` (introspects `AgentCache.__table__.columns` for `compliance_penalty`, asserts its `type` is `Numeric(5, 2)` and `nullable is False`). Run `uv run pytest tests/test_compliance_models.py`. Expected: passes (GREEN once T5 and T6 are done — order matters). Files touched: `tests/test_compliance_models.py` (new, ~30 lines). <!-- sdd-owner: implementation -->

- [ ] **T8 — Final verification.** Run targeted suite then full suite:
  1. `uv run pytest tests/test_compliance_penalty.py tests/test_compliance_models.py -v` → all green.
  2. `uv run pytest` (full suite) → baseline `285 passed, 8 skipped` preserved + new tests pass.
  3. `git diff -- app/routers/{pages,agents,admin,sync}.py` → empty.
  4. `git diff -- app/schemas/` → empty.
  5. `git diff -- app/templates/` → empty.
  6. `git diff -- app/static/` → empty (no JS changes).
  7. `app/services/flagged_sync.py`, `app/services/agent_score.py` byte-identical.
  
  Commit: 1 file modified (`app/db/models/agent.py`), 4 files created (`migrations/versions/0012_compliance_penalty.py`, `app/db/models/agent_compliance.py`, `app/services/compliance_refresh.py`, `tests/test_compliance_penalty.py`, `tests/test_compliance_models.py`). <!-- sdd-owner: implementation -->

---

## REFACTOR notes (apply-time discretion)

- The pure `compute_penalty` is already minimal; no expected refactor needed beyond trimming the test class structure if it bloats.
- The model file's `__table_args__` is shared with existing check constraints; if the alphabetization in design §2.3 conflicts with project ordering, match the surrounding style instead.
- The migration `downgrade()` MUST be a true reverse (drop check, drop column, drop table) — design §2.1 specifies "symmetric reverse".

## Evidence line (each task)

Each GREEN completion is confirmed by the explicit `uv run pytest` invocation shown in the task body. T1 RED = `ModuleNotFoundError`/`ImportError`. T2–T3 GREEN = `Decimal` literal equality passes. T4 GREEN = `alembic upgrade head` exit code 0; RED-on-rollback double-check via `alembic downgrade -1`. T5–T7 GREEN = `tests/test_compliance_models.py`. T8 GREEN = full-suite baseline preserved.

## Verification command (one-shot, run at the end of 1a-i)

```bash
uv run pytest tests/test_compliance_penalty.py tests/test_compliance_models.py -v && \
uv run pytest && \
git diff --stat -- app/ migrations/ tests/
```
