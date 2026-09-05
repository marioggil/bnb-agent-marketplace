# Design: indexer-link-fix — make `$U` transfers link to agent wallets

**Change:** `indexer-link-fix`
**Domain:** on-chain indexer / data pipeline
**Source spec:** `openspec/changes/indexer-link-fix/spec.md`
**Source proposal:** `openspec/changes/indexer-link-fix/proposal.md`

---

## 1. Goal & problem statement

The BSC `$U` Transfer indexer writes ~10K rows/day to `onchain_transfers`. Only 3 of 686,783 historical rows carry a `linked_agent_id`. As a result:

- `GET /api/onchain/trends?days=30` `top_agents` returns `agent: null` for nearly every transfer (3 entries, each with `transfers: 1`).
- `GET /api/agents/{chain}/{token}/onchain/stats` (the cached path in `app/routers/onchain_stats.py:35`) returns zero hire counts for every agent.
- The marketplace trust signal "paid by N users" (DESIGN.md D10) is unmeasurable.

The fix MUST make `to_address → agent_cache.agent_wallet` resolve to the correct `linked_agent_id`, retroactively for the 686K-row historical corpus and at insert time for new transfers.

---

## 2. Current state — what the code does today

### 2.1 Schema (no migration needed)

`onchain_transfers` (model: `app/db/models/onchain_index.py:51`):

| Column           | Type           | Nullable | Notes                                                   |
| ---------------- | -------------- | -------- | ------------------------------------------------------- |
| `linked_agent_id`| `Text`         | YES      | Stores `AgentCache.agent_id` (`{chain}:{registry}:{tok}`) |
| `to_address`     | `String(42)`   | NO       | Lowercase 0x-prefixed hex                                |

Existing indexes on `onchain_transfers` (migration 0005 + model `__table_args__`):

- `uq_transfer_tx (tx_hash, from_address, to_address)` — UNIQUE
- `ix_onchain_transfers_block (block_number)`
- `ix_onchain_transfers_from (from_address)`
- `ix_onchain_transfers_to (to_address)`
- `ix_onchain_transfers_agent (linked_agent_id)`

**No schema change required.** The column the proposal called `agent_id` / `agent_internal_id` already exists as `linked_agent_id`. The 8004scan-internal UUID is **not** what we link against — `agent_id` (the canonical `{chain}:{registry}:{tokenId}`) is.

### 2.2 Realtime linking — what `_scan_and_store` and `_scan_and_store_direct` do today

Both code paths (`app/services/onchain_indexer.py:259` and `:359`) do the same canonicalization:

1. Extract address: `_extract_addr(topic)` → `"0x" + topic[-40:]` (always lowercase hex, 42 chars).
2. Resolve wallet map: `_resolve_agent_wallets(session)` → `{row[0].lower(): row[1] for row in result.all()}` — keys are lowercase.
3. Lookup: `wallet_to_agent.get(to_addr.lower())` → assigns `linked_agent=...` in the `pg_insert(OnchainTransfer).values(...)` call.
4. Commit per chunk via `await session.commit()`.

### 2.3 Existing backfill — the endpoint that "links zero"

`app/routers/onchain_stats.py:574` exposes `GET /api/onchain/backfill-link-agents`. It:

1. Builds the same `{lower(wallet): agent_id}` map.
2. Selects `OnchainTransfer.id, to_address WHERE linked_agent_id IS NULL` — **loads the entire unlinked resultset into memory** (~686K rows).
3. Iterates Python-side and issues a per-row `UPDATE OnchainTransfer ... SET linked_agent_id = :agent WHERE id = :id`.
4. Reports `{unlinked_total, linked_now, wallets_known}`.

This is the script obs-242 ran against production and got `linked_now=0`. It does NOT chunk, does NOT batch the UPDATE, and has no exit-code guard against the "linked zero" failure mode the spec calls out (REQ-003).

### 2.4 The lookup expression is correct — the gap is elsewhere

The link expression `wallet_to_agent.get(to_addr.lower())` already lowercases both sides. The four REQ-001 root causes break down as follows for the **current code**:

