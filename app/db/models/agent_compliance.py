"""AgentComplianceFlag model — one OFAC compliance row per agent.

Spec: `openspec/changes/compliance-flags/spec.md` AC-2 + the requirements
section "Storage — per-agent flag table `agent_compliance_flags`". This is
the per-agent state derived from `flagged_addresses` (the OFAC mirror) by
the `run_compliance_refresh()` orchestrator (1a-ii). Phase 1a-i ships only
the schema + model + pure helper — the model is intentionally **not**
imported from `app/db/models/__init__.py` (the codebase uses lazy
discovery in 1a-ii's service), so consumers import this module directly.

Columns mirror the migration `0012_compliance_penalty.py` 1:1:

    agent_id                String(255)   PK     — matches AgentCache.agent_id shape
    creator_flagged         Boolean               — creator_address ∈ mirror
    creator_flag_sources    JSONB                 — list of source tags (ofac-bsc, ofac-eth)
    owner_flagged           Boolean               — owner_address   ∈ mirror
    owner_flag_sources      JSONB                 — list of source tags
    creator_is_owner        Boolean               — lower(creator) == lower(owner)
    flagged_data_stale      Boolean               — mirror older than 24h
    refreshed_at            timestamptz           — set on every upsert (1a-ii)
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

_AGENT_ID_LEN: int = 255


class AgentComplianceFlag(Base):
    """One row per agent recording creator/owner OFAC flag state."""

    __tablename__ = "agent_compliance_flags"

    agent_id: Mapped[str] = mapped_column(String(_AGENT_ID_LEN), primary_key=True)
    creator_flagged: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    creator_flag_sources: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    owner_flagged: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    owner_flag_sources: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    creator_is_owner: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    flagged_data_stale: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    refreshed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"AgentComplianceFlag(agent_id={self.agent_id!r}, "
            f"creator_flagged={self.creator_flagged!r}, owner_flagged={self.owner_flagged!r})"
        )


__all__ = ["AgentComplianceFlag"]
