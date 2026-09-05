# Tasks: indexer-link-fix

> Ordered implementation checklist for `indexer-link-fix`.
> Source spec: `openspec/changes/indexer-link-fix/spec.md` ·
> Source design: `openspec/changes/indexer-link-fix/design.md` ·
> Source proposal: `openspec/changes/indexer-link-fix/proposal.md`.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~650–900 (3 new files + 1 edit; bulk is tests) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 → WU1 + WU2 + WU4 (diagnostic + linking fix + realtime verify) · PR 2 → WU3 + WU5 (backfill script + test suite) |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

```text
Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High
```

> **Why chained:** WU3 (backfill script) and WU5 (test suite) can land independently of WU2's runtime behavior once `_canon_addr` / `resolve_wallet_to_agent` exist. WU1 must land first regardless — its output gates WU2's scope (canonicalization vs. wrong-column vs. structural). PR-1 carries the runtime contract change; PR-2 carries the operator-facing script + tests without expanding the runtime surface.

---

## WU1 — Diagnostic (gates WU2 / WU3 / WU4)

> One-shot CLI that prints overlap counts against the four candidate wallet columns and identifies the dominant root cause from REQ-001. Output is the gate for every later WU.

- [x] Scaffold `scripts/diag_linking.py` with `argparse` (default `DATABASE_URL` from `app.config.get_settings()`), a `main()` that opens an `AsyncSession` via `app.db.session.get_sessionmaker()`, and an `if __name__ == "__main__": asyncio.run(main())` entry. Module must be runnable as `python -m scripts.diag_linking`. <!-- sdd-owner: implementation -->
- [x] Implement the four-column overlap probe against `agent_cache`: `agent_wallet`, `creator_address`, `owner_address`, `contract_address`. For a sample of 100 recent `onchain_transfers` rows (`ORDER BY id DESC LIMIT 100`), print per-column lowercased match counts. <!-- sdd-owner: implementation -->
- [x] Implement the REQ-001 candidate counters: (a) case-only mismatch count (raw byte-for-byte equals but `.lower()` differs), (b) embedded whitespace + null-byte count on `to_address`, (c) `0x`-prefix drift count, (d) length != 42 count. Print one line per counter. <!-- sdd-owner: implementation -->
- [x] Implement the "wrong-column" probe: for the same 100-row sample, additionally try matching `to_address` (lowercased) against the lowercased full set of each of `creator_address`, `owner_address`, `contract_address`. Print a winner column if any of them yields non-zero overlap. <!-- sdd-owner: implementation -->
- [x] Print a `decision:` line at the end with one of `case | whitespace | wrong-column | structural` based on which counter / column produced the largest non-zero match. Script exits 0 unconditionally. <!-- sdd-owner: implementation -->
- [x] Run the diagnostic against the staging or production-replica database and capture stdout into `openspec/changes/indexer-link-fix/notes/diag-YYYYMMDD.md` (use the date the script ran). Commit the note. <!-- sdd-owner: implementation -->
- [x] Record the dominant root cause decision in the note (single sentence + which counter/column drove it). If the decision is `structural` (zero overlap on every column), STOP and escalate before starting WU2 — per design §3.1, this triggers a sync-worker follow-up that is out of scope for this change. <!-- sdd-owner: implementation -->

---

## WU2 — Fix linking logic (gated by WU1)

> Introduce one canonicalization helper + one public mapping function so realtime and backfill cannot drift. Three callsites in `app/services/onchain_indexer.py` get refactored to share them.