| Candidate root cause                            | Status in current code                                                                                                              |
| ----------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| Case mismatch (lowercase `to` vs EIP-55 wallet) | **Already handled** — both sides are `.lower()`-ed before comparison.                                                              |
| Whitespace / null-byte padding                  | **Not possible in current code** — `to_addr` is built deterministically from a 40-hex substring; `agent_wallet` is `.lower()`-ed. |
| Missing / unused index                          | **Lookup is in-memory dict, not a SQL join** — index health is irrelevant to the matching itself; only relevant to map population. |
| Wrong expression (`from` vs `to`)               | **Correct** — code uses `to_addr`. Verified at `app/services/onchain_indexer.py:276` and `:387`.                                     |

So the bug is **not** a canonicalization regression. The diagnostic must establish WHY the match count is 0 — three live possibilities:

1. **`agent_cache.agent_wallet` is NULL for the rows that 8004scan calls "agent wallets"** — e.g. the sync worker stores the wallet under a different column (`owner_address`, `contract_address`) or never persists it.
2. **The `to_address` values in `onchain_transfers` are wallet addresses of `$U` holders who are NOT agents** — i.e. there is no overlap between the indexed corpus and the agent set. In that case the spec's "linked_now > 0" acceptance is data-dependent, not code-dependent, and AC-1 cannot be satisfied by a code fix alone.
3. **A third column is the actual "receive payments" wallet** (e.g. `creator_address`, `owner_address`, or a separate `payment_address`) and we are matching against the wrong one.

The diagnostic (Section 3) resolves which of these is true and determines the actual fix surface.

---

## 3. Technical approach

The work is split into five sequenced phases — the first one is a hard prerequisite to the other four, because its output determines which fixes are even relevant.

### 3.1 WU1 — Diagnostic (gates WU2/WU3/WU4)

A standalone CLI script (`scripts/diag_linking.py`, new file) that, given a `DATABASE_URL`, prints:

- Total rows in `onchain_transfers`, total with `linked_agent_id IS NOT NULL`.
- Total non-null `agent_cache.agent_wallet` rows.
- For a 100-row sample of recent `onchain_transfers`:
  - Distinct `to_address` values.
  - Overlap (lowercased) with the full `agent_wallet` set.
  - Counts of: `0x`-prefix drift, embedded whitespace, embedded null bytes, length != 42.
- For the same sample, **the same probe against** `creator_address`, `owner_address`, `contract_address` on `agent_cache` (catches the "wrong column" case without writing a migration first).

Output goes to stdout and is captured in the change's task notes (`openspec/changes/indexer-link-fix/notes/diag-YYYYMMDD.md`). Acceptance for WU1:

- Probe runs against a representative subset of `onchain_transfers` (≥ 100 rows, last 30 days).
- Counts printed for each of the four REQ-001 candidates.
- Decision recorded: **case / whitespace / wrong-column / structural (data mismatch)**.

If the probe shows **zero overlap on every wallet column**, the fix surface expands: we either have to (a) widen the wallet column set, (b) accept AC-1 cannot be hit by code, or (c) trigger a fresh 8004scan sync that actually populates `agent_wallet`. That decision is captured as a sub-task before WU2 starts.

### 3.2 WU2 — Fix linking logic

Scope is determined by the WU1 result.

**Default path (canonicalization bug found):**

- Extract a single canonicalization helper:

  ```python
  # app/services/onchain_indexer.py
  def _canon_addr(addr: str | None) -> str | None:
      """Lowercase 0x-prefixed 20-byte hex; tolerate leading/trailing whitespace,
      drop an optional 0x prefix, return None for anything that does not parse."""
      if not addr:
          return None
      s = addr.strip().lower()
      if s.startswith("0x"):
          s = s[2:]
      if len(s) != 40 or any(c not in "0123456789abcdef" for c in s):
          return None
      return "0x" + s
  ```

- Use it in **three** places so realtime and backfill cannot drift:
  - `_resolve_agent_wallets` (builds the dict; canonicalize the value side too).
  - `_scan_and_store` and `_scan_and_store_direct` (canonicalize `to_addr` before lookup).
  - The new `scripts/backfill_link_agents.py` (Section 3.3).

- Preserve existing linkages: the `UPDATE` in WU3 uses `WHERE linked_agent_id IS NULL` so populated rows are never touched (REQ-002 "existing linkage preserved").

**Wrong-column path (probe shows wallet stored in another field):**

