"""Normalize raw_metadata.onchain to JSONB array.

Some agents have `raw_metadata.onchain` stored as a scalar (string) instead
of a JSONB array (data shape drift across sync versions / a 8004scan
upstream change). The platform filter in `pages.py::_platform_expression`
crashed the homepage on those rows with:

    asyncpg.exceptions.InvalidParameterValueError: cannot extract elements
    from a scalar

because `jsonb_array_elements()` cannot iterate a scalar. This migration
wraps any non-array `onchain` value in `jsonb_build_array(...)` so every
row has a consistent array shape — defensive even after the SQL is
rewritten to use the `@>` containment operator (which tolerates the
inconsistent shape, but the marketplace shouldn't carry it forever).

Revision ID: 0010_onchain_array
Revises: 0009_category_drop_x402_default
Create Date: 2026-09-05
"""
from alembic import op


revision = "0010_onchain_array"
down_revision = "0009_category_drop_x402_default"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Backfill: ensure raw_metadata.onchain is always a JSONB array.
    #   - NULL / missing            -> '[]'
    #   - already a JSONB array     -> leave as-is
    #   - scalar (string/number/obj)-> wrap in jsonb_build_array(...)
    # COALESCE handles rows where raw_metadata itself is NULL.
    # Idempotent: the WHERE clause matches only rows that still need
    # normalization, so re-running the migration is a no-op once converged.
    op.execute(
        """
        UPDATE agent_cache
        SET raw_metadata = jsonb_set(
            COALESCE(raw_metadata, '{}'::jsonb),
            '{onchain}',
            CASE
                WHEN raw_metadata->'onchain' IS NULL THEN '[]'::jsonb
                WHEN jsonb_typeof(raw_metadata->'onchain') = 'array'
                    THEN raw_metadata->'onchain'
                ELSE jsonb_build_array(raw_metadata->'onchain')
            END
        )
        WHERE raw_metadata IS NULL
           OR raw_metadata->'onchain' IS NULL
           OR jsonb_typeof(raw_metadata->'onchain') <> 'array'
        """
    )


def downgrade() -> None:
    # No meaningful downgrade — once a scalar is wrapped in an array we
    # cannot reconstruct the original shape (string vs single object).
    # Leaving data normalized is the correct steady state.
    pass
