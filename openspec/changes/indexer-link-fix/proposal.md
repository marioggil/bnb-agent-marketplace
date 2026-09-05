# Proposal: indexer-link-fix — make $U transfers link to agent wallets

## Change name
`indexer-link-fix`

## Domain
On-chain indexer / data pipeline

## Status
Draft

---

## Problem

The on-chain indexer scans $U `Transfer(address,address,uint256)` events on BSC mainnet (chain 56) and stores them in `onchain_transfers`. The volume is real (686,783 transfers, ~10K/day baseline, peaks of 122K on 2026-09-02), but only 3 transfers are linked to any `agent_cache.agent_wallet` (per `/api/onchain/trends?days=30` `top_agents` field).

Consequence: `GET /api/agents/{chain_id}/{token_id}/payments` returns empty for all agents, even those with `agent_wallet` set. The "agent paid by N users" trust signal (DESIGN.md D10) is at zero. The metric differentiating the marketplace from a directory (the BNB Agent Studio + TermiX hackathon narrative) is unmeasurable.

## Evidence

- obs-242 (production diagnosis, 2026-09-01): confirmed `unlinked_total=14511/14511, linked_now=0, wallets_known=7627`
- Live `/api/onchain/trends?days=30` (2026-09-05):
  ```
  top_agents: [
    {agent_id: '...:316780', transfers: 1, volume: 1.00},
    {agent_id: '...:318848', transfers: 1, volume: 49.43},
    {agent_id: '...:323333', transfers: 1, volume: 0.10}
  ]
  ```
  All other transfers return `agent: null`.

## Hypothesis

The `onchain_transfers` table has a nullable `agent_id` (or similar FK column) that should be populated when `to` matches an `agent_cache.agent_wallet`. The linking is either:
1. Not running (no scheduled backfill + no realtime linking)
2. Running but the lookup misses (case mismatch, checksum mismatch, hex prefix differences)
3. Running but the index isn't being used (no index on `to`, or join is wrong)

The previous diagnosis (obs-242) confirmed that `backfill-link-agents` reports `linked_now=0` despite 14,511 transfers and 7,627 known wallets. That's hard data showing the linking ran and found zero matches.

## Scope

**In**:
- Diagnostic step: read `app/services/onchain_indexer.py` linking logic
- Identify root cause (whitespace/checksum/case/index/expression)
- Fix the linking to actually match `to` → `agent_wallet`
- Backfill run that retroactively links existing 686K transfers
- Tests for the linking logic (offline + respx-mocked)

**Out**:
- Provider changes (Alchemy/Chainstack are fine)
- New indexer workers (no)
- Schema migration UNLESS the linking column doesn't exist
- UI changes (the trust signal will appear automatically once data flows)

## Acceptance criteria

1. `/api/onchain/trends?days=30` `top_agents` returns >= 5 distinct agents with >= 5 transfers each (sanity check the linking worked)
2. `GET /api/agents/{chain_id}/{token_id}/payments` for any x402 agent with wallet returns recent transfers
3. New test in `tests/test_indexer_linking.py` validates the `to → agent_wallet` matching offline
4. `backfill-link-agents` script (or equivalent) is idempotent: re-running it produces no duplicates
5. No regression in 282 baseline tests

## Risks

- **Medium**: if the fix involves a schema change (e.g. add `agent_internal_id` FK column), needs migration
- **Low**: linking might OOM if doing 686K transfers in one batch — may need pagination
- **Low**: case-sensitivity bug is the most likely root cause; verification is cheap

## Out of scope (deferred)

- Live monitoring of linking progress in production
- Alerting if `linked_now/total` drops below a threshold
- Per-agent transaction explorer link in the UI (already exists for paid hires via `tx_explorer_base`)

## Open questions

1. **Schema check**: does `onchain_transfers` have an `agent_internal_id` column already? (probably yes from earlier migrations, but need to verify)
2. **Backfill strategy**: one-shot CLI run vs incremental backfill alongside realtime
3. **Realtime linking**: should new transfers link immediately, or accumulate and link periodically?