- [x] Add `_canon_addr(addr: str | None) -> str | None` to `app/services/onchain_indexer.py` per design §3.2: strip whitespace, drop optional `0x`, lowercase, validate 40 hex chars, return `"0x" + s` or `None` for unparseable. Place it next to `_extract_addr` near line 83. <!-- sdd-owner: implementation -->
- [x] Add `resolve_wallet_to_agent(session: AsyncSession) -> dict[str, str]` to `app/services/onchain_indexer.py` as a public coroutine: returns `{_canon_addr(row[0]): row[1] for row in result.all()}` over `select(AgentCache.agent_wallet, AgentCache.agent_id).where(AgentCache.agent_wallet.isnot(None))`. Docstring notes it replaces the private helper so the backfill script can re-import. <!-- sdd-owner: implementation -->
- [x] Refactor the existing private `_resolve_agent_wallets(session)` (line 227) to delegate to `resolve_wallet_to_agent`, or inline its body into the new function and delete the private alias. Keep both old call sites (lines 554, 657) working without behavior change. <!-- sdd-owner: implementation -->
- [x] Refactor `_scan_and_store()` at `app/services/onchain_indexer.py:269` so the `linked_agent` lookup uses `_canon_addr(to_addr)` instead of `to_addr.lower()`. The `to_addr` itself is still produced by `_extract_addr` (deterministic `0x+40hex`), so this is a strict-superset safety net. <!-- sdd-owner: implementation -->
- [x] Refactor `_scan_and_store_direct()` at `app/services/onchain_indexer.py:381` symmetrically — same `_canon_addr(to_addr)` swap. Both paths must now route through the shared canonicalization; grep `to_addr.lower()` afterward to confirm only `_extract_addr`'s internals and the new helper itself remain. <!-- sdd-owner: implementation -->
- [x] Preserve existing linkages: the `pg_insert(...).on_conflict_do_nothing()` call already protects the UNIQUE index; no further change needed in the realtime path. Verify with `git diff` that the only behavioral edits are the `.lower()` → `_canon_addr()` swaps. <!-- sdd-owner: implementation -->

---

## WU3 — Backfill script

> Standalone CLI for the 686K-row historical corpus. Chunked, idempotent, exits 2 on stall. Designed to be runnable both standalone (`python -m scripts.backfill_link_agents`) and re-imported by the optional HTTP adapter.

- [ ] Create `scripts/backfill_link_agents.py` as a new module with `argparse` flags `--chunk` (default 10000, max 100000), `--dry-run` (count only), `--limit` (max total rows processed). Provide an `async run(args) -> int` entry point and a CLI wrapper that calls `asyncio.run(run(args()))`. <!-- sdd-owner: implementation -->
- [ ] In `run()`, open an `AsyncSession` via `app.db.session.get_sessionmaker()` and build `wallet_to_agent = await resolve_wallet_to_agent(session)` (the public helper from WU2 — do not duplicate canonicalization). Compute `unlinked_before = scalar("SELECT COUNT(*) FROM onchain_transfers WHERE linked_agent_id IS NULL")` and `wallets_known = len(wallet_to_agent)`. <!-- sdd-owner: implementation -->
- [ ] Implement the keyset pagination loop per design §3.3: `SELECT id, to_address FROM onchain_transfers WHERE linked_agent_id IS NULL AND id > :cursor ORDER BY id LIMIT :chunk`. Break when zero rows returned. Apply the `--limit` cap before the loop. <!-- sdd-owner: implementation -->
- [ ] For each chunk: resolve matches with `_canon_addr(to_addr)` against `wallet_to_agent`; collect `(id, agent_id)` pairs; issue a dialect-aware batched UPDATE. **Postgres**: `UPDATE onchain_transfers SET linked_agent_id = v.agent_id FROM (VALUES (:id, :agent_id), ...) AS v(id, agent_id) WHERE onchain_transfers.id = v.id AND onchain_transfers.linked_agent_id IS NULL`. **sqlite/dev**: `executemany` of `UPDATE onchain_transfers SET linked_agent_id = :agent_id WHERE id = :id AND linked_agent_id IS NULL`. Commit per chunk and sleep 50ms. <!-- sdd-owner: implementation -->
- [ ] Implement summary + exit-code policy per REQ-003: print exactly `unlinked_before=<n> linked_now=<n> still_unlinked=<n> wallets_known=<n>` on completion. Return 0 if `linked_now > 0` OR `unlinked_before == 0`. Return 2 if `linked_now == 0 AND wallets_known > 0 AND unlinked_before > 0` (the stall guard the existing endpoint lacks). Return 1 on DB / connection errors. <!-- sdd-owner: implementation -->
- [ ] Wire `--dry-run` to skip the batched UPDATE and only print the candidate count it *would* have processed. Wire `--limit` so the keyset loop terminates early once the cumulative processed count exceeds the limit (useful for smoke runs against prod). <!-- sdd-owner: implementation -->
- [ ] Smoke-test the script locally against the test sqlite (`uv run python -m scripts.backfill_link_agents --dry-run --limit 100`) and confirm the summary line shape + exit code path. <!-- sdd-owner: implementation -->

