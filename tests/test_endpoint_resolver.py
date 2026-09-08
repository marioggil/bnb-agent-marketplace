"""Tests for resolving {agentId} placeholders in off-chain service endpoints.

The Termix off-chain registration metadata ships service endpoints with a
literal `{agentId}` placeholder:

    - A2A:   .../api/v1/a2a/agents/{agentId}/card
    - Termix Platform: .../api/v1/agents/{agentId}/services

Verification against the live Termix API (2026-09):
    - `/a2a/agents/{token_id}/card`      -> 200 OK   ({agentId} == token_id)
    - `/agents/{internal_id}/services`   -> 200 OK   ({agentId} == internal card id)
    - `/agents/{token_id}/services`      -> 404 NOT_FOUND
    - `/agents/{ownerAccountId}/services`-> 404 NOT_FOUND

So the resolver must pick the id by PATH, not apply one id everywhere.
"""

from __future__ import annotations

from app.services.endpoint_resolver import resolve_agent_id_placeholder

_A2A = "https://platform-backend.prod.termix.live/api/v1/a2a/agents/{agentId}/card"
_SERVICES = "https://platform-backend.prod.termix.live/api/v1/agents/{agentId}/services"


# ============================================================================
# T1 — no placeholder: endpoint passes through unchanged
# ============================================================================


def test_no_placeholder_returns_unchanged():
    ep = "https://github.com/agntcy/oasf/"
    assert resolve_agent_id_placeholder(ep, token_id=8, termix_internal_id=None) == ep


# ============================================================================
# T2 — /a2a/ path: substitute with token_id
# ============================================================================


def test_a2a_path_substitutes_token_id():
    resolved = resolve_agent_id_placeholder(_A2A, token_id=334432, termix_internal_id="ignored")
    assert resolved == "https://platform-backend.prod.termix.live/api/v1/a2a/agents/334432/card"


def test_a2a_path_works_without_internal_id():
    """The A2A card endpoint only needs token_id, so it resolves even when
    the internal Termix id is unavailable (card fetch failed)."""
    resolved = resolve_agent_id_placeholder(_A2A, token_id=338705, termix_internal_id=None)
    assert resolved == "https://platform-backend.prod.termix.live/api/v1/a2a/agents/338705/card"


# ============================================================================
# T3 — /agents/{id}/services path: substitute with internal Termix id
# ============================================================================


def test_services_path_substitutes_internal_id():
    resolved = resolve_agent_id_placeholder(
        _SERVICES, token_id=334432, termix_internal_id="cmtn3m9fd0c7gwr01h65n2wrj"
    )
    assert resolved == (
        "https://platform-backend.prod.termix.live/api/v1/agents/cmtn3m9fd0c7gwr01h65n2wrj/services"
    )


def test_services_path_unresolved_without_internal_id_returns_none():
    """Without the internal Termix id, the /services endpoint cannot be
    resolved to a working URL — the resolver returns None so the caller can
    render it as plain text instead of a broken link."""
    resolved = resolve_agent_id_placeholder(_SERVICES, token_id=334432, termix_internal_id=None)
    assert resolved is None


def test_services_path_does_not_fall_back_to_token_id():
    """Critical: /services must NOT substitute token_id — verified 404 against
    the live API. Only the internal Termix id produces a working URL."""
    resolved = resolve_agent_id_placeholder(
        _SERVICES, token_id=334432, termix_internal_id="cmtn3m9fd0c7gwr01h65n2wrj"
    )
    assert "334432" not in resolved
    assert resolved == (
        "https://platform-backend.prod.termix.live/api/v1/agents/cmtn3m9fd0c7gwr01h65n2wrj/services"
    )


# ============================================================================
# T4 — unknown path with placeholder: best-effort token_id fallback
# ============================================================================


def test_unknown_placeholder_path_falls_back_to_token_id():
    ep = "https://example.com/agents/{agentId}/other"
    resolved = resolve_agent_id_placeholder(ep, token_id=42, termix_internal_id=None)
    assert resolved == "https://example.com/agents/42/other"


# ============================================================================
# T5 — defensive: no termix_internal_id for services, None; empty handled
# ============================================================================


def test_empty_endpoint_returns_empty():
    assert resolve_agent_id_placeholder("", token_id=1, termix_internal_id=None) == ""


def test_internal_id_empty_string_treated_as_missing():
    resolved = resolve_agent_id_placeholder(_SERVICES, token_id=334432, termix_internal_id="")
    assert resolved is None
