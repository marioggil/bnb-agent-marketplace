"""Add x402 agent-offer evidence columns to `hired_agents`.

Spec: `openspec/changes/x402-agent-hire/spec.md` AC-5 (R7). The marketplace
records what the agent's own x402 endpoint quoted at hire time so the audit
trail is kept even if the agent later changes its price. Four nullable columns
(no data migration): `amount_agent` ($U units, 18 decimals), `pay_to_agent`,
`asset_agent`, `network_agent`. They are populated only when `create_hire`
used the probed offer; null on the flat-price fallback path. No indexes are
added (write-once audit data, never queried).

The migration is symmetric: `downgrade()` drops the columns in reverse order.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013_hired_agent_offer_evidence"
down_revision: Union[str, None] = "0012_compliance_penalty"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the 4 nullable evidence columns (AC-5)."""
    op.add_column(
        "hired_agents",
        sa.Column("amount_agent", sa.Numeric(38, 18), nullable=True),
    )
    op.add_column(
        "hired_agents",
        sa.Column("pay_to_agent", sa.Text(), nullable=True),
    )
    op.add_column(
        "hired_agents",
        sa.Column("asset_agent", sa.Text(), nullable=True),
    )
    op.add_column(
        "hired_agents",
        sa.Column("network_agent", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Drop the evidence columns in reverse order (symmetric with upgrade)."""
    op.drop_column("hired_agents", "network_agent")
    op.drop_column("hired_agents", "asset_agent")
    op.drop_column("hired_agents", "pay_to_agent")
    op.drop_column("hired_agents", "amount_agent")