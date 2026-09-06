# On-Chain Indexer — Agent Linking Fix

**Change:** `indexer-link-fix`
**Domain:** `onchain-indexer` (`app/services/onchain_indexer.py`, `onchain_transfers` table, `app/routers/agents.py` `payments` endpoint, `/api/onchain/trends` `top_agents` aggregation)
**Scope:** Data-pipeline correctness. Diagnose, fix, backfill, and realtime-link `onchain_transfers` rows to `agent_cache` rows so that agent-scoped payment queries return real data.

---

## Purpose

The BSC `$U` Transfer indexer writes ~10K rows/day to `onchain_transfers`, but only 3 of 686,783 historical rows have an agent linkage populated. As a result `GET /api/agents/{chain}/{token}/payments` returns empty for every agent, and `/api/onchain/trends?days=30` `top_agents` reports `agent: null` for the overwhelming majority of transfers. The "agent paid by N users" trust signal (DESIGN.md D10) is unmeasurable.

This spec fixes the link between `onchain_transfers.to` and `agent_cache.agent_wallet`, backfills the 686K existing rows idempotently, links new transfers at insert time, and locks the behavior in with offline + respx-mocked tests.

---

## Requirements

### Requirement: REQ-001 — Diagnose root cause of zero linkage

The fix MUST begin with a diagnostic read of `app/services/onchain_indexer.py` that explicitly rules in or out each of the four known root causes:

1. **Case mismatch** — `to` is lowercase hex, `agent_wallet` is mixed-case EIP-55 checksum.
2. **Whitespace / null-byte padding** — any `strip()` / `lower()` missing in the comparison.
3. **Missing or unused index** — `agent_cache.agent_wallet` has no index, or the join uses a different column.
4. **Wrong expression** — link lookup uses `from` instead of `to`, or compares against the wrong column.

The diagnostic MUST print, for a sample of 100 recent transfers, whether the `to` address matches any known wallet after canonicalizing both sides to lowercase. The output MUST be committed to a diagnostic note referenced from the change tasks.

#### Scenario: diagnostic finds the cause

- GIVEN 100 recent `onchain_transfers` rows with non-null `to`
- AND the full `agent_cache.agent_wallet` set
- WHEN the diagnostic canonicalizes both sides to lowercase and counts matches
- THEN the count MUST be > 0 (otherwise the lookup itself is broken at a deeper level, not just a case bug)
- AND the dominant root cause MUST be identified by name from the four candidates above

#### Scenario: diagnostic finds zero matches even after canonicalization

- IF the lowercase-canonicalized match count is still 0, the fix MUST also inspect the `onchain_transfers.to` values for prefix drift (`0x` vs no-`0x`), whitespace, and null bytes before concluding the bug is structural.
- The diagnostic note MUST list which of these additional checks were run and their counts.

---

### Requirement: REQ-002 — Fix the linking logic

The fix MUST update the linking function (in `app/services/onchain_indexer.py` or equivalent) so that, given a `to` address and the `agent_cache` set, it correctly identifies the owning `agent_internal_id` and writes it back to the transfer row.

The fix MUST:

- Canonicalize both sides of the comparison (lowercase, strip whitespace, drop optional `0x` prefix consistently).
- Use the same comparison function for realtime linking and the backfill.
- Preserve any existing linkage (do not overwrite a populated `agent_id` / `agent_internal_id` with NULL).

#### Scenario: realtime single-row link

- GIVEN a transfer with `to = "0xabc..."` (lowercase)
- AND an `agent_cache` row with `agent_wallet = "0xABC..."` (EIP-55 checksum) for the same address
- WHEN the link function is called with the transfer
- THEN it MUST return that agent's `agent_internal_id`
- AND the transfer row MUST be updated to set `agent_id` (or `agent_internal_id`) to that value

#### Scenario: no match

- GIVEN a transfer with `to` not present in any `agent_cache.agent_wallet`
- WHEN the link function is called with the transfer
- THEN it MUST return NULL
- AND MUST NOT raise

#### Scenario: existing linkage preserved

- GIVEN a transfer that already has a non-null `agent_id` populated
- WHEN the link function is called with the transfer
- THEN the existing `agent_id` MUST remain unchanged (idempotent on populated rows)

---

### Requirement: REQ-003 — Backfill existing transfers idempotently

A backfill script (e.g. `scripts/backfill_link_agents.py` or extend the existing `backfill-link-agents` entry point) MUST retroactively link all existing `onchain_transfers` rows whose `to` matches an `agent_cache.agent_wallet`.

The backfill MUST:

- Be safe to re-run: a second invocation on the same dataset MUST NOT create duplicate rows or flip already-linked rows back to NULL.
- Process transfers in chunks (default ≤ 10,000 per chunk) to avoid OOM on the 686K-row corpus.
- Print a summary line `unlinked_before, linked_now, still_unlinked, wallets_known` on completion, matching the shape of the obs-242 production report.
- Exit with a non-zero status if `linked_now == 0` AND `wallets_known > 0` AND `unlinked_before > 0` — i.e. refuse to silently report "linked zero" the way the current script does.

#### Scenario: cold backfill on full corpus

- GIVEN the production `onchain_transfers` table with ~686,783 rows, ~7,627 known `agent_cache.agent_wallet` values, and only 3 rows with non-null `agent_id`
- WHEN the backfill script is invoked once
- THEN at least 1,000 rows MUST be newly linked (sanity threshold; real count is data-dependent but must be non-trivial)
- AND the printed summary MUST show `linked_now > 0`

