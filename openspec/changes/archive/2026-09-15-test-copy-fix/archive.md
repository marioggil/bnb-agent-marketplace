# SDD Archive Report — `test-copy-fix`

**Change:** `test-copy-fix`
**Phase:** archive
**Artifact store:** `openspec` (authoritative; `openspec/` directory present and writeable)
**Archive date:** 2026-09-15
**Commit verified:** `e20a34f test: fix 3 stale test asserts (x402_supported seed)`
**Workspace:** `/home/mario/Documentos/Bnb_agent`

---

## Status: PASS ✅

All phases completed successfully: init → proposal → spec → design → tasks → apply → verify → archive. Ready for canonical merge and folder move.

---

## Archive Preconditions — All Satisfied

| Check | Result |
|-------|--------|
| Verification report present | ✅ `openspec/changes/test-copy-fix/verify-report.md` exists (verdict `pass`, blockers `0`, critical findings `0`) |
| Verification verdict clearly passing | ✅ `pass` — no `FAIL`, `BLOCKED`, `CRITICAL`, or verification blockers |
| Proposal artifact present | ✅ `openspec/changes/test-copy-fix/proposal.md` |
| Spec artifact present | ✅ `openspec/changes/test-copy-fix/spec.md` (flat layout; see "Canonical Sync" below) |
| Design artifact present | ✅ `openspec/changes/test-copy-fix/design.md` |
| Tasks artifact present | ✅ `openspec/changes/test-copy-fix/tasks.md` (6/6 complete) |
| Apply-progress artifact present | ✅ `openspec/changes/test-copy-fix/apply-progress.md` |
| Unchecked implementation tasks (`- [ ]`) | ✅ none — Final Task Completion Gate re-read of `tasks.md` confirms 0 matches for `^\s*- \[ \]` |
| Stale-checkbox reconciliation required | ❌ N/A — all 6 task boxes were marked `[x]` by `sdd-apply` and backstopped by apply-progress + verify-report |
| Canonical sync report required | ⚠️ no pre-existing `sync-report.md` — handled by archive-time sync fallback (see "Canonical Sync" below) |
| Archive-time sync fallback approval | ✅ Implicit — parent prompt directs archive; spec §"Output path note" and design §"File changes" pre-authorize treating the flat spec as a new full domain spec at `openspec/specs/agent-detail-ui/spec.md`; verify-report structured status reports `dependencies.sync: ready` and `dependencies.archive: ready`; `nextRecommended: sync (then archive)` |
| Critical verification issues | ✅ none — explicit CRITICAL/BLOCKED/FAIL overrides are not in play |
| Destructive merge approval | ❌ N/A — no MODIFIED/REMOVED requirement sections; this is a new full domain spec |
| Missing-artifact partial-archive approval | ❌ N/A — all required artifacts (proposal/spec/design/tasks/apply-progress/verify-report) are present |
| Production diff | ✅ empty — `git diff -- app/ app/templates app/static app/routers` empty; `tests/conftest.py` empty |
| `actionContext.mode` workspace-planning with no `allowedEditRoots` | ✅ not in play — mode is repo-local |

---

## Artifacts Read

| Artifact | Path |
|----------|------|
| Proposal | `openspec/changes/test-copy-fix/proposal.md` |
| Spec (flat) | `openspec/changes/test-copy-fix/spec.md` |
| Design | `openspec/changes/test-copy-fix/design.md` |
| Tasks | `openspec/changes/test-copy-fix/tasks.md` |
| Apply-progress | `openspec/changes/test-copy-fix/apply-progress.md` |
| Verify report | `openspec/changes/test-copy-fix/verify-report.md` |
| Config | `openspec/config.yaml` |
| Project status / nextRecommended | derived from verify-report structured status (`artifactStore: openspec`, `applyState: all_done`, `dependencies.archive: ready`, `nextRecommended: sync (then archive)`) |

---

## Summary of the Change

**Domain:** Tests / copy drift (renders produced by `app/templates/pages/agent_detail.html`).
**Scope:** Tests-only. Update 3 stale assertions to match the current rendered copy introduced by FU-2 (commits `ffaad16`, `2b92290`).
**Production-code surface touched:** none. `app/`, `app/templates/`, `app/static/`, `app/routers/` all byte-for-byte unchanged.
**`tests/conftest.py`:** unchanged.
**Net test-code diff:** 5 insertions, 3 deletions across 2 files (within the 400-line budget; single-PR boundary; `chain_strategy: size-exception`).

### Files Changed (Commit `e20a34f`)

