"""0003_agent_cache_health_status_jsonb — 8004scan returns health_status as a dict.

The 0002 migration created `health_status` as TEXT, mirroring the
shallow listing endpoint. The detail endpoint (used by the seed
and the production sync after the iter_agents/get_agent envelope
fix) returns health_status as a structured object with per-service
health metadata, e.g.

    {
        "services": {
            "mcp": {
                "status": "verifiable",
                "verifiable_count": 1,
                "last_checked_at": "..."
            }
        }
    }

JSONB is the right home for it (allows per-service filtering and
GIN if we ever want to). The USING clause falls back gracefully when
the existing rows are null, which is the common case.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0003_health_status_jsonb"
down_revision: Union[str, None] = "0002_enrich_agent_cache"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "agent_cache",
        "health_status",
        existing_type=sa.Text(),
        type_=postgresql.JSONB(astext_type=sa.Text()),
        postgresql_using=(
            "CASE WHEN health_status IS NULL OR health_status = '' "
            "THEN NULL ELSE health_status::jsonb END"
        ),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "agent_cache",
        "health_status",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        type_=sa.Text(),
        postgresql_using=(
            "CASE WHEN health_status IS NULL THEN NULL "
            "ELSE health_status::text END"
        ),
        existing_nullable=True,
    )
