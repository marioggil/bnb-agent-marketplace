"""AgentProbe model — append-only A2A probe telemetry.

Spec: `sdd/agent-score/spec` agent-probes P5. Design D9:
- one row per probe per agent (append-only history, two cycles → two rows)
- `agent_id` has NO hard FK to `agent_cache` — telemetry survives
  `agent_cache` churn without write locks or cascade constraints
- `(agent_id, probed_at DESC)` index serves the "latest probe" lookups and
  the history read for the /score detail

`responded` follows spec P3: True for 2xx/401, False for 404/5xx/timeout/
parse error. `http_status`/`latency_ms` are recorded even when the probe did
not respond so failures stay measurable.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AgentProbe(Base):
    """One status-aware A2A probe of an agent's Termix card endpoint."""

    __tablename__ = "agent_probes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False)
    probed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    responded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str | None] = mapped_column(Text, nullable=True)
    presence: Mapped[str | None] = mapped_column(Text, nullable=True)
    endpoint: Mapped[str | None] = mapped_column(Text, nullable=True)
    skills_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("ix_agent_probes_agent_probed_at", "agent_id", text("probed_at DESC")),)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"AgentProbe(agent_id={self.agent_id!r}, probed_at={self.probed_at!r})"