| File | Action | Summary |
|------|--------|---------|
| `tests/test_pages.py` | Edited | +1 line `agent.x402_supported = True` (flips `profile.hireable=True` so the enabled CTA branch renders); price literal `Hire again for $1.00` → `Hire again for $1.03` (+1, -1). Net: `+2, -1`. |
| `tests/test_pages_x402.py` | Edited | +1 kwarg `x402_supported=wallet is not None` in `_seed_one` helper (keeps `profile.hireable` consistent with `agent_wallet` presence); price literals `Hire for $1.00` → `Hire for $1.03` in two assertions (+2, -2). Net: `+3, -2`. |
| `app/`, `app/templates/`, `app/static/`, `app/routers/` | Unchanged | Production code byte-for-byte unchanged (spec AC-3). |
| `tests/conftest.py` | Unchanged | No session-sharing tweak needed (spec R-1 fallback not invoked). |

### Root Cause (Diagnosed in Design §1/§2)

`app/routers/pages.py:605` computes `profile.hireable = bool(agent.x402_supported and agent.agent_wallet)`. The seeded agents in the failing tests didn't set `x402_supported`, so the column stored `False` (`server_default=text("false")` at `app/db/models/agent.py:113`). With `hireable=False`, the template renders the **disabled** branch (lines 589–595), whose button copy is `Hire for $X.XX` — never `Hire again for $X.XX`, regardless of `my_paid_count`. That is why test 1's body showed `"Hire for $1.00"` even with a PAID row seeded. The proposal's earlier `my_paid_count` DB-session-visibility hypothesis was **not** the actual root cause; the seed needed `x402_supported=True` to unlock the enabled branch.

### Test Results

| Run | Command | Result |
|-----|---------|--------|
| Targeted | `uv run pytest tests/test_pages.py tests/test_pages_x402.py -v` | 34 passed, 0 failed (AC-1) |
| Full | `uv run pytest` | **285 passed, 8 skipped (Postgres-only), 0 failed** in 12.83–14.18s (AC-2; 282 baseline + 3 newly-passing = 285; spec R-3 reconciliation applied) |
| Production diff | `git diff -- app/ app/templates app/static app/routers` | empty (AC-3) |
| Conftest diff | `git diff -- tests/conftest.py` | empty (AC-3 stricter gate) |
| Build | `uv sync --frozen --extra dev` | exit 0 |

**Exit codes:** test `0`, build `0`.
**Output digests:** `test_output_hash sha256:48003c66f0bd5c2c30262bb06824bfeee6468c8ba54ae4e3b4bde8bd70acb973`; `build_output_hash sha256:c6865bf1c10c2a0a700da4781ed323048e57115544d208825ded80c3edc87471`.

### Acceptance Criteria — Final Status

| AC | Description | Status | Evidence |
|----|-------------|--------|----------|
| AC-1 | Targeted suite green | ✅ pass | 34 passed, 0 failed |
| AC-2 | Baseline preserved (no new failures/errors) | ✅ pass | 285 passed (282 baseline + 3 newly-passing), 8 Postgres-only skipped (identical to pre-change baseline) |
| AC-3 | Production diff empty | ✅ pass | `app/`, `app/templates/`, `app/static/`, `app/routers/` empty; `tests/conftest.py` empty |
| AC-4 | Assertions are contiguous substrings of rendered HTML | ✅ pass | All 3 updated `$X.XX` literals match rendered price `$1.03` exactly; no paraphrasing; no settings mutated to mask drift (R-2 satisfied) |

---

## Requirement Coverage Summary

`openspec/changes/test-copy-fix/spec.md` declares 3 requirements (3 `### Requirement` headings) and 6 scenarios (6 `#### Scenario` headings). All are covered by passing tests:

| # | Requirement | Scenarios | Status |
|---|-------------|-----------|--------|
| R1 | Hired-by-you panel + "Hire again" CTA test passes | 2 | ✅ pass |
| R2 | CTA price-and-enabled test passes | 2 | ✅ pass |
| R3 | CTA disabled-without-wallet test passes | 2 | ✅ pass |

**Totals:** 3/3 requirements, 6/6 scenarios, 0 CRITICAL, 0 WARNING, 0 BLOCKER.

### Test-by-Test Pass

