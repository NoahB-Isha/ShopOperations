"""staging2 pallet transfers

Revision ID: c9d4e8b2f7a1
Revises: e7b1f5a9c2d4
Create Date: 2026-07-27 12:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = 'c9d4e8b2f7a1'
down_revision = 'e7b1f5a9c2d4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('pallet_transfers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('picking_status', sa.String(length=12), nullable=False),
    sa.Column('picking_reference', sa.String(length=40), nullable=False),
    sa.Column('picking_error', sa.Text(), nullable=False),
    sa.Column('odoo_picking_id', sa.Integer(), nullable=True),
    sa.Column('odoo_picking_name', sa.String(length=80), nullable=False),
    sa.Column('odoo_picking_url', sa.String(length=500), nullable=False),
    sa.Column('lines', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('validated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('checked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_pallet_transfers_created_by_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_pallet_transfers'))
    )


def downgrade() -> None:
    op.drop_table('pallet_transfers')
