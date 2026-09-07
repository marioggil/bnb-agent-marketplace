# SDD Archive Report — `x402-remove-fee-multichain`

**Change:** `x402-remove-fee-multichain`
**Phase:** archive
**Artifact store:** `openspec` (authoritative; `openspec/` directory present and writeable)
**Archive date:** `2026-09-15`
**Commit verified:** `uncommitted` (working tree only; archive pre-commit, no `git commit`/`git push` performed)
**Workspace:** `/home/mario/Documentos/Bnb_agent`
**Branch:** `feat/x402-multichain-no-fee` (current)

---

## Status: PASS ✅

All phases completed: init → proposal → spec → design → tasks → apply → verify (round 1) → archive. **Single-PR change** (not a chain — delivered on one feature-branch terminal state). Round-1 verify verdict **pass**: 10/10 requirements, 16/16 scenarios, 7/7 ACs, **456 passed / 9 skipped / 0 failed**, scope guard 0 on all forbidden surfaces, blockers 0, critical_findings 0. All 38 task checkboxes `[x]`, 0 unchecked. Ready for canonical merge and folder move.

---

## Archive Preconditions — All Satisfied

| Check | Result |
|-------|--------|
| Verify report present + verdict `pass` | ✅ `openspec/changes/x402-remove-fee-multichain/verify-report.md` (verdict `pass`, blockers `0`, critical `0`, no `FAIL`/`BLOCKED`/`CRITICAL`; round-1 FINAL on disk) |
| Proposal / Spec (flat) / Design / Tasks / Apply-progress / Verify-report artifacts | ✅ all 6 present in the active change dir |
| Explore artifact | ⚠️ absent for this change — none was produced in prior phases; not a required archive precondition artifact (explore is not among the mandatory proposal/spec/design/tasks/verify-reads) |
| Final Task Completion Gate (re-read `tasks.md`) | ✅ `grep -c '^\s*- \[ \]'` = `0`; `grep -c '^\s*- \[x\]'` = `38`; all 38 rows are `sdd-owner: implementation` markers (18 task rows each with its RED/GREEN/TRIANGULATE marker + an "Implement and verify the behavior" row) |
| Stale-checkbox reconciliation | ❌ N/A — all 38 boxes marked `[x]` by `sdd-apply`, backstopped by apply-progress + round-1 verify-report |
| Canonical sync (`sync-report.md`) | ⚠️ no prior `sync-report.md` — handled by archive-time sync fallback (see "Canonical Sync"); parent delegated phase prompt explicitly instructs: "Create canonical spec at `openspec/specs/x402-remove-fee-multichain/spec.md` (copy from flat spec — new domain)" |
| Destructive merge approval | ❌ N/A — new full domain spec (single ADDED, no MODIFIED/REMOVED operations) |
| Critical verification issues | ✅ none (round-1 verdict pass; blockers 0, critical_findings 0) |
| Scope guard — forbidden surfaces | ✅ `git diff master..HEAD -- app/db/models/agent.py app/db/models/hired_agent.py app/services/wallet_activity.py app/services/compliance_refresh.py app/services/agent_score.py migrations/` returns **0** lines; worktree form (`git diff HEAD`) also 0 (round-1 verify-report) |
| Baseline | ✅ **456 passed, 9 skipped, 0 failed** (branch point 446; net +10) |
| `actionContext.mode` workspace-planning with no `allowedEditRoots` | ✅ not in play — repo-local mode |

---

## Artifacts Read

| Artifact | Path |
|----------|------|
| Proposal | `openspec/changes/x402-remove-fee-multichain/proposal.md` |
| Spec (flat) | `openspec/changes/x402-remove-fee-multichain/spec.md` (10 requirements, 16 scenarios) |
| Design | `openspec/changes/x402-remove-fee-multichain/design.md` |
| Tasks | `openspec/changes/x402-remove-fee-multichain/tasks.md` |
| Apply-progress | `openspec/changes/x402-remove-fee-multichain/apply-progress.md` |
| Verify report | `openspec/changes/x402-remove-fee-multichain/verify-report.md` (round-1 FINAL) |
| Explore | **absent** for this change (no `explore.md` was produced in prior phases) |
| Sync report | `openspec/changes/x402-remove-fee-multichain/sync-report.md` — **absent** (no prior `sdd-sync` run; archive-time sync fallback parent-approved) |
| Config | `openspec/config.yaml` (no `rules.archive` section) |
| Format reference | `openspec/changes/archive/2026-09-15-x402-agent-hire/archive.md` |
| Status contract | `~/.pi/agent/gentle-ai/support/sdd-status-contract.md` (no project override `.pi/gentle-ai/support/sdd-status-contract.md`) |

