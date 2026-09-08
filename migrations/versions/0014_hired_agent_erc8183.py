"""Add ERC-8183 columns to `hired_agents`.

Spec: docs/category-study.md §ERC-8183 (buyer-side).

Adds five nullable columns to support the new payment rail alongside the
existing x402 (EIP-3009) flow. All columns are nullable so no data
migration is needed; legacy x402 rows simply have `job_id`/`chain_id`/etc
as NULL and continue to work.

  - `rail`           TEXT     — already exists, now defaults to 'eip3009'
                                for legacy rows. New escrow writes use
                                'erc8183'.
  - `job_id`         BIGINT   — on-chain ERC-8183 job id (returned by
                                createJob). Null on x402 rows.
  - `chain_id`       INTEGER  — chain id the job was created on (56 mainnet).
  - `provider_address` TEXT   — agent_wallet / owner_address used as the
                                ERC-8183 job provider. Mirrors the
                                `pay_to` column used by x402.
  - `budget_wei`     NUMERIC(38,0)  — ERC-8183 budget in raw wei (the
                                `expectedBudget` arg to `fund`).

Indexes: `(rail, status)` so the admin view "show all in-flight escrow
hires" stays cheap; `(address, job_id)` is unique so the same on-chain
job can't be recorded twice by accident.

The migration is symmetric: `downgrade()` drops the index and columns in
reverse order.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014_hired_agent_erc8183"
down_revision: Union[str, None] = "0013_hired_agent_offer_evidence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add ERC-8183 columns + the (rail, status) support index."""
    op.add_column(
        "hired_agents",
        sa.Column("job_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "hired_agents",
        sa.Column("chain_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "hired_agents",
        sa.Column("provider_address", sa.Text(), nullable=True),
    )
    op.add_column(
        "hired_agents",
        sa.Column("budget_wei", sa.Numeric(38, 0), nullable=True),
    )
    # Rails on legacy rows (rail was NULL before): backfill to 'eip3009'.
    op.execute("UPDATE hired_agents SET rail = 'eip3009' WHERE rail IS NULL")
    # The rail column already exists but was nullable; legacy x402 rows had
    # it as NULL. The UPDATE above is the data fix; the NOT NULL constraint
    # change below locks future writes to one of the two known rails.
    op.alter_column("hired_agents", "rail", existing_type=sa.Text(), nullable=False)
    op.create_index(
        "ix_hired_agents_rail_status",
        "hired_agents",
        ["rail", "status"],
        unique=False,
    )
    # Dedup guard: one DB row per on-chain job per user.
    op.create_index(
        "ix_hired_agents_address_job",
        "hired_agents",
        ["address", "job_id"],
        unique=True,
        postgresql_where=sa.text("job_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Reverse: drop indexes, drop NOT NULL on rail, drop columns."""
    op.drop_index("ix_hired_agents_address_job", table_name="hired_agents")
    op.drop_index("ix_hired_agents_rail_status", table_name="hired_agents")
    op.alter_column("hired_agents", "rail", existing_type=sa.Text(), nullable=True)
    op.drop_column("hired_agents", "budget_wei")
    op.drop_column("hired_agents", "provider_address")
    op.drop_column("hired_agents", "chain_id")
    op.drop_column("hired_agents", "job_id")
