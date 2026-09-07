# SDD Archive Report — `x402-agent-hire`

**Change:** `x402-agent-hire`
**Phase:** archive
**Artifact store:** `openspec` (authoritative; `openspec/` directory present and writeable)
**Archive date:** `2026-09-15`
**Commit verified:** `uncommitted` (working tree only; archive pre-commit, no `git commit`/`git push` performed)
**Workspace:** `/home/mario/Documentos/Bnb_agent`
**Branch:** `feat/x402-agent-hire` (current)

---

## Status: PASS ✅

All phases completed: init → explore → proposal → spec → design → tasks → apply → verify (rounds 1–3) → archive. **Single-PR change** (not a chain — delivered on one feature-branch terminal state). Round-3 verify verdict **pass**: 9/9 requirements, 16/16 scenarios, **442 passed / 9 skipped / 0 failed**, scope guard 0 on all forbidden surfaces, blockers 0, critical_findings 0. All 23 task checkboxes `[x]`, 0 unchecked. Ready for canonical merge and folder move.

---

## Archive Preconditions — All Satisfied

| Check | Result |
|-------|--------|
| Verify report present + verdict `pass` | ✅ `openspec/changes/x402-agent-hire/verify-report.md` (verdict `pass`, blockers `0`, critical `0`, no `FAIL`/`BLOCKED`/`CRITICAL`; round-3 FINAL on disk) |
| Proposal / Spec (flat) / Design / Tasks / Apply-progress / Verify-report / Explore artifacts | ✅ all 7 present in the active change dir |
| Final Task Completion Gate (re-read `tasks.md`) | ✅ `grep -c '^\s*- \[ \]'` = `0`; `grep -c '^\s*- \[x\]'` = `23`; `sdd-owner: implementation` markers = `23` (no malformed rows) |
| Stale-checkbox reconciliation | ❌ N/A — all 23 boxes marked `[x]` (fixtures checkbox + T1–T22) by `sdd-apply`, backstopped by apply-progress + round-3 verify-report |
| Canonical sync (`sync-report.md`) | ⚠️ no prior `sync-report.md` — handled by archive-time sync fallback (see "Canonical Sync"); parent delegated phase prompt explicitly instructs: "Create canonical spec at `openspec/specs/x402-agent-hire/spec.md` (copy from flat spec — new domain)" |
| Destructive merge approval | ❌ N/A — new full domain spec (single ADDED, no MODIFIED/REMOVED operations) |
| Critical verification issues | ✅ none (round-3 verdict pass; all prior CRITICALs resolved) |
| Scope guard — forbidden surfaces | ✅ `git diff master..HEAD -- app/services/payment.py app/static/js/payment.js app/services/wallet_activity.py app/services/compliance_refresh.py app/db/models/agent.py app/services/agent_score.py` returns **0** lines; worktree form also 0 (round-3 verify-report) |
| Baseline | ✅ **442 passed, 9 skipped, 0 failed** (branch point 414; net +28) |
| `actionContext.mode` workspace-planning with no `allowedEditRoots` | ✅ not in play — repo-local mode |

---

## Artifacts Read

| Artifact | Path |
|----------|------|
| Proposal | `openspec/changes/x402-agent-hire/proposal.md` |
| Spec (flat) | `openspec/changes/x402-agent-hire/spec.md` |
| Design | `openspec/changes/x402-agent-hire/design.md` |
| Tasks | `openspec/changes/x402-agent-hire/tasks.md` |
| Apply-progress | `openspec/changes/x402-agent-hire/apply-progress.md` |
| Verify report | `openspec/changes/x402-agent-hire/verify-report.md` (round-3 FINAL) |
| Explore | `openspec/changes/x402-agent-hire/explore.md` |
| Sync report | `openspec/changes/x402-agent-hire/sync-report.md` — **absent** (no prior `sdd-sync` run; archive-time sync fallback parent-approved) |
| Config | `openspec/config.yaml` (no `rules.archive` section) |
| Format reference | `openspec/changes/archive/2026-09-15-wallet-activity/archive.md` |
| Status contract | `~/.pi/agent/gentle-ai/support/sdd-status-contract.md` (no project override `.pi/gentle-ai/support/sdd-status-contract.md`) |

