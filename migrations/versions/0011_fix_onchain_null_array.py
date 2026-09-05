"""Fix object-less onchain arrays left by migration 0010.

The original 0010 migration wrapped JSON `null` values via
`jsonb_build_array(null::jsonb)` which produced `[null]` — arrays that
pass `jsonb_typeof(...) = 'array'` but contain no dict entries. Downstream
code (`_onchain_value`) iterates entries looking for `isinstance(entry,
dict)`, so it silently returns None for these rows, breaking platform
detection for EvoEvo agents whose onchain came in as JSON null.

This migration normalizes those empty/object-less arrays to `[]` so the
heuristic fallback in `_build_agent_profile` can re-derive the platform
from `services.web.endpoint` when needed.

Revision ID: 0011_fix_onchain_null_array
Revises: 0010_onchain_array
Create Date: 2026-09-05
"""
from alembic import op


revision = "0011_fix_onchain_null_array"
down_revision = "0010_onchain_array"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # An "object-less" onchain array is one whose elements are all null
    # (or non-object scalars) — produced by 0010 wrapping JSON null with
    # jsonb_build_array, or by sync data that stored literal [null, ...].
    # Normalize to [] so the heuristic fallback in _build_agent_profile
    # can re-derive the platform from services.web.endpoint instead of
    # silently dropping the lookup.
    op.execute(
        """
        UPDATE agent_cache
        SET raw_metadata = jsonb_set(raw_metadata, '{onchain}', '[]'::jsonb)
        WHERE raw_metadata IS NOT NULL
          AND raw_metadata->'onchain' IS NOT NULL
          AND jsonb_typeof(raw_metadata->'onchain') = 'array'
          AND NOT EXISTS (
              SELECT 1
              FROM jsonb_array_elements(raw_metadata->'onchain') AS e
              WHERE jsonb_typeof(e) = 'object'
          )
        """
    )


def downgrade() -> None:
    # No meaningful downgrade — once normalized to [], we cannot
    # reconstruct whether the original was JSON null, [null], or empty.
    pass
