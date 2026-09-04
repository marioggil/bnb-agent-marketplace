"""Drop x402 from the category default.

x402 support is a payment rail, not a category signal: the old GENERATED
expression grouped every x402 agent (2,840 in prod) as 'rebalancing'.
Postgres cannot redefine a GENERATED expression in place (no SET
EXPRESSION), so this migration drops the expression, fixes the stored
values, and pins the column to a plain DEFAULT 'other'. Only oasf rows
keep 'rebalancing'.

Revision ID: 0009_category_drop_x402_default
Revises: 0008_agent_feedbacks
Create Date: 2026-09-04
"""
from alembic import op


revision = "0009_category_drop_x402_default"
down_revision = "0008_agent_feedbacks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) Column becomes a plain column (values are preserved as stored).
    op.execute("ALTER TABLE agent_cache ALTER COLUMN category DROP EXPRESSION")
    # 2) Fix rows that carry the old x402-derived default: only oasf rows
    #    may stay 'rebalancing'.
    op.execute(
        "UPDATE agent_cache SET category = 'other' "
        "WHERE category = 'rebalancing' "
        "AND NOT (supported_protocols ? 'oasf')"
    )
    # 3) Plain default for rows inserted without an explicit category.
    op.execute("ALTER TABLE agent_cache ALTER COLUMN category SET DEFAULT 'other'")


def downgrade() -> None:
    # Recreate the original GENERATED column (Postgres cannot SET a
    # generated expression on an existing column).
    op.execute(
        "ALTER TABLE agent_cache ADD COLUMN category_new TEXT GENERATED ALWAYS AS ("
        "CASE "
        "WHEN x402_supported THEN 'rebalancing' "
        "WHEN supported_protocols ? 'oasf' THEN 'rebalancing' "
        "ELSE 'other' "
        "END"
        ") STORED"
    )
    op.execute("ALTER TABLE agent_cache DROP COLUMN category")
    op.execute("ALTER TABLE agent_cache RENAME COLUMN category_new TO category")