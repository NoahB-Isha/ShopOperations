"""per-user token epoch (session revocation)

Revision ID: c1f7a4d90b52
Revises: b4e7d19c3f82
Create Date: 2026-08-05 09:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'c1f7a4d90b52'
down_revision = 'b4e7d19c3f82'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default so already-deployed rows get 0 without a backfill pass;
    # tokens minted before this migration carry no epoch, which reads as 0.
    op.add_column(
        'users', sa.Column('token_epoch', sa.Integer(), nullable=False, server_default='0')
    )


def downgrade() -> None:
    op.drop_column('users', 'token_epoch')
