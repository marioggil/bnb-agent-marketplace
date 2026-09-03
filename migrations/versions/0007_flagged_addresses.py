"""Add flagged_addresses table (T2 wallet risk flags).

Revision ID: 0007_flagged_addresses
Revises: 0006_agent_probes
Create Date: 2026-09-03
"""
from alembic import op


revision = "0007_flagged_addresses"
down_revision = "0006_agent_probes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # flagged_addresses — OFAC-sanctioned wallet addresses mirrored from the
    # 0xB10C nightly lists (T2 trust signal, DESIGN.md). `address` (lowercase
    # hex) + `source` form a COMPOSITE PK: the same wallet may appear in both
    # the BSC and ETH sanctioned lists (EVM address space), so one row per
    # source is the convergent shape. Rows are swapped per source by the
    # flagged-address sync (DELETE + INSERT). The IF NOT EXISTS keeps the
    # migration idempotent like 0005/0006.
    op.execute("""
        CREATE TABLE IF NOT EXISTS flagged_addresses (
            address VARCHAR(42) NOT NULL,
            source VARCHAR(64) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (address, source)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS flagged_addresses")