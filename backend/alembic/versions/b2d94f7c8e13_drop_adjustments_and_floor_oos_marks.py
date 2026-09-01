"""feature merge: drop the adjustments queue and floor OOS marks

The adjustments queue (counted-vs-sent discrepancies) had no nav entry and
no reviewer; the transfer DISCREPANCY event and the validated count picking
in Odoo are the durable records. Floor OOS marks (and their draft
reduction/addition pickings) are gone with the rest of ad-hoc counting —
counted numbers now enter the app ONLY through the counting page
(Noah, 2026-08-24).

Revision ID: b2d94f7c8e13
Revises: f1a83c6d2b57
Create Date: 2026-08-24 16:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'b2d94f7c8e13'
down_revision = 'f1a83c6d2b57'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table('adjustments')
    op.drop_table('floor_oos_marks')


def downgrade() -> None:
    # Recreate the schemas (rows are gone for good — the app-side history was
    # judged disposable when the features were removed).
    op.create_table(
        'adjustments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('request_id', sa.Integer(), nullable=True),
        sa.Column('line_id', sa.Integer(), nullable=True),
        sa.Column('pallet_id', sa.Integer(), nullable=True),
        sa.Column('product_id', sa.Integer(), nullable=False),
        sa.Column('qty_expected', sa.Float(), nullable=False),
        sa.Column('qty_counted', sa.Float(), nullable=False),
        sa.Column('delta', sa.Float(), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('note', sa.Text(), nullable=False),
        sa.Column('resolution_note', sa.Text(), nullable=False),
        sa.Column('resolved_by_id', sa.Integer(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['request_id'], ['transfer_requests.id'],
            name=op.f('fk_adjustments_request_id_transfer_requests'),
        ),
        sa.ForeignKeyConstraint(
            ['line_id'], ['transfer_request_lines.id'],
            name=op.f('fk_adjustments_line_id_transfer_request_lines'),
        ),
        sa.ForeignKeyConstraint(
            ['pallet_id'], ['pallet_transfers.id'],
            name=op.f('fk_adjustments_pallet_id_pallet_transfers'),
        ),
        sa.ForeignKeyConstraint(
            ['product_id'], ['products.id'],
            name=op.f('fk_adjustments_product_id_products'),
        ),
        sa.ForeignKeyConstraint(
            ['resolved_by_id'], ['users.id'],
            name=op.f('fk_adjustments_resolved_by_id_users'),
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_adjustments')),
    )
    op.create_index(op.f('ix_adjustments_request_id'), 'adjustments', ['request_id'])
    op.create_index(op.f('ix_adjustments_pallet_id'), 'adjustments', ['pallet_id'])
    op.create_index(op.f('ix_adjustments_status'), 'adjustments', ['status'])

    op.create_table(
        'floor_oos_marks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=False),
        sa.Column('note', sa.Text(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('qty_removed', sa.Float(), nullable=False),
        sa.Column('picking_status', sa.String(length=12), nullable=False),
        sa.Column('picking_reference', sa.String(length=40), nullable=False),
        sa.Column('picking_error', sa.Text(), nullable=False),
        sa.Column('odoo_picking_id', sa.Integer(), nullable=True),
        sa.Column('odoo_picking_name', sa.String(length=80), nullable=False),
        sa.Column('odoo_picking_url', sa.String(length=500), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['product_id'], ['products.id'],
            name=op.f('fk_floor_oos_marks_product_id_products'),
        ),
        sa.ForeignKeyConstraint(
            ['created_by_id'], ['users.id'],
            name=op.f('fk_floor_oos_marks_created_by_id_users'),
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_floor_oos_marks')),
    )
    op.create_index(op.f('ix_floor_oos_marks_product_id'), 'floor_oos_marks', ['product_id'])