| Test | File | Status | Substrings asserted |
|------|------|--------|---------------------|
| `test_agent_detail_shows_hired_panel_and_hire_again` | `tests/test_pages.py` | ✅ PASSED | `Hired by you`, `You hired this agent once`, `Hire again for $1.03`, `view transaction` |
| `test_cta_renders_price_and_is_enabled` | `tests/test_pages_x402.py` | ✅ PASSED | `id="hire-cta"`, `Hire for $1.03`, `"disabled" not in body`, `data-agent-url="https://agent.example.com/chat"`, `data-agent-id="56:0x8004A169FB4a3325136EB29fA0ceB6D2e539a432:1"`, `data-csrf="` |
| `test_cta_disabled_without_wallet` | `tests/test_pages_x402.py` | ✅ PASSED | `id="hire-cta"`, `disabled`, `no payment wallet (payTo) is registered`, `Hire for $1.03` |

---

## Deviations from Design

The design §1/§2 predicted "no string changes needed" assuming `X402_FEE_AMOUNT_USD=0` in the test env. On this branch, `.env` configures `X402_FEE_AMOUNT_USD=0.03` with a non-empty fee wallet (`X402_FEE_WALLET=0x49D8d8a61A7807a8cC78b42A34FA223014518863`), so the rendered price is `$1.03` and three price literals needed to be bumped from `$1.00` to `$1.03`. This is the explicit deviation documented in `apply-progress.md` and anticipated by the design ("If `X402_FEE_AMOUNT_USD=0.03` and a fee wallet get set in conftest later, the price will read `$1.03` and this assertion becomes the one to update"). No settings were mutated (spec R-2 satisfied). AC-4 satisfied (literal substring match).

---

## Canonical Sync

**Status:** ✅ Performed as archive-time sync fallback.

The change spec was written flat at `openspec/changes/test-copy-fix/spec.md` (not under `specs/agent-detail-ui/spec.md`). The spec itself (§"Output path note") and the design (§"File changes") explicitly direct archive to treat this flat layout as a full new domain spec and copy it to `openspec/specs/agent-detail-ui/spec.md`. The parent prompt instructs archive, and the verify-report structured status reports `dependencies.sync: ready` and `dependencies.archive: ready` with `nextRecommended: sync (then archive)` — together constituting implicit approval of archive-time sync fallback.

### Operation applied

**New canonical spec created.** `openspec/specs/agent-detail-ui/` did not exist before this archive (no prior canonical spec for the `agent-detail-ui` domain). The change spec was treated as a full domain spec and copied verbatim to:

```text
openspec/specs/agent-detail-ui/spec.md
```

### ADDED Requirements

The change spec is **new canonical domain content** (no prior `openspec/specs/agent-detail-ui/spec.md` to merge into), so all 3 requirements are effectively `## ADDED Requirements` for the new domain:

| # | Requirement name | Source heading |
|---|------------------|----------------|
| 1 | Hired-by-you panel + "Hire again" CTA test passes | `### Requirement: Hired-by-you panel + "Hire again" CTA test passes` |
| 2 | CTA price-and-enabled test passes | `### Requirement: CTA price-and-enabled test passes` |
| 3 | CTA disabled-without-wallet test passes | `### Requirement: CTA disabled-without-wallet test passes` |

### MODIFIED Requirements

None — no existing canonical requirement blocks were targeted for replacement.

### REMOVED Requirements

None — no destructive removal.

### Active Same-Domain Change Warnings

None. `openspec/changes/indexer-link-fix` (the only other active change under `openspec/changes/`) targets the on-chain indexer domain, not `agent-detail-ui`. No collision risk on the `agent-detail-ui` canonical spec.

### Destructive Merge Guard

Not triggered — only `ADDED` (new canonical domain content). No MODIFIED/REMOVED operations, no replaced blocks, no removed scenarios.

---

## Folder Move

The change folder will be moved from:

```text
openspec/changes/test-copy-fix/
```

to:

```text
openspec/changes/archive/2026-09-15-test-copy-fix/
```

The `openspec/changes/archive/` directory already exists with one prior archive (`2026-09-15-doc-refresh`); the move is additive and preserves the audit trail. All files in the active change folder (proposal.md, spec.md, design.md, tasks.md, apply-progress.md, verify-report.md) plus this archive.md move into the dated archive folder intact.

---

## Structured Status & Action Context Findings

