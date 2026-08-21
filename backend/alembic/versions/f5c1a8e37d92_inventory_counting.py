"""inventory counting: submissions, items, append-only entries, review trail

Four new tables and nothing touched. The shape follows the spec's hardest
rule — never overwrite a previous count — so each act of counting is its own
`inventory_count_entries` row (attempt 1 is the original, 2+ are recounts),
carrying the Odoo quantity as it stood when THAT count was made.

Purely additive: no existing table is altered, so this is safe to run on the
hosted stack while the feature is still behind its nav entries.

Revision ID: f5c1a8e37d92
Revises: e4a7c2b91d63
Create Date: 2026-08-19 09:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f5c1a8e37d92"
down_revision = "e4a7c2b91d63"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inventory_counts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("location_key", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("counted_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_inventory_counts_location_key", "inventory_counts", ["location_key"])
    op.create_index("ix_inventory_counts_status", "inventory_counts", ["status"])
    op.create_index("ix_inventory_counts_counted_by_id", "inventory_counts", ["counted_by_id"])

    op.create_table(
        "inventory_count_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "count_id", sa.Integer(), sa.ForeignKey("inventory_counts.id"), nullable=False
        ),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column(
            "recount_assignee_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("reviewed_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("picking_status", sa.String(length=12), nullable=False, server_default="none"),
        sa.Column("picking_reference", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("picking_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("odoo_picking_id", sa.Integer(), nullable=True),
        sa.Column("odoo_picking_name", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("odoo_picking_url", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("applied_qty", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("count_id", "product_id", name="uq_count_product"),
    )
    op.create_index("ix_inventory_count_items_count_id", "inventory_count_items", ["count_id"])
    op.create_index(
        "ix_inventory_count_items_product_id", "inventory_count_items", ["product_id"]
    )
    op.create_index("ix_inventory_count_items_status", "inventory_count_items", ["status"])
    op.create_index(
        "ix_inventory_count_items_recount_assignee_id",
        "inventory_count_items",
        ["recount_assignee_id"],
    )

    op.create_table(
        "inventory_count_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "item_id", sa.Integer(), sa.ForeignKey("inventory_count_items.id"), nullable=False
        ),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("counted_qty", sa.Float(), nullable=False),
        sa.Column("odoo_qty", sa.Float(), nullable=False),
        sa.Column("odoo_qty_source", sa.String(length=12), nullable=False, server_default="live"),
        sa.Column("counted_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("item_id", "attempt", name="uq_item_attempt"),
    )
    op.create_index(
        "ix_inventory_count_entries_item_id", "inventory_count_entries", ["item_id"]
    )
    op.create_index(
        "ix_inventory_count_entries_counted_by_id", "inventory_count_entries", ["counted_by_id"]
    )

    op.create_table(
        "inventory_count_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "count_id", sa.Integer(), sa.ForeignKey("inventory_counts.id"), nullable=False
        ),
        sa.Column(
            "item_id", sa.Integer(), sa.ForeignKey("inventory_count_items.id"), nullable=True
        ),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_inventory_count_events_count_id", "inventory_count_events", ["count_id"])
    op.create_index("ix_inventory_count_events_item_id", "inventory_count_events", ["item_id"])


def downgrade() -> None:
    op.drop_table("inventory_count_events")
    op.drop_table("inventory_count_entries")
    op.drop_table("inventory_count_items")
    op.drop_table("inventory_counts")
