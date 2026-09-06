# Tasks: compliance-flags

**Change:** `compliance-flags`
**Stack order:** `1a-i` → `main` → `1a-ii` → `main` → `1b` → `main`
**Strategy:** stacked-to-main · chained PRs · strict TDD · `uv run pytest`
**Test discipline:** RED → GREEN → TRIANGULATE → REFACTOR with targeted invocation; full suite only at the end of each sub-PR.

---

## Review Workload Forecast (umbrella)

| Field | Value |
|-------|-------|
| Estimated changed lines (sum, 3 PRs) | ~724 (186 + 382 + 156) |
| Per-PR 400-line budget risk | 1a-i **Low** · 1a-ii **Medium** (382/400, 4% headroom) · 1b **Low** |
| Chained PRs recommended | Yes |
| Suggested split | 1a-i → 1a-ii → 1b (3 PRs, stacked-to-main) |
| Delivery strategy | ask-on-risk |
| Chain strategy | stacked-to-main |

```text
Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: Medium
```

> **Operator decision required before apply:** apply 1a-i first in isolation. If 1a-ii's running line count exceeds 380 before its last commit, apply the §6.R-1 mitigation from `design.md` and split `tests/test_admin_compliance.py` before merging 1a-ii. Then apply 1b.

---

## Per-sub-PR forecast (compact)

| Sub-PR | Branch | Base | Files touched (own only) | Lines | Risk | Stack slot |
|---|---|---|---|---|---|---|
| **1a-i** | `feat/compliance-flags-db-schema` | `main` | migration `0012_*`; `app/db/models/agent.py` (+col, +check); new `app/db/models/agent_compliance.py`; new `app/services/compliance_refresh.py` skeleton + `compute_penalty`; new `tests/test_compliance_penalty.py`; new `tests/test_compliance_models.py`. | ~186 | Low | 1st PR |
| **1a-ii** | `feat/compliance-flags-db-service` | `main` (post-1a-i) | `app/services/compliance_refresh.py` extension; new `app/routers/admin.py`; `app/main.py` mount; new `tests/test_compliance_refresh.py`; new `tests/test_admin_compliance.py`. | ~382 | Medium | 2nd PR |
| **1b** | `feat/compliance-flags-ui` | `main` (post-1a-ii) | `app/routers/pages.py` (`agent_detail`); `app/templates/pages/agent_detail.html`; `app/schemas/score.py` (+fields); `app/routers/agents.py` (`get_agent_score`); `tests/test_pages.py` extension; new `tests/test_compliance_api.py`. | ~156 | Low | 3rd PR |

**Hard invariants preserved across all 3 sub-PRs** (full list in `design.md` §5):

- `activity_score`, `wallet_score`, `materialize_score()` untouched.
- `payment.js`, `flagged_sync.py` byte-identical — no JS surface change.
- Case-insensitive exact match against `flagged_addresses.address`; no heuristic/prefix/substring.
- `compute_penalty` returns `Decimal`, not `float`.
- Baseline `285 passed, 8 skipped` preserved at the end of each sub-PR.

---

## Dependency statement (cross-PR)

| Sub-PR | Depends on | Why |
|---|---|---|
| 1a-i | — | First link; pure schema + pure helper. |
| 1a-ii | 1a-i merged | Service reads the column 1a-i added (`compliance_penalty`) and writes/reads the table 1a-i modeled (`agent_compliance_flags`). |
| 1b | 1a-ii merged (and therefore 1a-i) | UI reads precomputed rows and the populated column; schema-aware additive fields. |

**Apply-time rebasing tolerance:** each later sub-PR ships a **pre-seeding fixture** that re-runs the prior sub-PR's DDL / row inserts so a branch can land in any order without waiting on the prior merge. Source of truth: `design.md` §3.5 (`_ensure_compliance_schema`), §4.6 (`compliance_seed`).

---

## Cross-cutting concerns (apply-phase pre-flight, every sub-PR)

These apply before each `sdd-apply-{1a-i|1a-ii|1b}` start, NOT inside apply work itself:

1. **`ls migrations/versions/`** — confirm slot before branching 1a-i. Collision → renumber to `0013_*`, update `down_revision`. (design §5.5, R-2)
2. **`git status` / clean tree** — confirm no in-flight edits; sub-PRs are independent branches off clean main (or post-1a-i for 1a-ii, post-1a-ii for 1b).
3. **`grep -r "compliance_penalty" app/ migrations/ tests/`** — confirm 1a-i's outputs are merged before 1a-ii starts; confirm 1a-ii's column writes have happened before 1b reads them.
4. **Test pre-seeding fixtures** — drop or keep auto-fixtures per the matrix in `design.md` §8. When the prior sub-PR is merged, the `IF NOT EXISTS` / try-except pre-seed branches become harmless no-ops.
5. **Baseline snapshot** — capture `uv run pytest` final line (`285 passed, 8 skipped`) before starting each sub-PR so the post-apply diff is a clean diff against baseline.
6. **Byte-identical guards** — `git diff app/static/ app/services/flagged_sync.py app/services/agent_score.py` must be empty after every sub-PR (design §7).

---

## Stack order — exact sequence

```
main
  └─ feat/compliance-flags-db-schema   (1a-i)
       └─ PR review → merge
            └─ main
                 └─ feat/compliance-flags-db-service  (1a-ii)
                      └─ PR review → merge
                           └─ main
                                └─ feat/compliance-flags-ui  (1b)
                                     └─ PR review → merge
                                          └─ main
```

Each merge is a fast-forward or no-ff merge into `main`. No feature-branch chaining between sub-PRs — each is a sibling from `main`. The dependency chain holds because of the column/table contract, not the branch graph.

---

## Sub-task files

Detailed TDD sequences, file paths, and verification commands:

- **[`tasks-1a-i.md`](tasks-1a-i.md)** — DB schema (migration + model) + pure `compute_penalty` helper (8 tasks).
- **[`tasks-1a-ii.md`](tasks-1a-ii.md)** — Service orchestrator (`refresh_agent_compliance_flags`, `run_compliance_refresh`, `flagged_data_stale`) + admin router (15 tasks).
- **[`tasks-1b.md`](tasks-1b.md)** — UI gate (banner + `#hire-cta` disabled) + additive `ScoreOut` fields + `/score` populate (10 tasks).