| Field | Value |
|-------|-------|
| `schemaName` | `spec-driven` |
| `changeName` | `test-copy-fix` |
| `artifactStore` | `openspec` (authoritative; `openspec/` directory present and writeable) |
| `proposal` | done |
| `specs` | done (flat at `openspec/changes/test-copy-fix/spec.md`) |
| `design` | done |
| `tasks` | done (6/6 `[x]`) |
| `applyProgress` | done |
| `verifyReport` | done (`verdict: pass`) |
| `taskProgress.total` | 6 |
| `taskProgress.complete` | 6 |
| `taskProgress.remaining` | 0 |
| `taskProgress.unchecked` | `[]` |
| `deferredParentActions.total` | 0 |
| `taskArtifactErrors` | `[]` |
| `applyState` | `all_done` |
| `dependencies.apply` | `all_done` |
| `dependencies.verify` | `all_done` |
| `dependencies.sync` | `ready` → **resolved by archive-time sync fallback** |
| `dependencies.archive` | `ready` → **resolved by this archive** |
| `actionContext.mode` | `repo-local` (not `workspace-planning`; no edit-root gating required) |
| `actionContext.workspaceRoot` | `/home/mario/Documentos/Bnb_agent` |
| `actionContext.allowedEditRoots` | n/a (repo-local mode) |
| `isNonAuthoritative` | `false` |
| `nextRecommended` | (post-archive) terminal for this change |

---

## Risks

- **R-A1 (info):** The canonical spec at `openspec/specs/agent-detail-ui/spec.md` is tests-only behaviour (no production code requirements). It is still appropriate as a canonical spec because it locks down the contract that the three tests must pass against the current rendered copy, and any future template mutation will need to update both production code and these tests (or update the canonical spec with a `## MODIFIED Requirements` delta in a follow-up change).
- **R-A2 (low):** `openspec/specs/agent-detail-ui/` is a fresh canonical domain directory. The next change touching `agent-detail-ui` should be written under `openspec/changes/{next-change}/specs/agent-detail-ui/spec.md` (nested layout) and merged via ADDED/MODIFIED/REMOVED operations into the canonical spec, rather than flat-overwriting the canonical.
- **R-A3 (info):** The 8 Postgres-only skipped tests remain gated on `RUN_POSTGRES_TESTS=1`; this archive does not alter their skip behaviour.

---

## Blockers

**None.** All acceptance criteria met, all tasks complete, production diff empty, baseline preserved (285 passed, 0 failed, 8 Postgres-only skipped), all assertions are real substrings of rendered HTML, single-commit PR boundary matches `single-pr`/`size-exception` strategy, archive-time sync fallback performed per the in-spec and in-design pre-authorization.

---

## Archived Path

```text
openspec/changes/archive/2026-09-15-test-copy-fix/
```

Contents after move: `proposal.md`, `spec.md`, `design.md`, `tasks.md`, `apply-progress.md`, `verify-report.md`, `archive.md` (this file).

Canonical sync target: `openspec/specs/agent-detail-ui/spec.md` (created — new full domain spec).

---

## Key Learnings

1. `git diff -- app/ app/templates app/static app/routers` returning empty after a test-fix change is the strongest available signal that production code stayed byte-for-byte unchanged; pairing it with a stricter `git diff -- tests/conftest.py` empty check tightens AC-3.
2. `profile.hireable` is gated by the conjunction `agent.x402_supported AND agent.agent_wallet` — any test seed that supplies only one column flips the enabled CTA branch off, regardless of any PAID-hire row.
3. Asserting against a contiguous substring of the rendered body, rather than a regex, preserves the test's lock on exact rendered copy and surfaces fee-wallet-driven price drift as a one-line literal update instead of a silent test weakening.
4. Splitting the archive-time sync fallback across the spec and design (the change artifact itself documents and pre-authorizes the canonical copy) is a viable way to honour the "flat spec → canonical spec" path without an explicit `sync-report.md`; structured verify status (`dependencies.sync: ready`) is the gating signal.
5. Counting Postgres-only skips separately from passed/failed is essential for AC-2 reconciliation; raw `passed` count alone would be 285 even on a baseline with no skips, hiding the +3 newly-passing delta.

---

## Key Learnings

1. Counting the +3 newly-passing tests explicitly against the 282-test baseline is the only way to detect silent "test weakening" that would otherwise leave the count unchanged.
2. The flat-spec → canonical-spec archive-time fallback works cleanly when the spec itself documents the intended destination path before archive is invoked.
3. Pairing AC-3's empty production diff with an empty `tests/conftest.py` diff closes the spec R-1 fallback loophole for session-sharing tweaks.
4. Single-PR `size-exception` chain strategy is the right fit for a 5/3 line test-only diff that doesn't justify chained PRs.
5. Skipping `sdd-sync` as a separate phase is acceptable when the change artifact (spec + design) already encodes the canonical merge intent and the verify-report structured status reports `dependencies.sync: ready`.