---

## WU4 — Realtime linking verification (no new code if WU2 is correct)

> The realtime path already writes `linked_agent_id` at insert time; WU4's job is to lock in that it shares the new helper with backfill and to document the per-cycle wallet-map refresh.

- [x] Grep `app/services/onchain_indexer.py` to confirm both `_scan_and_store` (line 269) and `_scan_and_store_direct` (line 381) now call `_canon_addr(to_addr)` for the lookup. If either path still uses bare `.lower()`, refactor it and add a regression note to the change log. <!-- sdd-owner: implementation -->
- [x] Confirm `_realtime_cycle` (line ~554) and `_backfill_cycle` (line ~657) both call `resolve_wallet_to_agent(session)` exactly once per cycle (not per log). Add a one-line comment near each callsite: "Refresh per cycle so newly-synced agents are picked up on the next pass." <!-- sdd-owner: implementation -->
- [x] Verify no deferral: grep the indexer for any TODO / FIXME / `cron` / `periodic` references to a "link later" job. None must exist — REQ-005 forbids deferred linking. <!-- sdd-owner: implementation -->
- [x] Add a worker comment in `_resolve_agent_wallets` documenting R-5 + R-6: empty wallet map on first cycle is a no-op until the sync worker populates `agent_cache`; a wallet added between cycles is picked up on the next cycle's map refresh. <!-- sdd-owner: implementation -->

---

## WU5 — Tests

> New file `tests/test_indexer_linking.py`. Offline unit tests + one respx-mocked integration test. Must contribute ≥10 test functions and ≥25 assertions covering REQ-001..REQ-005.

- [ ] Create `tests/test_indexer_linking.py` with imports `from app.services.onchain_indexer import _canon_addr, resolve_wallet_to_agent, _scan_and_store, _scan_and_store_direct` and the existing `tests/conftest.py` fixtures (`db`, `client`, `respx_mock`, `_now`, `_seed_agent`). <!-- sdd-owner: implementation -->
- [ ] Write offline `_canon_addr` tests: `test_canonicalize_lowercase_matches_checksum` (REQ-001 case), `test_canonicalize_strips_whitespace_and_prefix` (REQ-001 whitespace + `0x`), `test_canonicalize_rejects_garbage` (defensive — non-hex / wrong length / None). <!-- sdd-owner: implementation -->
- [ ] Write offline mapping tests: `test_resolve_wallets_returns_lowercase_dict` (REQ-002 helper contract — seeded `AgentCache` row with EIP-55 wallet returns `{canon(wallet): agent_id}`), `test_link_lookup_no_match_returns_none` (REQ-002 no-match scenario), `test_link_lookup_preserves_existing_linkage` (REQ-002 — pre-populated `linked_agent_id` is not overwritten by a re-resolve). <!-- sdd-owner: implementation -->
- [ ] Write offline backfill tests against an in-memory sqlite: `test_backfill_idempotent_second_run_no_writes` (REQ-003 — second invocation finds zero candidates), `test_backfill_chunks_at_most_chunk_size` (REQ-003 — patch the SELECT to assert `LIMIT :chunk` is honored), `test_backfill_exits_2_when_linking_is_zero` (REQ-003 — seed `agent_cache` wallet + unlinked transfer, force the matching helper to return empty, assert `run()` returns 2). <!-- sdd-owner: implementation -->
- [ ] Write realtime linking tests: `test_realtime_link_inserts_with_agent_id` (REQ-005 — invoke `_scan_and_store` against a fake `wallet_to_agent` map with one seeded wallet; assert the resulting `OnchainTransfer` row has `linked_agent_id` populated), `test_realtime_link_inserts_null_for_unknown` (REQ-005 — same shape, wallet not in map → `linked_agent_id IS NULL`, no exception). <!-- sdd-owner: implementation -->
- [ ] Write the respx integration test (REQ-004): mock `eth_getLogs` via `respx_mock.post(RPC_URL)` to return a fixture batch of `$U` Transfer logs whose `to` includes a seeded `AgentCache.agent_wallet`. Invoke the indexer ingest path; assert the resulting `onchain_transfers` row exists with `block_number` matching the fixture and `linked_agent_id == seeded_agent.agent_id`. Reuse the `_log()` / `_rpc_result()` helpers from `tests/test_api_agent_payments.py` as a template. <!-- sdd-owner: implementation -->
- [ ] Confirm `uv run pytest tests/test_indexer_linking.py -v` collects and passes all 10+ tests. Confirm total assertion count is ≥25 (visible in `-v` output). <!-- sdd-owner: implementation -->

