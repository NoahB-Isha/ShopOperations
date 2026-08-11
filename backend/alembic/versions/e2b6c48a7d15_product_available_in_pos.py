"""products.available_in_pos (Odoo "Available in POS")

Revision ID: e2b6c48a7d15
Revises: a7c3e91d5f24
Create Date: 2026-08-11 18:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'e2b6c48a7d15'
down_revision = 'a7c3e91d5f24'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Default TRUE so deployed rows stay visible until the next product sync
    # fills the real value — hiding a live SKU is worse than showing a dead one.
    op.add_column(
        'products',
        sa.Column('available_in_pos', sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column('products', 'available_in_pos')