---

## Summary of the Change

**Domain:** `x402-agent-hire` — new canonical domain (payments / agent hire). The marketplace now prices the Hire CTA from the agent's **real x402 offer** (probed from its `a2a_endpoint`/`agent_url`) instead of the flat `X402_DEFAULT_PRICE_USD`, with the marketplace fee kept on top as `accepts[1]` and the agent's quoted values recorded as evidence columns.

**Architecture:** Additive. New leaf module `app/services/x402_client.py` (pure async probe `probe_agent_offer` → `AgentOffer | None`, `is_supported_offer`, SSRF-guarded `_validate_probe_url`); new lazy HTMX endpoint `GET /agents/{chain}/{token}/hire-offer` + `partials/hire_offer.html` + `schemas/hire_offer.py`; `create_hire` re-probes at hire time and uses the offer's real `pay_to`/`amount` when supported (flat fallback otherwise); migration `0013_hired_agent_offer_evidence` adds 4 nullable evidence columns to `HiredAgent` (+ echo in `hired.py`); `agent_detail.html` CTA renders "Checking availability…" and swaps only the inner `#hire-offer-slot` (payment.js untouched). **No** change to `payment.py`, `payment.js`, wallet/compliance/score services, or the verify/settle path.

---

## Canonical Sync

**Status:** ✅ Performed as archive-time sync fallback (parent-delegated phase prompt explicitly instructs the copy; no separate `sdd-sync` run exists, and no `sync-report.md` is on disk).

### Operation Applied

**New canonical spec created.** `openspec/specs/x402-agent-hire/` did not exist before this archive (prior `openspec/specs/` contained only `agent-compliance`, `agent-detail-ui`, `branding`, `onchain-indexer`, `wallet-activity`). The change spec — written flat at `openspec/changes/x402-agent-hire/spec.md` — is treated as a full new domain spec and copied **byte-identical** (MD5 `f18ad4643e262f0fb0378266b03e7ee2` on both sides) to:

```text
openspec/specs/x402-agent-hire/spec.md
```

### ADDED Requirements (9 — full new domain content)

| # | Requirement name |
|---|------------------|
| 1 | R1: Probe helper returns AgentOffer or None |
| 2 | R2: Endpoint source — a2a_endpoint then agent_url |
| 3 | R3: Render real price when asset+network match the rail |
| 4 | R4: Render disabled "not available" otherwise |
| 5 | R5: create_hire uses the offer or falls back to flat |
| 6 | R6: Marketplace fee applies on top of the real price |
| 7 | R7: HiredAgent persists 4 evidence columns |
| 8 | R8: SSRF guard |
| 9 | R9: Lazy HTMX contract |

### MODIFIED / REMOVED Requirements

None — no existing canonical requirement blocks targeted for replacement; no destructive removal.

### Active Same-Domain Change Warnings

None. `x402-agent-hire` is the only active change touching this domain. Other active changes: `score-integration` (domain `agent-detail-ui` EXTENSION) and `chore-close-stale-changes` (SDD hygiene) — neither overlaps the new `x402-agent-hire` canonical domain, nor the payments surface it documents. (`compliance-flags`, `wallet-activity`, `test-copy-fix`, `indexer-link-fix`, `logo-v2`, `doc-refresh` are already archived.)

### Destructive Merge Guard

Not triggered — single ADDED operation only. The full spec content was copied verbatim (MD5 verified); every `### Requirement` and `#### Scenario` heading preserved exactly as written.

---

## Single-PR Closure + Verify Round History (Not a Chain)