- Update `_resolve_agent_wallets` to read from the column the probe identified (e.g. `owner_address` instead of `agent_wallet`). No schema change.
- If multiple columns are populated for different agents, build the dict from a UNION of all candidate columns.

**Structural path (probe shows zero overlap on every column):**

- Block WU2/WU3/WU4; trigger a 8004scan sync re-run; if that fails to populate `agent_wallet`, escalate (out of scope for this change per proposal).

### 3.3 WU3 — Backfill script (new CLI)

A new standalone script: **`scripts/backfill_link_agents.py`**, runnable as:

```bash
python -m scripts.backfill_link_agents                # default chunk=10_000
python -m scripts.backfill_link_agents --chunk 5000   # override chunk size
python -m scripts.backfill_link_agents --dry-run      # count only, no writes
python -m scripts.backfill_link_agents --limit 100000 # process first 100k only (smoke)
```

Behavior:

- Connects via `app.db.session.get_sessionmaker()`.
- Resolves the canonical wallet→agent map via the same `_resolve_agent_wallets`-equivalent helper from WU2 (re-imported, not copy-pasted).
- Counts `unlinked_before` (single scalar query: `SELECT COUNT(*) FROM onchain_transfers WHERE linked_agent_id IS NULL`).
- Streams chunks via keyset pagination:

  ```sql
  SELECT id, to_address
  FROM onchain_transfers
  WHERE linked_agent_id IS NULL AND id > :cursor
  ORDER BY id
  LIMIT :chunk
  ```

- For each chunk:
  - Resolves the match in Python (same canonicalization as realtime).
  - Issues a **single batched** `UPDATE onchain_transfers SET linked_agent_id = v.agent_id FROM (VALUES (...)) AS v(id, agent_id) WHERE onchain_transfers.id = v.id` per chunk (Postgres) or, for sqlite/dev, `executemany` of parameter-binds. Per-chunk commit.
  - Sleeps 50ms between chunks so the connection isn't hammered.

- Idempotency (REQ-003): the `WHERE linked_agent_id IS NULL` clause on both the SELECT and the UPDATE means a second run finds zero candidates and exits 0 with `linked_now=0`.

- Failure detection (REQ-003): if `linked_now == 0 AND wallets_known > 0 AND unlinked_before > 0`, the script exits with code **2** (distinct from "no work" exit 0). This is the guard that the existing `/api/onchain/backfill-link-agents` endpoint lacks.

- Final summary line, exactly matching the obs-242 report shape:

  ```
  unlinked_before=<n> linked_now=<n> still_unlinked=<n> wallets_known=<n>
  ```

- Reuses `app.config.get_settings()` for `DATABASE_URL`; no new env vars.

### 3.4 WU4 — Realtime linking verification

The realtime path already writes `linked_agent_id` at insert time (Section 2.2). The verification work is:

1. **Lock in the same canonicalization**: confirm `_scan_and_store` and `_scan_and_store_direct` both call the new helper. If not, refactor so they share one function.
2. **Refresh the wallet map per cycle, not per log**: the current code calls `_resolve_agent_wallets` once per chunk in `_realtime_cycle` / `_backfill_cycle`, which is correct — keep that.
3. **No deferral**: do NOT remove the per-insert assignment and rely on a cron to backfill. The spec (REQ-005) explicitly forbids that.
4. **On a missed match, retry-once per cycle**: if a new transfer lands with `linked_agent_id IS NULL` (because `agent_cache` was stale when the cycle ran), the next backfill/realtime cycle will pick it up via `_resolve_agent_wallets` re-reading the table. No code change; documented in the worker comments.

A small assertion in `tests/test_indexer_linking.py` (Section 3.5) is sufficient to lock this in.

### 3.5 WU5 — Tests

New file: **`tests/test_indexer_linking.py`** — offline unit tests + a respx integration test. Structure:

