"""restock line snooze ("not today", back tomorrow)

Revision ID: a7c3e91d5f24
Revises: c1f7a4d90b52
Create Date: 2026-08-07 16:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'a7c3e91d5f24'
down_revision = 'c1f7a4d90b52'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable: NULL = never snoozed, which is every existing row.
    op.add_column('restock_lines', sa.Column('snoozed_until', sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column('restock_lines', 'snoozed_until')
