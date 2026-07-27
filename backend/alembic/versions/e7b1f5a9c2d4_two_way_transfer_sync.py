"""two-way transfer sync: staging inbound snapshot + outbound poll stamp

Revision ID: e7b1f5a9c2d4
Revises: a4e9d27c81b3
Create Date: 2026-07-27 09:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'e7b1f5a9c2d4'
down_revision = 'a4e9d27c81b3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('staging_inbound_moves',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('odoo_picking_id', sa.Integer(), nullable=False),
    sa.Column('picking_name', sa.String(length=80), nullable=False),
    sa.Column('picking_state', sa.String(length=20), nullable=False),
    sa.Column('product_id', sa.Integer(), nullable=False),
    sa.Column('qty', sa.Float(), nullable=False),
    sa.Column('expected_date', sa.Date(), nullable=True),
    sa.Column('synced_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], name=op.f('fk_staging_inbound_moves_product_id_products')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_staging_inbound_moves'))
    )
    op.create_index(op.f('ix_staging_inbound_moves_odoo_picking_id'), 'staging_inbound_moves', ['odoo_picking_id'], unique=False)
    op.create_index(op.f('ix_staging_inbound_moves_product_id'), 'staging_inbound_moves', ['product_id'], unique=False)

    op.add_column(
        'transfer_requests',
        sa.Column('picking_checked_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('transfer_requests', 'picking_checked_at')
    op.drop_index(op.f('ix_staging_inbound_moves_product_id'), table_name='staging_inbound_moves')
    op.drop_index(op.f('ix_staging_inbound_moves_odoo_picking_id'), table_name='staging_inbound_moves')
    op.drop_table('staging_inbound_moves')