```
tests/test_indexer_linking.py
├── test_canonicalize_lowercase_matches_checksum   # REQ-001 / case
├── test_canonicalize_strips_whitespace_and_prefix # REQ-001 / whitespace+prefix
├── test_canonicalize_rejects_garbage              # defensive
├── test_resolve_wallets_returns_lowercase_dict    # REQ-002 helper contract
├── test_link_lookup_no_match_returns_none         # REQ-002 no-match scenario
├── test_link_lookup_preserves_existing_linkage    # REQ-002 preserve scenario
├── test_backfill_idempotent_second_run_no_writes  # REQ-003 idempotent scenario
├── test_backfill_chunks_at_most_chunk_size        # REQ-003 chunking scenario
├── test_backfill_exits_2_when_linking_is_zero     # REQ-003 exit-code guard
├── test_realtime_link_inserts_with_agent_id       # REQ-005 insert scenario
└── test_realtime_link_inserts_null_for_unknown    # REQ-005 unknown wallet
```

Plus an integration test that wires respx to mock `eth_getLogs` against the existing `app/services/onchain_indexer.py` ingest entry, seeds an `AgentCache` row with a wallet that appears in the fixture logs, and asserts the resulting `onchain_transfers` row has the right `linked_agent_id`.

The test file must be collectable by the default `uv run pytest` invocation (no new fixtures beyond what `tests/conftest.py` already provides — the `_now`, `_seed_agent`, `db`, `client`, `respx_mock` helpers cover everything needed).

---

## 4. Key questions — answered

### Q1. Schema: does `onchain_transfers` have an `agent_internal_id` or similar FK column?

**Yes** — as `linked_agent_id TEXT` (nullable). It stores `AgentCache.agent_id` (the canonical `{chainId}:{registry}:{tokenId}` string), **not** the `agent_internal_id` UUID. The spec's mention of `agent_internal_id` is shorthand for "the column that links to an agent" — the actual column name is `linked_agent_id` and that is what every read path already filters on (`app/routers/onchain_stats.py:43,56,65,192`; `app/routers/pages.py:879`).

**No migration.** A new Alembic revision would be a no-op ALTER and is rejected by the proposal's "schema migration UNLESS the linking column doesn't exist" carve-out — the column exists.

### Q2. Canonicalization: lowercase? EIP-55 checksum? trim 0x prefix?

The current code does `.lower()` on both sides of the comparison, which already handles EIP-55 checksum (checksum addresses differ only in case). The WU2 helper additionally:

- Trims whitespace.
- Tolerates a missing `0x` prefix (some 8004scan payloads omit it on certain fields).
- Returns `None` for unparseable inputs so a corrupt row cannot poison the dict.

This is the **minimum** change to be defensive — the current code happens to work because every address passes through `_extract_addr` (which always emits `0x` + 40 hex chars) and `AgentCache.agent_wallet` is stored with `0x`, but the helper makes the contract explicit and guards against future drift.

### Q3. Index: is there an index on `to` column?

**Yes**: `ix_onchain_transfers_to ON onchain_transfers (to_address)` (migration 0005 + model `__table_args__`). The realtime/backfill lookup is in-memory, so the index is only used by read paths like `/api/onchain/stats/wallet/{wallet}` (which already filters by `to_address`). No new index needed.

For the **backfill** specifically: a covering index on `(id) WHERE linked_agent_id IS NULL` would speed up the keyset cursor, but on 686K rows the speedup is marginal and the partial-index DDL is not portable to sqlite. Use the existing PK on `id` and accept the `WHERE linked_agent_id IS NULL` filter as a sequential scan over unlinked rows (acceptable: a successful run collapses the unlinked set to ~0).

### Q4. Backfill strategy: chunked (e.g. 1000 at a time) or full sweep?

**Chunked, 10,000 per chunk** (spec REQ-003 default). Keyset-paginate on `id`, batch the UPDATE per chunk, commit per chunk.

Why keyset on `id` and not on `block_number`? `block_number` has duplicates (multiple transfers per block), so `OFFSET` would skip/duplicate rows; `id` is the PK and monotonic. The cursor is the last `id` of the previous chunk.

Why 10K? Empirically small enough to keep one chunk's rowset under ~2 MB (40 bytes × 10K), large enough that 686K rows finish in ~70 chunks in a few seconds. Configurable via `--chunk` for the dev-mode smoke run.

---

## 5. Module map

