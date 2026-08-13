"""floor team item requests

The Floor Team can't create transfers, but they know what the shelves need.
Their ask lands here and shows up on the Inventory Flow Manager's "Suggested
items" page above the app's own computed suggestions.

Revision ID: b5f18c26d3a7
Revises: a7c3e91d64b8
"""
import sqlalchemy as sa
from alembic import op

revision = "b5f18c26d3a7"
down_revision = "a7c3e91d64b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "floor_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=12), nullable=False, server_default="open"),
        sa.Column("requested_by_id", sa.Integer(), nullable=True),
        sa.Column("resolved_by_id", sa.Integer(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.ForeignKeyConstraint(["requested_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["resolved_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_floor_requests_product_id", "floor_requests", ["product_id"])
    op.create_index("ix_floor_requests_status", "floor_requests", ["status"])


def downgrade() -> None:
    op.drop_index("ix_floor_requests_status", table_name="floor_requests")
    op.drop_index("ix_floor_requests_product_id", table_name="floor_requests")
    op.drop_table("floor_requests")