---

## Verification

- [ ] Run `uv run pytest tests/test_indexer_linking.py -v` from repo root and confirm all new tests pass. <!-- sdd-owner: implementation -->
- [ ] Run the full `uv run pytest` and confirm no new failures versus the 282-test pre-change baseline (AC-5). Treat the AC as "no new failures" per proposal R-4 if the working branch baseline differs from 282. <!-- sdd-owner: implementation -->
- [ ] Production backfill: SSH into the production container and run `python -m scripts.backfill_link_agents --chunk 10000`. Confirm `linked_now >= 1000` (REQ-003 sanity threshold) and that the summary line matches the obs-242 shape exactly: `unlinked_before=<n> linked_now=<n> still_unlinked=<n> wallets_known=<n>`. <!-- sdd-owner: implementation -->
- [ ] Production idempotency check: re-run `python -m scripts.backfill_link_agents --chunk 10000` immediately. Confirm `linked_now=0`, exit code 0, and that zero rows were modified (AC-4). <!-- sdd-owner: implementation -->
- [ ] AC-1 spot-check: `curl /api/onchain/trends?days=30` and confirm `top_agents` contains ≥ 5 distinct agents with ≥ 5 transfers each. <!-- sdd-owner: implementation -->
- [ ] AC-2 spot-check: `curl /api/agents/56/<a-token-with-wallet>/payments` and confirm a non-empty recent-transfers response (note: per design §10 OQ-1, this endpoint uses live RPC; for the indexer-backed path see `/api/agents/{chain}/{token}/indexed-payments` if added by a follow-up change). <!-- sdd-owner: implementation -->

---

## Rollback boundaries (per WU)

| WU | Rollback action |
|----|-----------------|
| WU1 | Delete `scripts/diag_linking.py` + the notes file. No schema impact. |
| WU2 | `git revert` the WU2 commits. Behavior reverts to `.lower()`-only comparison; no schema or data impact. |
| WU3 | Delete `scripts/backfill_link_agents.py`. The HTTP adapter (if changed) reverts to the previous endpoint. Already-linked rows stay linked (no automatic unlink). |
| WU4 | Comment-only change; revert the comment commit. |
| WU5 | Delete `tests/test_indexer_linking.py`. |

---

## Out of scope (deferred)

- Live monitoring / alerting on `linked_now / total` ratio (proposal §"Out of scope").
- Sync-worker fix if WU1 finds `agent_cache.agent_wallet` is structurally NULL (design §10 OQ-2 — separate change).
- Per-agent indexed-payments read endpoint if AC-2 needs to consult `onchain_transfers` instead of live RPC (design §10 OQ-1 — separate change `indexer-payment-cache`).
