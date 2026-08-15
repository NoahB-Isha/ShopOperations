"""restock floor lines age out instead of repeating forever

An open line used to survive until someone checked it off. In practice nobody
checks off everything, so the list carried the same rows every morning — 20 of
51 open lines on the live stack were 15-19 days old (Noah, 2026-08-15: "the
restock list looks as if it repeated each day"). Lines now expire after
`restock_line_max_age_days`; the row is kept, stamped, and simply stops being
on today's list.

Revision ID: d3b7f21a5c40
Revises: c8e4a1b93f26
"""
import sqlalchemy as sa
from alembic import op

revision = "d3b7f21a5c40"
down_revision = "c8e4a1b93f26"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "restock_lines",
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("restock_lines", "expired_at")
