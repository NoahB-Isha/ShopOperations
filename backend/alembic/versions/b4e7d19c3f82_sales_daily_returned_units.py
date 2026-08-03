"""sales_daily returned units (POS refunds split out of the net)

Revision ID: b4e7d19c3f82
Revises: d8f2a6c31b90
Create Date: 2026-08-03 12:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'b4e7d19c3f82'
down_revision = 'd8f2a6c31b90'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # nullable, no backfill: NULL = row synced before returns capture
    # (unknown), 0 = genuinely no returns. The sales sync rebuilds the
    # recent daily window on every run, so fresh rows fill in on their own.
    op.add_column('sales_daily', sa.Column('returned_units', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('sales_daily', 'returned_units')
