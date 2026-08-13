""""not this week" for computed warehouse suggestions

A Floor Team ask can be settled for good (a person judged it). A computed
suggestion can't — the numbers will keep saying the same thing — so swiping
one away parks it for a week instead.

Revision ID: c8e4a1b93f26
Revises: b5f18c26d3a7
"""
import sqlalchemy as sa
from alembic import op

revision = "c8e4a1b93f26"
down_revision = "b5f18c26d3a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "suggestion_snoozes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("snoozed_until", sa.Date(), nullable=False),
        sa.Column("snoozed_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.ForeignKeyConstraint(["snoozed_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_suggestion_snoozes_product_id", "suggestion_snoozes", ["product_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_suggestion_snoozes_product_id", table_name="suggestion_snoozes")
    op.drop_table("suggestion_snoozes")
