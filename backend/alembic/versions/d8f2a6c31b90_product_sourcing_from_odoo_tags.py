"""product sourcing from odoo tags

Revision ID: d8f2a6c31b90
Revises: c9d4e8b2f7a1
Create Date: 2026-07-27 18:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'd8f2a6c31b90'
down_revision = 'c9d4e8b2f7a1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'products',
        sa.Column('sourcing', sa.String(length=10), nullable=False, server_default=''),
    )


def downgrade() -> None:
    op.drop_column('products', 'sourcing')