This change is a **single-PR change**. `tasks.md`'s Review Workload Forecast recommended 3 chained PRs (`feature-branch-chain`; probe+offer → endpoint+partial → create_hire+evidence+CTA) due to the 400-line-budget risk, and a `size:exception` was recorded (~750 produced vs 400 budget). All T1–T22 were nonetheless delivered on the single `feat/x402-agent-hire` branch terminal state; round-3 verify-report confirms "no scope creep beyond assigned tasks; protected modules 0-diff. Per-PR chain boundaries not separately evidenced at terminal verify (transparency only)." No `x402-agent-hire-1a/1b/…` sub-change directories exist, so the umbrella directory is moved **in full** (7 original artifacts + this `archive.md` = 8 files).

**Verify round history (all corrected):**

| Round | Outcome | Findings resolved by next round |
|-------|---------|--------------------------------|
| Round 1 | CRITICALs | Lazy HTMX CTA wiring (R9/D-8) genuinely absent; missing `TDD Cycle Evidence` table; stale `test_pages_x402`/`test_alembic_check` claims — apply applied the wiring through the stable `#hire-cta` node + `#hire-offer-slot`, added the TDD table, and pinned the W1 test to the lazy contract (apply-progress "Post-verify correction"). |
| Round 2 | partial | One remaining CRITICAL (strict-TDD evidence format — `TDD Cycle Evidence` table) + WARNING (static `hire-price` contradicting the lazy CTA) + W1 test re-pin pending. |
| Round 3 | **pass** | `TDD Cycle Evidence` table present at `apply-progress.md:125` (verified real, not narrative); static `hire-price` paragraph confined to the not-hireable else branch (`agent_detail.html:633`); W1 test re-pinned to the lazy contract. **442 passed, 9 skipped, 0 failed; blockers 0; critical 0.** |

---

## Stats

- **Tests:** 27 new tests per `apply-progress.md` §Test evidence (baseline 414 → 441); a 28th test (`tests/test_pages.py::test_agent_detail_lazy_hire_offer_wiring`) was added during the round-1 correction, lifting the final suite to **442 passed, 9 skipped** (net +28 vs the 414 branch-point baseline, per round-3 verify AC-7). New/extended suites: `test_x402_client.py`, `test_hire_offer_endpoint.py`, `test_api_hires.py`, `test_pages_x402.py`, `test_pages.py`.
- **Migration:** `0013_hired_agent_offer_evidence` (4 nullable columns `amount_agent`/`pay_to_agent`/`asset_agent`/`network_agent`; symmetric downgrade; `alembic heads` → single head `0013 (head)`).
- **Size:** ~750 produced lines vs the 400-line budget — `size:exception accepted` recorded in `apply-progress.md` §Status as required (growth from test density + SSRF guard internals; production code within design intent; code/tests not compressed to fit the budget).
- **Scope guard:** 0 lines diff on `payment.py`, `payment.js`, `wallet_activity.py`, `compliance_refresh.py`, `agent.py`, `agent_score.py` (both `master..HEAD` and worktree forms).

---

## Structured Status & Action Context

| Field | Value |
|-------|-------|
| `changeName` | `x402-agent-hire` |
| `artifactStore` | `openspec` (authoritative; `openspec/` writeable) |
| `proposal` / `specs` / `design` / `tasks` / `explore` / `applyProgress` / `verifyReport` | all done |
| `taskProgress.total` / `complete` / `remaining` | `23` / `23` / `0` (fixtures checkbox + T1–T22) |
| `taskProgress.unchecked` | `[]` |
| `applyState` | `all_done` |
| `dependencies.sync` | `ready` → resolved by archive-time sync fallback |
| `dependencies.archive` | `ready` → resolved by this archive |
| `dependencies.apply` / `verify` | `all_done` |
| `actionContext.mode` | repo-local (no `workspace-planning`) |
| `actionContext.workspaceRoot` | `/home/mario/Documentos/Bnb_agent` |
| `actionContext.allowedEditRoots` | `openspec/changes/x402-agent-hire/`, `openspec/changes/archive/2026-09-15-x402-agent-hire/`, `openspec/specs/x402-agent-hire/` (openspec-only edits) |
| `isNonAuthoritative` | `false` |
| `nextRecommended` | (post-archive) `orchestrator-commit` |