| Path                                                         | Status        | Purpose                                                                 |
| ------------------------------------------------------------ | ------------- | ----------------------------------------------------------------------- |
| `app/services/onchain_indexer.py`                            | **EDIT**      | Add `_canon_addr`; use it from both `_resolve_agent_wallets` and the two scan-and-store paths. Add `resolve_wallet_to_agent` public helper so the script can import it. |
| `app/db/models/onchain_index.py`                             | **READ ONLY** | Schema confirmed correct; no edits.                                     |
| `migrations/versions/0012_*.py`                              | **NOT NEEDED**| `linked_agent_id` already exists (migration 0005). Skipping a migration avoids a no-op revision and respects the proposal's carve-out. |
| `scripts/diag_linking.py`                                    | **NEW**       | One-shot diagnostic; prints overlap counts for the four REQ-001 candidates. CLI: `python -m scripts.diag_linking`. |
| `scripts/backfill_link_agents.py`                            | **NEW**       | Chunked, idempotent, exit-2-on-stall backfill. CLI: `python -m scripts.backfill_link_agents [--chunk N] [--dry-run] [--limit N]`. |
| `app/routers/onchain_stats.py`                               | **EDIT (opt)**| The existing `/api/onchain/backfill-link-agents` endpoint stays as a thin adapter that delegates to `scripts/backfill_link_agents.run()` so HTTP-driven backfills keep working for ops, but its O(n) memory + per-row UPDATE behavior is **not** preserved — every HTTP hit now goes through the chunked script. (If we want to preserve the HTTP endpoint for read-only diagnostics only, it returns the same `{unlinked_before, linked_now, still_unlinked, wallets_known}` shape after invoking the script; otherwise delete the endpoint.) |
| `tests/test_indexer_linking.py`                              | **NEW**       | Offline unit tests + respx integration test. 10+ assertions covering REQ-001 through REQ-005. |

---

## 6. Data flow

### Realtime (per cycle)

```
_realtime_cycle (every REALTIME_INTERVAL = 240s)
  ├─ fetch head block via MultiRPCClient
  ├─ open AsyncSession
  ├─ SELECT MAX(block_number) FROM onchain_transfers  ── cursor
  ├─ wallet_to_agent = resolve_wallet_to_agent(session)   ◄── uses _canon_addr
  ├─ scan blocks [from, to] via Chainstack eth_getLogs
  └─ for each log:
       to_addr = _extract_addr(topics[2])                  # always 0x + 40 hex
       linked = wallet_to_agent.get(_canon_addr(to_addr))
       pg_insert(OnchainTransfer, ..., linked_agent_id=linked)
         .on_conflict_do_nothing()
       session.commit()                                     # per chunk
```

