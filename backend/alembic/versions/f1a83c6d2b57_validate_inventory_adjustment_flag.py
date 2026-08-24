"""feature flag: write_validate_inventory_adjustment

Revision ID: f1a83c6d2b57
Revises: f5c1a8e37d92
Create Date: 2026-08-22 16:00:00.000000

Feature flags normally arrive with the seed, but the deployed stack only runs
`alembic upgrade head` — no seed — so a flag that isn't inserted here can never
be turned on there either (`PUT /admin/flags/{key}` 404s on a missing row).

It goes in ENABLED, which is not the usual posture for a write flag. The usual
posture exists so a new write op can't surprise anyone; this one IS the
request (Noah, 2026-08-22: counts should end up validated, not drafted), and
shipping it off would deliver a feature that silently does nothing. Turning it
off again in Dev Tools restores exactly the old behaviour: a draft, and a link
for a human.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'f1a83c6d2b57'
down_revision = 'f5c1a8e37d92'
branch_labels = None
depends_on = None

KEY = 'write_validate_inventory_adjustment'
DESCRIPTION = (
    'OdooWriter.validate_adjustment may POST approved inventory-count adjustments '
    '(this one MOVES STOCK — every other write stops at a draft). '
    'Off = the adjustment is created and left for a human to validate.'
)


def upgrade() -> None:
    # INSERT … WHERE NOT EXISTS: a stack that already seeded the flag keeps
    # whatever state a human chose for it.
    op.execute(
        sa.text(
            # updated_at's default is Python-side (the ORM's), so a raw
            # INSERT has to supply it or the NOT NULL bites.
            "INSERT INTO feature_flags (key, enabled, description, updated_at) "
            "SELECT :key, true, :description, CURRENT_TIMESTAMP "
            "WHERE NOT EXISTS (SELECT 1 FROM feature_flags WHERE key = :key)"
        ).bindparams(key=KEY, description=DESCRIPTION)
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM feature_flags WHERE key = :key").bindparams(key=KEY))
