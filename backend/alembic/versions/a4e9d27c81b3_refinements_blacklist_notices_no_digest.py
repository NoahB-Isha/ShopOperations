"""refinements: product blacklist, admin notices, drop digest subscriptions

Revision ID: a4e9d27c81b3
Revises: f3c8e21b7a54
Create Date: 2026-07-25 09:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'a4e9d27c81b3'
down_revision = 'f3c8e21b7a54'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'products',
        sa.Column('blacklisted', sa.Boolean(), nullable=False, server_default='false'),
    )

    op.create_table('notices',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_notices_created_by_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_notices'))
    )

    op.create_table('notice_reads',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('notice_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['notice_id'], ['notices.id'], name=op.f('fk_notice_reads_notice_id_notices')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_notice_reads_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_notice_reads')),
    sa.UniqueConstraint('notice_id', 'user_id', name=op.f('uq_notice_read'))
    )
    op.create_index(op.f('ix_notice_reads_notice_id'), 'notice_reads', ['notice_id'], unique=False)
    op.create_index(op.f('ix_notice_reads_user_id'), 'notice_reads', ['user_id'], unique=False)

    # the availability email digest is gone — feature removed entirely
    op.drop_table('digest_subscriptions')


def downgrade() -> None:
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
    op.drop_index(op.f('ix_notice_reads_user_id'), table_name='notice_reads')
    op.drop_index(op.f('ix_notice_reads_notice_id'), table_name='notice_reads')
    op.drop_table('notice_reads')
    op.drop_table('notices')
    op.drop_column('products', 'blacklisted')