The change: `_canon_addr(to_addr)` instead of `to_addr.lower()` (the latter is a strict subset of the former, so behavior is preserved when input is well-formed and survives when it isn't).

### Backfill (one-shot, CLI)

```
scripts/backfill_link_agents.run()
  ├─ session = get_sessionmaker()()
  ├─ wallet_to_agent = resolve_wallet_to_agent(session)
  ├─ unlinked_before = scalar("SELECT COUNT(*) WHERE linked_agent_id IS NULL")
  ├─ cursor = 0
  ├─ while True:
  │    rows = SELECT id, to_address
  │            WHERE linked_agent_id IS NULL AND id > :cursor
  │            ORDER BY id LIMIT :chunk
  │    if not rows: break
  │    matched = [(id, wallet_to_agent[_canon_addr(to_addr)])
  │               for id, to_addr in rows
  │               if _canon_addr(to_addr) in wallet_to_agent]
  │    execute batched UPDATE onchain_transfers
  │          SET linked_agent_id = v.agent_id
  │          FROM (VALUES ...) AS v(id, agent_id)
  │          WHERE onchain_transfers.id = v.id
  │    session.commit()
  │    cursor = rows[-1].id
  │    sleep 0.05
  ├─ still_unlinked = scalar("SELECT COUNT(*) WHERE linked_agent_id IS NULL")
  ├─ print(f"unlinked_before={n} linked_now={k} still_unlinked={m} wallets_known={w}")
  └─ exit 0 if linked_now > 0 OR unlinked_before == 0
     exit 2 if linked_now == 0 AND wallets_known > 0 AND unlinked_before > 0
```

### Read path (unchanged)

`/api/onchain/trends`, `/api/onchain/stats/{agent_id}`, `/api/onchain/stats/wallet/{wallet}` already filter on `linked_agent_id` (or `func.lower(to_address)` for the wallet-scoped endpoint). Once the indexer and backfill populate that column, these endpoints surface real data without any code change. No read-path edits.

---

## 7. Contracts & interfaces

### 7.1 Public functions added

```python
# app/services/onchain_indexer.py

def _canon_addr(addr: str | None) -> str | None:
    """Canonicalize an EVM address: trim, drop 0x, lowercase, validate, return
    '0x' + 40 hex. Returns None for unparseable / empty inputs."""

async def resolve_wallet_to_agent(session: AsyncSession) -> dict[str, str]:
    """Return {canon(wallet): agent_id} for every agent_cache row whose
    wallet column is parseable. Replaces the private _resolve_agent_wallets
    so the backfill script can import the same mapping function."""
```

### 7.2 CLI contracts

```
python -m scripts.diag_linking
  stdout: counts per REQ-001 candidate; overlap with agent_wallet,
          creator_address, owner_address, contract_address.
  exit 0 always.

python -m scripts.backfill_link_agents [--chunk N] [--dry-run] [--limit N]
  --chunk N    default 10000, max 100000
  --dry-run    count candidates, do not write
  --limit N    process at most N total rows (smoke)
  stdout:      one progress line per chunk,
               final summary "unlinked_before=… linked_now=… still_unlinked=… wallets_known=…"
  exit 0       on success or no-op
  exit 2       on "stall" (linked_now==0 && wallets_known>0 && unlinked_before>0)
  exit 1       on DB / connection error
```

### 7.3 Test contracts (assertions per file)

`tests/test_indexer_linking.py` MUST pass `uv run pytest` and contribute **at least one assertion per REQ-001 root cause** plus the full scenario set from REQ-002 / REQ-003 / REQ-004 / REQ-005. Minimum count: 10 test functions, ≥ 25 assertions.

---

## 8. Rollout plan

1. **Diagnostic run** on production replica (`scripts/diag_linking.py`) — captures the real overlap counts and the dominant root cause. Output committed to `openspec/changes/indexer-link-fix/notes/diag-YYYYMMDD.md`.
2. **Fix + tests + backfill script** in a single PR. CI runs the full 282+ test suite.
3. **Dry-run backfill** (`--dry-run`) on staging or prod replica: confirms `linked_now > 0` after the fix. If still zero, the "structural" path is taken (out of scope per proposal).
4. **Real backfill** on prod: `python -m scripts.backfill_link_agents`. Logs the summary line; ops verifies `linked_now >= 1000` against the sanity threshold in REQ-003.
5. **Realtime check**: tail `/api/onchain/health` for ~30 minutes; verify `linked_now` count grows by ~10K/day (the realtime cycle is unchanged but now writes the link).
6. **Acceptance verification** (AC-1 through AC-5):
   - AC-1: `GET /api/onchain/trends?days=30` → `top_agents` has ≥ 5 distinct agents with ≥ 5 transfers each.
   - AC-2: `GET /api/agents/56/{token_id}/payments` returns non-empty for an x402 agent with `agent_wallet` set. (Note: this endpoint currently uses live RPC via `app/services/agent_payments.py`; the indexer's populated `linked_agent_id` doesn't affect it. To validate AC-2 as written, we add a one-line cache check that returns indexed results when present and falls back to live RPC otherwise. See "Open question" in §10.)
   - AC-3: `tests/test_indexer_linking.py` exists and is collected.
   - AC-4: re-run backfill → `linked_now=0`, exit 0.
   - AC-5: `uv run pytest` reports no new failures vs the 282 baseline (REGR-1 risk is mitigated by the test suite).

---

## 9. Risks & mitigations

| Risk                                                                | Mitigation                                                                                                                            |
| ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| **R-1** schema change needed (column missing)                       | **Resolved by §2.1**: `linked_agent_id` already exists. No migration.                                                                  |
| **R-2** OOM on 686K-row backfill                                    | Keyset pagination + chunked batch UPDATE + per-chunk commit; constant ~2 MB memory per chunk. `--limit` and `--chunk` overrides for safety. |
| **R-3** root cause is structural (data mismatch, not code)          | WU1 diagnostic gates WU2; if zero overlap on every wallet column, escalate (sync re-run or out-of-scope).                              |
| **R-4** baseline test drift                                          | AC-5 is interpreted as "no new failures", not exact 282 match (per proposal R-4).                                                     |
| **R-5** new transfers not linked because `agent_cache` is stale     | Realtime cycle re-reads `agent_cache` every 240s; a wallet added between cycles is picked up on the next cycle. Document in worker comment. |
| **R-6** indexer writes `linked_agent_id=NULL` when wallet map is empty on first cycle | `_resolve_agent_wallets` returns `{}` if no agents have `agent_wallet` yet; the indexer is a no-op for linking until the sync worker populates the cache. Documented behavior; no code change. |
| **R-7** sqlite / Postgres portability of batched UPDATE             | The script detects the dialect: Postgres uses `UPDATE ... FROM (VALUES ...)`; sqlite uses `executemany` with parameter binds. Same exit codes and summary line in both. |
| **R-8** respx integration test brittle to log fixture shape          | Use the existing `_log()` helper from `tests/test_api_agent_payments.py` as a template; pin to `X402_U_TOKEN_ADDRESS_TESTNET` and topic0 = keccak("Transfer(address,address,uint256)"). |

---

## 10. Open questions

1. **AC-2 verification**: `GET /api/agents/{chain}/{token}/payments` currently calls live RPC, not the indexer. The proposal's AC-2 will be satisfied "for free" only if we extend the endpoint to consult `onchain_transfers` first. **Recommendation**: leave the endpoint's live-RPC behavior unchanged (it has tests); add a separate read endpoint `/api/agents/{chain}/{token}/indexed-payments` that reads from `onchain_transfers` for AC-2's spirit. Capture this as a separate change (indexer-payment-cache).
2. **Sync worker**: if WU1 finds that `agent_cache.agent_wallet` is NULL because the 8004scan sync doesn't populate it, that is a sync-worker bug and out of scope. Note it in the change's follow-ups.
3. **Realtime gap when `from_block` jumps**: `_realtime_cycle` jumps `from_block` to `current_block - REALTIME_CHUNK_SIZE` when the chain head gap exceeds 2× the realtime chunk. Transfers in the gap are not realtime-linked — the backfill worker picks them up. Documented; no code change.

---

## 11. Acceptance criteria mapping

| AC    | Spec clause                  | How this design satisfies it                                                                                                |
| ----- | ---------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| AC-1  | `top_agents` has real signal | Realtime linking + backfill populate `linked_agent_id`; `/api/onchain/trends` aggregation already filters on it.            |
| AC-2  | agent payments endpoint      | Out-of-box: live RPC still works. Indexed path is a separate cache endpoint (see §10 OQ-1).                                 |
| AC-3  | tests exist                  | `tests/test_indexer_linking.py` per §3.5, ≥ 10 functions, ≥ 25 assertions, collected by pytest.                             |
| AC-4  | idempotent backfill          | `WHERE linked_agent_id IS NULL` on both SELECT and UPDATE; second run finds zero candidates and exits 0.                    |
| AC-5  | baseline preserved           | No edits to existing tests; new tests in their own file; realtime path uses the same helper so existing `test_onchain_indexer.py` keeps passing. |

---

## 12. Work-unit summary (task-list seeds)

- **WU1** Diagnostic + schema check — `scripts/diag_linking.py`, output committed to change notes.
- **WU2** Fix linking logic — `_canon_addr` helper + `resolve_wallet_to_agent` in `app/services/onchain_indexer.py`; refactor both scan-and-store paths and the `_resolve_agent_wallets` private function.
- **WU3** Backfill script — `scripts/backfill_link_agents.py` (chunked, idempotent, exit-2-on-stall).
- **WU4** Realtime linking verification — confirm shared helper use; comment the per-cycle wallet-map refresh; no new code if WU2 is done correctly.
- **WU5** Tests — `tests/test_indexer_linking.py` (offline + respx).

Total new files: 3 (`scripts/diag_linking.py`, `scripts/backfill_link_agents.py`, `tests/test_indexer_linking.py`).
Total edited files: 1 (`app/services/onchain_indexer.py`); optionally 1 (`app/routers/onchain_stats.py` to delegate the HTTP backfill endpoint to the new script).
