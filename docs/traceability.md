# Traceability Map

Every shipped feature maps to a CO1 pair (BDOS → REQ) assigned by the
product owner. This document is the registry; code comments are the
mechanism.

## Registration contract

Every feature **MUST** register its CO1 pair in two places:

1. **Code comments** — the feature's main module(s) carry the pair as a
   comment (example: `app/routers/payments.py`,
   `app/services/agent_payments.py`, `tests/test_api_agent_payments.py`).
2. **This document** — one row per pair, linking the repo artifact.

Rules:

- **Never invent CO1 IDs.** A feature without an assigned CO1 pair is
  listed as **unregistered** below (no fabricated ID is created for it).
- When a new feature ships with a real CO1 pair, add the row here AND the
  comment in code in the same change — the two must land together.
- A feature is not "traceable" until both artifacts carry the pair.

## Registered pairs

| Design | Requirement | Repo artifact |
|---|---|---|
| CO1.BDOS.2063185 | CO1.REQ.2121688 | `GET /api/agents/{chain_id}/{token_id}/payments` — agent payment history (U token transfers to `agent_wallet`) |

## Unregistered features

The following shipped features have **no assigned CO1 pair** and therefore
carry no ID (do not invent one for them):

- On-chain $U transfer indexer (backfill/realtime workers + `/api/onchain/*`).
- Category taxonomy post-pass (10 categories + `other`).
- x402 hire flow (`/api/hires`, facilitator settlement).
- Wallet-nonce auth (`/auth/*`).
- Sync worker + Sync API (`/api/sync`).