#### Scenario: idempotent re-run

- GIVEN the backfill has already been run once successfully
- WHEN the backfill script is invoked a second time on the same dataset
- THEN zero rows MUST be modified (`linked_now == 0` is acceptable; the row count stays the same, no NULLs are reintroduced)
- AND no duplicate `agent_id` assignments MUST be created
- AND the script MUST exit 0

#### Scenario: chunked processing

- GIVEN a transfer corpus of >10,000 rows
- WHEN the backfill is invoked
- THEN it MUST process the corpus in chunks of at most `BACKFILL_CHUNK_SIZE` rows (default 10,000) and MUST NOT load the entire resultset into memory at once

---

### Requirement: REQ-004 — Tests for the linking logic

A new test file `tests/test_indexer_linking.py` MUST cover the linking function with offline unit tests plus a respx-mocked integration test against the indexer HTTP path.

Tests MUST include:

- **Offline unit tests** (no network, no DB session required beyond SQLite/in-memory fixture):
  - Lowercase `to` matches EIP-55 `agent_wallet` → returns the agent id.
  - Whitespace and `0x`-prefix variants on either side → still matches.
  - Non-match → returns None without raising.
  - Idempotent on already-linked rows.
- **respx integration test** (mocked BSC RPC + indexer endpoint):
  - A mocked `eth_getLogs` returns a batch of `$U` Transfer events, some of whose `to` addresses correspond to seeded `agent_cache` wallets.
  - After the indexer runs, the corresponding `onchain_transfers` rows MUST have `agent_id` populated.

#### Scenario: offline matching covers all four candidate bugs

- GIVEN the test file `tests/test_indexer_linking.py`
- WHEN the test suite runs
- THEN there MUST be at least one assertion per REQ-001 root cause (case, whitespace, index, wrong-column-expression) that demonstrates the fix handles it correctly
- AND all assertions MUST pass

#### Scenario: respx integration test exercises the indexer end-to-end

- GIVEN respx intercepting the BSC RPC endpoint and returning a fixture log batch
- AND a seeded `agent_cache` row whose wallet appears in that batch's `to` field
- WHEN the indexer processes the batch
- THEN a row in `onchain_transfers` exists with `block_number` matching the fixture
- AND that row has `agent_id` equal to the seeded agent's `agent_internal_id`

---

### Requirement: REQ-005 — Realtime linking for new transfers

The indexer's ingest path (the function that writes new `$U` Transfer events into `onchain_transfers`) MUST populate the agent linkage on insert, not defer it to a periodic job.

New transfers MUST have their `agent_id` (or `agent_internal_id`) populated within the same code path that writes the row, using the same canonicalization rules as the backfill (REQ-002).

#### Scenario: new transfer lands linked

- GIVEN a fresh BSC block containing a `$U` Transfer event with `to = 0xabc...`
- AND an `agent_cache` row with `agent_wallet = 0xABC...`
- WHEN the indexer ingests the block
- THEN the newly written `onchain_transfers` row MUST have `agent_id` populated to that agent's `agent_internal_id` before the ingest transaction commits
- AND a subsequent query `SELECT agent_id FROM onchain_transfers WHERE tx_hash = ...` MUST return the agent id, not NULL

#### Scenario: new transfer to an unknown wallet

- GIVEN a `$U` Transfer event whose `to` is not in `agent_cache.agent_wallet`
- WHEN the indexer ingests the block
- THEN the row MUST be inserted with `agent_id = NULL`
- AND the ingest MUST NOT raise or retry

---

## Acceptance Criteria

All MUST be true after the change is applied:

1. **AC-1 — `top_agents` has real signal.** `GET /api/onchain/trends?days=30` returns `top_agents` with at least 5 distinct agents, each having at least 5 transfers in the 30-day window.
2. **AC-2 — Agent payments endpoint returns data.** `GET /api/agents/{chain_id}/{token_id}/payments` returns a non-empty list of recent transfers for any x402 agent with a populated `agent_wallet`.
3. **AC-3 — Tests exist.** `tests/test_indexer_linking.py` is present and contains at least the offline + respx scenarios above; the file is collected by `pytest`.
4. **AC-4 — Idempotent backfill.** Re-running the backfill script produces zero row modifications and exits 0.
5. **AC-5 — Baseline preserved.** `uv run pytest` reports no new failures versus the 282-test pre-change baseline.

---

## Risks

- **R-1 (medium):** If `onchain_transfers.agent_id` (or `agent_internal_id`) does not exist as a column, a schema migration is required. That migration is in-scope per the proposal's "Schema migration UNLESS the linking column doesn't exist" carve-out, but must be its own Alembic revision with a downgrade.
- **R-2 (low):** Linking 686K rows in one transaction could OOM. The chunking requirement in REQ-003 mitigates this; the script MUST commit per chunk.
- **R-3 (low):** If REQ-001 finds the bug is *not* a simple canonicalization issue (e.g. wrong column entirely), the fix surface expands beyond REQ-002 and the implementation tasks list must be updated.
- **R-4 (low):** Baseline test count drift — if the working branch baseline differs from 282, AC-5 is interpreted as "no new failures" rather than an exact match.