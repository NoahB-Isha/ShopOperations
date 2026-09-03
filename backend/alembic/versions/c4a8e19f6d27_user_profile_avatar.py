"""profile personalization: avatar icon + color, first-login setup stamp

Every existing user has profile_setup_at NULL, so the whole team gets the
first-login setup dialog exactly once after this deploys — which IS the
rollout (they never had the chance to pick).

Revision ID: c4a8e19f6d27
Revises: b2d94f7c8e13
Create Date: 2026-09-01 18:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'c4a8e19f6d27'
down_revision = 'b2d94f7c8e13'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('avatar_icon', sa.String(length=40), nullable=False, server_default=''),
    )
    op.add_column(
        'users',
        sa.Column('avatar_color', sa.String(length=20), nullable=False, server_default=''),
    )
    op.add_column(
        'users',
        sa.Column('profile_setup_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('users', 'profile_setup_at')
    op.drop_column('users', 'avatar_color')
    op.drop_column('users', 'avatar_icon')
