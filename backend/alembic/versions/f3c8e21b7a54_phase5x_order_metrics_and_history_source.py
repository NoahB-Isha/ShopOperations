"""phase5x order metrics and history source

Revision ID: f3c8e21b7a54
Revises: d1a7c9f42e10
Create Date: 2026-07-22 09:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'f3c8e21b7a54'
down_revision = 'd1a7c9f42e10'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('sales_orders_monthly',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('year', sa.Integer(), nullable=False),
    sa.Column('month', sa.Integer(), nullable=False),
    sa.Column('channel', sa.String(length=20), nullable=False),
    sa.Column('orders', sa.Integer(), nullable=False),
    sa.Column('amount', sa.Float(), nullable=False),
    sa.Column('orders_with_customer', sa.Integer(), nullable=False),
    sa.Column('distinct_customers', sa.Integer(), nullable=False),
    sa.Column('new_customers', sa.Integer(), nullable=False),
    sa.Column('returning_customers', sa.Integer(), nullable=False),
    sa.Column('synced_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sales_orders_monthly')),
    sa.UniqueConstraint('year', 'month', 'channel', name=op.f('uq_sales_orders_bucket'))
    )

    op.create_table('customer_first_seen',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('partner_id', sa.Integer(), nullable=False),
    sa.Column('channel', sa.String(length=20), nullable=False),
    sa.Column('first_order_on', sa.Date(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_customer_first_seen')),
    sa.UniqueConstraint('partner_id', 'channel', name=op.f('uq_customer_first_seen'))
    )
    op.create_index(op.f('ix_customer_first_seen_partner_id'), 'customer_first_seen', ['partner_id'], unique=False)

    op.add_column(
        'stock_snapshot_days',
        sa.Column('source', sa.String(length=16), nullable=False, server_default='sync'),
    )


def downgrade() -> None:
    op.drop_column('stock_snapshot_days', 'source')
    op.drop_index(op.f('ix_customer_first_seen_partner_id'), table_name='customer_first_seen')
    op.drop_table('customer_first_seen')
    op.drop_table('sales_orders_monthly')