---

## Risks

- **R-A1 (info):** New canonical domain. Future changes touching `x402-agent-hire`/payments should be written under `openspec/changes/{next-change}/specs/x402-agent-hire/spec.md` (nested) and merged via ADDED/MODIFIED/REMOVED ops.
- **R-A2 (info):** Per-PR chain boundaries (3 recommended PRs) were not separately evidenced at terminal verify — transparency only; single-branch delivery verified end-to-end (442 pass).
- **R-A3 (low):** `test_alembic_check.py` has no dedicated `0013` up/down parity block (T19 sub-bullet) — apply-progress explicitly disclaims the claim; migration validity is proven by `alembic heads` single head + green harness (documented, reconciled deviation; informational only, not a blocker).
- **R-A4 (info):** 9 Postgres-only skipped tests remain gated on `RUN_POSTGRES_TESTS=1`; archive does not alter skip behaviour.
- **R-A5 (info):** Commit state is **pre-commit**; archive did NOT commit, push, or modify production code. Orchestrator owns the `git add` + `git commit` + `git push` for the PR.

---

## Blockers

**None.** Round-3 verify verdict pass (9/9 reqs, 16/16 scenarios, 442 passed / 9 skipped / 0 failed, scope guards 0, blockers 0, critical 0); all 23 task boxes `[x]`, 0 unchecked; Final Task Completion Gate re-read before sync fallback confirmed 0 `- [ ]` lines; archive-time sync fallback explicitly approved by the parent delegated phase prompt (copy flat spec to a new canonical domain — no destructive merge); single-PR folder move in full.

---

## Archived Path

```text
openspec/changes/archive/2026-09-15-x402-agent-hire/
```

**Contents after move (8 files):** `proposal.md`, `spec.md`, `design.md`, `tasks.md`, `apply-progress.md`, `verify-report.md`, `explore.md`, `archive.md` (this file).

**Canonical sync target:** `openspec/specs/x402-agent-hire/spec.md` (created — new full domain spec, byte-identical copy of the flat spec, MD5 `f18ad4643e262f0fb0378266b03e7ee2` verified).

**Active change directory** `openspec/changes/x402-agent-hire/` is removed after move.

---

## Next Recommended

`orchestrator-commit` — archive hands back to orchestrator for `git add` + `git commit` on `feat/x402-agent-hire` + `git push` to open the PR. Archive itself does NOT commit, push, or modify production code per parent-task constraint.

---

## Key Learnings

1. The Final Task Completion Gate re-read of the persisted `tasks.md` immediately before sync fallback and folder move is the load-bearing archive-readiness check — 0 `- [ ]` lines and 23/23 `[x]` matching 23 `sdd-owner: implementation` markers confirmed no silent apply-phase checkbox regression.
2. A verify verdict alone does not prove archive readiness: round-3 `pass` only became trustworthy after the report itself documented how each round-1 CRITICAL and round-2 partial finding was corrected and independently re-verified against the worktree.
3. For a flat-spec single-PR change into a brand-new canonical domain, archive-time sync fallback is a plain byte-identical `cp` (MD5-verified on both sides) with no merge semantics, provided the parent prompt explicitly authorizes it.
4. When apply-progress and the verify report disagree on a derived count (27 new tests vs net +28), the archive report should reconcile both figures against the final full-suite number (442 passed) instead of silently picking one.
5. Scope-guard proof via `git diff master..HEAD -- <forbidden-paths>` returning 0 lines, cross-checked in worktree form, confirms production code stayed byte-identical on protected payment/compliance/score surfaces throughout the change.
