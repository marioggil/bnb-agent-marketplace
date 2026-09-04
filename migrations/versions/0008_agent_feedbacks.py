"""Add agent_feedbacks table (individual agent reviews).

Revision ID: 0008_agent_feedbacks
Revises: 0007_flagged_addresses
Create Date: 2026-09-03
"""
from alembic import op


revision = "0008_agent_feedbacks"
down_revision = "0007_flagged_addresses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # agent_feedbacks — individual reviews mirrored from the 8004scan
    # /api/v1/feedbacks endpoint (agent-feedbacks). `feedback_id` is the
    # upstream id ("56:137:0x…:1") and the natural PK: the sync upserts on
    # it, so re-runs converge instead of duplicating. Hard FK to
    # agent_cache(agent_id) with CASCADE — reviews belong to a cached
    # agent and must disappear with it. The composite (agent_id,
    # submitted_at DESC) index serves both the per-agent listing and the
    # newest-first order; agent_id is its leading column, so per-agent
    # lookups never need a second index. IF NOT EXISTS keeps the migration
    # idempotent like 0005/0006/0007.
    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_feedbacks (
            feedback_id VARCHAR(160) PRIMARY KEY,
            agent_id TEXT NOT NULL REFERENCES agent_cache(agent_id) ON DELETE CASCADE,
            chain_id INTEGER NOT NULL,
            token_id INTEGER NOT NULL,
            user_address VARCHAR(42),
            score INTEGER,
            comment TEXT,
            tag1 VARCHAR(64),
            tag2 VARCHAR(64),
            tx_hash VARCHAR(66),
            block_number BIGINT,
            submitted_at TIMESTAMPTZ,
            is_revoked BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # (agent_id, submitted_at DESC) — "reviews for this agent, newest first"
    # lookups (the listing order of the /feedbacks endpoint).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_feedbacks_agent_submitted_at "
        "ON agent_feedbacks (agent_id, submitted_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agent_feedbacks")
