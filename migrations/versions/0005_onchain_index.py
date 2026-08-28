"""Add onchain_transfers and onchain_agent_events tables.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-27
"""
from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004_hired_payment_cols"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create transfer_type enum (IF NOT EXISTS for idempotency)
    op.execute("DO $$ BEGIN CREATE TYPE transfer_type AS ENUM ('erc20_u', 'erc721_agent'); EXCEPTION WHEN duplicate_object THEN NULL; END $$")

    # Create onchain_transfers table (IF NOT EXISTS for idempotency)
    op.execute("""
        CREATE TABLE IF NOT EXISTS onchain_transfers (
            id BIGSERIAL PRIMARY KEY,
            from_address VARCHAR(42) NOT NULL,
            to_address VARCHAR(42) NOT NULL,
            value NUMERIC(38,18) NOT NULL,
            block_number BIGINT NOT NULL,
            timestamp TIMESTAMPTZ NOT NULL,
            tx_hash VARCHAR(66) NOT NULL,
            transfer_type transfer_type NOT NULL DEFAULT 'erc20_u',
            linked_agent_id TEXT
        )
    """)

    # Indexes (IF NOT EXISTS for idempotency)
    op.execute("CREATE INDEX IF NOT EXISTS ix_onchain_transfers_agent ON onchain_transfers (linked_agent_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_onchain_transfers_block ON onchain_transfers (block_number)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_onchain_transfers_from ON onchain_transfers (from_address)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_onchain_transfers_to ON onchain_transfers (to_address)")
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE onchain_transfers ADD CONSTRAINT uq_transfer_tx UNIQUE (tx_hash, from_address, to_address);
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """)

    # Create onchain_agent_events table (IF NOT EXISTS for idempotency)
    op.execute("""
        CREATE TABLE IF NOT EXISTS onchain_agent_events (
            id BIGSERIAL PRIMARY KEY,
            agent_id TEXT NOT NULL,
            token_id BIGINT NOT NULL,
            event_type VARCHAR(32) NOT NULL,
            from_address VARCHAR(42) NOT NULL,
            to_address VARCHAR(42) NOT NULL,
            block_number BIGINT NOT NULL,
            timestamp TIMESTAMPTZ NOT NULL,
            tx_hash VARCHAR(66) NOT NULL
        )
    """)

    op.execute("CREATE INDEX IF NOT EXISTS ix_onchain_agent_events_agent ON onchain_agent_events (agent_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_onchain_agent_events_block ON onchain_agent_events (block_number)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS onchain_agent_events")
    op.execute("DROP TABLE IF EXISTS onchain_transfers")
    op.execute("DROP TYPE IF EXISTS transfer_type")