---

## Summary of the Change

**Domain:** `x402-remove-fee-multichain` — new canonical domain (payments / agent hire). The change removes the marketplace fee end-to-end and settles on the agent's EVM chain via a static rail map, exactly as specified.

**Architecture:** The marketplace fee (model-A commission `accepts[1]` to `X402_FEE_WALLET`) is removed from `create_hire`/`build_challenge`/`payment.js`/`pay_hire` — the agent's quoted price is the price. Settlement becomes multi-chain EVM through a static rail map (`X402_RAIL_MAP` in `app/config.py`) mapping `chain_id → {rpc_url, token_address, token_name, token_version}` for Base 8453, Polygon 137, Avalanche 43114 (all USDC) plus the existing `$U` chains BSC 56 / testnet 97. `get_token_config(settings, chain_id)` resolves per chain and raises `UnknownRail` for unknown chains. `create_hire` probes the agent offer at hire time and uses its real `accepts[0]` (amount/payTo/asset/network), recording evidence columns; `pay_hire` derives the chain from `hire.network_agent` and verifies + broadcasts exactly once through the rail-map RPC, ignoring any legacy `decoded.fee`. `payment.js` signs one authorization with no `payload.fee`. `decode_envelope` retains legacy `payload.fee` back-compat.

---

## Canonical Sync

**Status:** ✅ Performed as archive-time sync fallback (parent-delegated phase prompt explicitly instructs the copy; no separate `sdd-sync` run exists, and no `sync-report.md` is on disk).

### Operation Applied

**New canonical spec created.** `openspec/specs/x402-remove-fee-multichain/` did not exist before this archive (prior `openspec/specs/` contained `agent-compliance`, `agent-detail-ui`, `branding`, `onchain-indexer`, `wallet-activity`, `x402-agent-hire`). The change spec — written flat at `openspec/changes/x402-remove-fee-multichain/spec.md` — is treated as a full new domain spec and copied **byte-identical** (MD5 verified on both sides) to:

```text
openspec/specs/x402-remove-fee-multichain/spec.md
```

### ADDED Requirements (10 — full new domain content)

| # | Requirement name |
|---|------------------|
| 1 | R1: Static rail map covers the three USDC EVM chains plus the $U chains |
| 2 | R2: get_token_config returns the rail-map token for the quoted chain |
| 3 | R3: get_token_config raises on an unknown chain |
| 4 | R4: create_hire charges the offer's accepts[0] and records evidence |
| 5 | R5: The hire challenge contains exactly one accept — no fee accept |
| 6 | R6: pay_hire verifies and broadcasts on the hire's chain via the rail map, with no fee |
| 7 | R7: payment.js signs one authorization with no fee payload |
| 8 | R8: Hire-offer UI enables rail-map EVM chains; "not available" otherwise |
| 9 | R9: decode_envelope keeps parsing legacy payload.fee |
| 10 | R10: Full suite green under uv run pytest |

### MODIFIED / REMOVED Requirements

None — no existing canonical requirement blocks targeted for replacement; no destructive removal.

### Active Same-Domain Change Warnings

None. `x402-remove-fee-multichain` is the only active change touching this new payments domain. Other active changes: `score-integration` (domain `agent-detail-ui` EXTENSION — references hire/CTA text but is a separate canonical domain and does not overlap this new domain) and `chore-close-stale-changes` (SDD hygiene). Neither overlaps the new `x402-remove-fee-multichain` canonical domain.

### Destructive Merge Guard

Not triggered — single ADDED operation only. The full spec content is copied verbatim (MD5 verified); every `### R{n}:` requirement and scenario heading preserved exactly as written.

---

## Single-PR Closure + Size Exception

