"""Add onchain_transfers and onchain_agent_events tables.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-27
"""
from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create transfer_type enum
    op.execute("CREATE TYPE transfer_type AS ENUM ('erc20_u', 'erc721_agent')")

    # Create onchain_transfers table
    op.create_table(
        "onchain_transfers",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("from_address", sa.String(42), nullable=False),
        sa.Column("to_address", sa.String(42), nullable=False),
        sa.Column("value", sa.Numeric(38, 18), nullable=False),
        sa.Column("block_number", sa.BigInteger(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tx_hash", sa.String(66), nullable=False),
        sa.Column(
            "transfer_type",
            sa.Enum("erc20_u", "erc721_agent", name="transfer_type"),
            nullable=False,
            server_default="erc20_u",
        ),
        sa.Column("linked_agent_id", sa.Text(), nullable=True),
    )

    # Indexes
    op.create_index(
        "ix_onchain_transfers_agent",
        "onchain_transfers",
        ["linked_agent_id"],
    )
    op.create_index(
        "ix_onchain_transfers_block",
        "onchain_transfers",
        ["block_number"],
    )
    op.create_index(
        "ix_onchain_transfers_from",
        "onchain_transfers",
        ["from_address"],
    )
    op.create_index(
        "ix_onchain_transfers_to",
        "onchain_transfers",
        ["to_address"],
    )
    op.create_unique_constraint(
        "uq_transfer_tx",
        "onchain_transfers",
        ["tx_hash", "from_address", "to_address"],
    )

    # Create onchain_agent_events table
    op.create_table(
        "onchain_agent_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("token_id", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("from_address", sa.String(42), nullable=False),
        sa.Column("to_address", sa.String(42), nullable=False),
        sa.Column("block_number", sa.BigInteger(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tx_hash", sa.String(66), nullable=False),
    )

    op.create_index(
        "ix_onchain_agent_events_agent",
        "onchain_agent_events",
        ["agent_id"],
    )
    op.create_index(
        "ix_onchain_agent_events_block",
        "onchain_agent_events",
        ["block_number"],
    )


def downgrade() -> None:
    op.drop_table("onchain_agent_events")
    op.drop_table("onchain_transfers")
    op.execute("DROP TYPE transfer_type")
