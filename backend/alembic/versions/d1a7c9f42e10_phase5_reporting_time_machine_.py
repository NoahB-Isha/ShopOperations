"""phase5 reporting time machine availability

Revision ID: d1a7c9f42e10
Revises: b8539cb5b38a
Create Date: 2026-07-21 10:05:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'd1a7c9f42e10'
down_revision = 'b8539cb5b38a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('stock_snapshots',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('snapshot_date', sa.Date(), nullable=False),
    sa.Column('product_id', sa.Integer(), nullable=False),
    sa.Column('location_key', sa.String(length=20), nullable=False),
    sa.Column('qty', sa.Float(), nullable=False),
    sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], name=op.f('fk_stock_snapshots_product_id_products')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_stock_snapshots')),
    sa.UniqueConstraint('snapshot_date', 'product_id', 'location_key', name=op.f('uq_stock_snapshot_bucket'))
    )
    op.create_index(op.f('ix_stock_snapshots_snapshot_date'), 'stock_snapshots', ['snapshot_date'], unique=False)
    op.create_index('ix_stock_snapshots_product_date', 'stock_snapshots', ['product_id', 'snapshot_date'], unique=False)

    op.create_table('stock_snapshot_days',
    sa.Column('snapshot_date', sa.Date(), nullable=False),
    sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('rows', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('snapshot_date', name=op.f('pk_stock_snapshot_days'))
    )

    op.create_table('sales_center_monthly',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('config_name', sa.String(length=120), nullable=False),
    sa.Column('center_id', sa.Integer(), nullable=True),
    sa.Column('year', sa.Integer(), nullable=False),
    sa.Column('month', sa.Integer(), nullable=False),
    sa.Column('units', sa.Float(), nullable=False),
    sa.Column('amount', sa.Float(), nullable=True),
    sa.Column('synced_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['center_id'], ['centers.id'], name=op.f('fk_sales_center_monthly_center_id_centers')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sales_center_monthly')),
    sa.UniqueConstraint('config_name', 'year', 'month', name=op.f('uq_sales_center_bucket'))
    )

    op.create_table('digest_subscriptions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('cadence', sa.String(length=10), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('last_sent_on', sa.Date(), nullable=True),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_digest_subscriptions_created_by_id_users')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_digest_subscriptions_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_digest_subscriptions')),
    sa.UniqueConstraint('email', 'kind', name=op.f('uq_digest_email_kind'))
    )

    # revenue capture on the sales snapshot (nullable — rows synced before the
    # split stay NULL until an admin re-runs the sales backfill)
    op.add_column('sales_monthly', sa.Column('amount', sa.Float(), nullable=True))
    op.add_column('sales_daily', sa.Column('amount', sa.Float(), nullable=True))
    op.create_index('ix_sales_monthly_year_month', 'sales_monthly', ['year', 'month'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_sales_monthly_year_month', table_name='sales_monthly')
    op.drop_column('sales_daily', 'amount')
    op.drop_column('sales_monthly', 'amount')
    op.drop_table('digest_subscriptions')
    op.drop_table('sales_center_monthly')
    op.drop_table('stock_snapshot_days')
    op.drop_index('ix_stock_snapshots_product_date', table_name='stock_snapshots')
    op.drop_index(op.f('ix_stock_snapshots_snapshot_date'), table_name='stock_snapshots')
    op.drop_table('stock_snapshots')