This change is a **single-PR change**. `tasks.md`'s Review Workload Forecast recommended 3 chained PRs (`feature-branch-chain`; rail map → backend fee removal → JS/UI) due to the 400-line-budget risk, and a `size:exception` was recorded (~895 produced vs 400 budget). All T1–T19 were nonetheless delivered on the single `feat/x402-multichain-no-fee` branch terminal state; round-1 verify-report confirms "no scope creep beyond assigned tasks; protected modules 0-diff. Per-PR chain boundaries not separately evidenced at terminal verify (transparency only)." No `x402-remove-fee-multichain-1a/1b/…` sub-change directories exist, so the umbrella directory is moved **in full** (6 original artifacts + this `archive.md` = 7 files).

**Size note:** ~895 changed lines (560 insertions + 335 deletions) across **8 production files + 6 test files** — `size:exception accepted` recorded in `apply-progress.md` ("~850 produced vs 400 budget — user-approved multi-PR scope, done as one branch"). Growth from test density + rail-map internals; code/tests not compressed to fit the budget.

**Fee removal:** fee removed end-to-end, not bypassed — no `feeAccept`, no `payload.fee`, no "marketplace fee" UI text, no fee params on `build_challenge`, no fee verify/broadcast in `pay_hire` (verify-report grep-ingress: zero matches across hire flow files). Deprecated config knobs (`x402_fee_*`, `x402_default_price_usd`) retained per T19 scope guard, no longer consulted.

**Multi-chain rail:** static `X402_RAIL_MAP` (5 chains), `get_token_config` per chain, `create_hire` offer-driven, `pay_hire` chain-aware via `hire.network_agent`, `payment.js` single auth with `domain.chainId` from the accept network, UI enabled for any rail-map EVM chain.

**Verify round history:** round-1 only — fresh verification with no prior verify-report existed; verdict **pass** (blockers 0, critical 0).

---

## Verify Warnings (6 — non-blocking, documentation/hygiene)

1. No formal `TDD Cycle Evidence` table in `apply-progress.md` (strict-TDD protocol not formally activated; tasks.md markers + green execution substitute).
2. T15's named `test_is_supported_offer_multi_chain` unit test absent from `test_x402_client.py`; behavior covered by endpoint tests + pre-existing unit test (all green).
3. Stale comment in `app/static/js/payment.js` above `signPayment` still describes the removed fee-signing block — cosmetic only; code signs one authorization.
4. Dead leftover assignment `fee = settings.x402_fee_amount_usd ...` in `pages.py::_build_hire_offer` (computed, unused; `total` ignores it, `fee_usd=None`) — cosmetic.
5. Untracked `NuevosCambios/` directory at repo root contains unrelated user content (logos, backup logs); not part of this change and not touched by it — noted for hygiene only.
6. Deployment note (design risk R-1, carried): USDC EIP-712 name/version "USD Coin"/"2" is frozen by the fixture test; a live-contract mismatch would fail signature recovery visibly (403), never silently.

All six are documentation/hygiene-level with **zero** blockers and **zero** CRITICAL findings.

---

## Stats

- **Tests:** 10 new per `apply-progress.md` §Test evidence (baseline 446 → 456); focused group re-run green. New/extended suites: `test_payment_rail_map.py` (new), `test_api_hires.py`, `test_api_hires_pay.py`, `test_payment.py`, `test_hire_offer_endpoint.py`, `test_config_x402.py`, `test_pages.py`.
- **Migration:** none — evidence columns already existed from `x402-agent-hire` (0013).
- **Size:** ~895 changed lines vs the 400-line budget — `size:exception accepted` recorded in `apply-progress.md` §Status.
- **Scope guard:** 0 lines diff on `agent.py`, `hired_agent.py`, `wallet_activity.py`, `compliance_refresh.py`, `agent_score.py`, `migrations/` (both `master..HEAD` and worktree forms).

---

## Structured Status & Action Context

