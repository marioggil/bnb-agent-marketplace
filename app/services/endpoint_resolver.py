"""Resolve `{agentId}` placeholders in off-chain service endpoints.

Termix off-chain registration metadata ships service endpoints with a
literal `{agentId}` placeholder. The correct substitution depends on the
API path (verified against the live Termix API, 2026-09):

    - `/a2a/agents/{agentId}/card`   -> {agentId} == ERC-8004 token_id
    - `/agents/{agentId}/services`   -> {agentId} == Termix internal card id
      (NOT token_id, NOT ownerAccountId — both 404 against the live API)

Pure function, DB-free, unit-testable.
"""

from __future__ import annotations

#: Path marker for the A2A card endpoint (resolves with token_id).
_A2A_PATH = "/a2a/"
#: Path marker for the Termix Platform services endpoint (resolves with the
#: internal Termix card id — verified: token_id 404s against the live API).
_SERVICES_PATH_SUFFIX = "/services"


def resolve_agent_id_placeholder(
    endpoint: str,
    *,
    token_id: int,
    termix_internal_id: str | None,
) -> str | None:
    """Return `endpoint` with its `{agentId}` placeholder resolved, or None
    when the endpoint cannot be turned into a working URL.

    Rules:
      - No `{agentId}`       -> endpoint unchanged.
      - `/a2a/` path         -> substitute with `token_id` (verified 200).
      - `/services` path     -> substitute with `termix_internal_id` when
                                present; None otherwise (verified: token_id
                                gives a 404, so a best-effort fallback would
                                produce a broken link).
      - Any other path       -> best-effort substitute with `token_id`.

    The caller renders `None` as plain text (never a link), since a URL
    still containing `{agentId}` is worse than no link.
    """
    if not endpoint:
        return endpoint
    if "{agentId}" not in endpoint:
        return endpoint

    # Termix Platform services endpoint: only the internal Termix card id
    # produces a working URL (404 with token_id or ownerAccountId).
    if endpoint.rstrip("/").endswith(_SERVICES_PATH_SUFFIX) and "/a2a/" not in endpoint:
        if not termix_internal_id:
            return None
        return endpoint.replace("{agentId}", termix_internal_id)

    # A2A card endpoint (and every other path): token_id is the identifier.
    return endpoint.replace("{agentId}", str(token_id))


__all__ = ["resolve_agent_id_placeholder"]
