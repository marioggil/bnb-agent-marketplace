"""Add agent_probes table.

Revision ID: 0006_agent_probes
Revises: 0005
Create Date: 2026-09-01
"""
from alembic import op


revision = "0006_agent_probes"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # agent_probes — append-only A2A probe telemetry (spec P5, design D9).
    # No hard FK to agent_cache: history survives agent_cache churn without
    # write locks. IF NOT EXISTS keeps the migration idempotent like 0005.
    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_probes (
            id BIGSERIAL PRIMARY KEY,
            agent_id TEXT NOT NULL,
            probed_at TIMESTAMPTZ NOT NULL,
            responded BOOLEAN NOT NULL,
            http_status INTEGER,
            latency_ms INTEGER,
            status TEXT,
            presence TEXT,
            endpoint TEXT,
            skills_count INTEGER,
            error TEXT
        )
    """)

    # (agent_id, probed_at DESC) — "latest probe per agent" lookups (D9).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_probes_agent_probed_at "
        "ON agent_probes (agent_id, probed_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agent_probes")