| Field | Value |
|-------|-------|
| `changeName` | `x402-remove-fee-multichain` |
| `artifactStore` | `openspec` (authoritative; `openspec/` writeable) |
| `proposal` / `specs` / `design` / `tasks` / `applyProgress` / `verifyReport` | all done |
| `taskProgress.total` / `complete` / `remaining` | `38` / `38` / `0` |
| `taskProgress.unchecked` | `[]` |
| `applyState` | `all_done` |
| `dependencies.sync` | `ready` → resolved by archive-time sync fallback |
| `dependencies.archive` | `ready` → resolved by this archive |
| `dependencies.apply` / `verify` | `all_done` |
| `actionContext.mode` | repo-local (no `workspace-planning`) |
| `actionContext.workspaceRoot` | `/home/mario/Documentos/Bnb_agent` |
| `actionContext.allowedEditRoots` | `openspec/changes/x402-remove-fee-multichain/`, `openspec/changes/archive/2026-09-15-x402-remove-fee-multichain/`, `openspec/specs/x402-remove-fee-multichain/` (openspec-only edits) |
| `isNonAuthoritative` | `false` |
| `nextRecommended` | (post-archive) `orchestrator-commit` |

---

## Risks

- **R-A1 (info):** New canonical domain. Future changes touching `x402-remove-fee-multichain`/payments should be written under `openspec/changes/{next-change}/specs/x402-remove-fee-multichain/spec.md` (nested) and merged via ADDED/MODIFIED/REMOVED ops.
- **R-A2 (info):** Per-PR chain boundaries (3 recommended PRs) were not separately evidenced at terminal verify — transparency only; single-branch delivery verified end-to-end (456 pass).
- **R-A3 (info):** Static public RPCs for 3 new chains (Base/Polygon/Avalanche) — per-chain timeout; failed broadcast flips hire to failed via existing `BroadcastFailed` path (design risk R-4).
- **R-A4 (info):** 9 Postgres-only skipped tests remain gated on `RUN_POSTGRES_TESTS=1`; archive does not alter skip behaviour.
- **R-A5 (info):** Commit state is **pre-commit**; archive did NOT commit, push, or modify production code. Orchestrator owns the `git add` + `git commit` + `git push` for the PR.

---

## Blockers

**None.** Round-1 verify verdict pass (7/7 ACs, 10/10 reqs, 16/16 scenarios, 456 passed / 9 skipped / 0 failed, scope guards 0, blockers 0, critical 0); all 38 task boxes `[x]`, 0 unchecked; Final Task Completion Gate re-read before sync fallback confirmed 0 `- [ ]` lines; archive-time sync fallback explicitly approved by the parent delegated phase prompt (copy flat spec to a new canonical domain — no destructive merge); single-PR folder move in full.

---

## Archived Path

```text
openspec/changes/archive/2026-09-15-x402-remove-fee-multichain/
```

**Contents after move (7 files):** `proposal.md`, `spec.md`, `design.md`, `tasks.md`, `apply-progress.md`, `verify-report.md`, `archive.md` (this file). No `explore.md` was produced for this change, so it is not present (7 files, not 8).

**Canonical sync target:** `openspec/specs/x402-remove-fee-multichain/spec.md` (created — new full domain spec, byte-identical copy of the flat spec, MD5 verified).

**Active change directory** `openspec/changes/x402-remove-fee-multichain/` is removed after move.

---

## Next Recommended

`orchestrator-commit` — archive hands back to orchestrator for `git add` + `git commit` on `feat/x402-multichain-no-fee` + `git push` to open the PR. Archive itself does NOT commit, push, or modify production code per parent-task constraint.

---

## Key Learnings

1. The Final Task Completion Gate re-read of the persisted `tasks.md` immediately before sync fallback and folder move is the load-bearing archive-readiness check — 0 `- [ ]` lines and 38/38 `[x]` matching 38 `sdd-owner: implementation` markers confirmed no silent apply-phase checkbox regression.
2. A verify verdict alone does not prove archive readiness; the round-1 `pass` became trustworthy only after the report itself documented every requirement/scenario/AC evidence source and the zero-blocker status.
3. For a flat-spec single-PR change into a brand-new canonical domain, archive-time sync fallback is a plain byte-identical `cp` (MD5-verified on both sides) with no merge semantics, provided the parent prompt explicitly authorizes it.
4. An `explore.md` may legitimately be absent from an active change without blocking archive, since it is not among the mandatory proposal/spec/design/tasks/verify archive preconditions — the archive report should note its absence explicitly rather than fabricate it.
5. Scope-guard proof via `git diff master..HEAD -- <forbidden-paths>` returning 0 lines, cross-checked in worktree form, confirms production code stayed byte-identical on protected compliance/score/migration surfaces throughout the change.
