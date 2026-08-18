"""delivery form: declared pallets, request links, discrepancy reasons

The warehouse works in Odoo and declares what they sent on the app's
delivery form. A pallet_transfers row is now the DELIVERY: who declared it,
which requests rode it (pallet_requests), why quantities differ
(pallet_discrepancies), and its ONE floor-staging→floor count transfer.

Additive only — every new column carries a server_default so deployed rows
(there are live pallets on the hosted stack) stay valid.

Revision ID: e4a7c2b91d63
Revises: d3b7f21a5c40
Create Date: 2026-08-17 12:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e4a7c2b91d63"
down_revision = "d3b7f21a5c40"
branch_labels = None
depends_on = None

JSON_VARIANT = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()), "postgresql"
)


def upgrade() -> None:
    # ---- the delivery form's own columns on pallet_transfers
    with op.batch_alter_table("pallet_transfers") as batch:
        batch.add_column(
            sa.Column("note", sa.Text(), nullable=False, server_default="")
        )
        batch.add_column(sa.Column("declared_by_id", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("declared_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "count_status", sa.String(length=12), nullable=False, server_default="none"
            )
        )
        batch.add_column(
            sa.Column(
                "count_reference", sa.String(length=40), nullable=False, server_default=""
            )
        )
        batch.add_column(
            sa.Column("count_error", sa.Text(), nullable=False, server_default="")
        )
        batch.add_column(sa.Column("count_picking_id", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column(
                "count_picking_name", sa.String(length=80), nullable=False, server_default=""
            )
        )
        batch.add_column(
            sa.Column(
                "count_picking_url", sa.String(length=500), nullable=False, server_default=""
            )
        )
        batch.add_column(
            sa.Column(
                "count_barcode_url", sa.String(length=500), nullable=False, server_default=""
            )
        )
        batch.add_column(
            sa.Column("count_checked_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.create_foreign_key(
            op.f("fk_pallet_transfers_declared_by_id_users"),
            "users",
            ["declared_by_id"],
            ["id"],
        )

    # ---- which requests rode which delivery
    op.create_table(
        "pallet_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pallet_id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["pallet_id"],
            ["pallet_transfers.id"],
            name=op.f("fk_pallet_requests_pallet_id_pallet_transfers"),
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["transfer_requests.id"],
            name=op.f("fk_pallet_requests_request_id_transfer_requests"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pallet_requests")),
        sa.UniqueConstraint("pallet_id", "request_id", name="uq_pallet_request"),
    )
    op.create_index(
        op.f("ix_pallet_requests_pallet_id"), "pallet_requests", ["pallet_id"]
    )
    op.create_index(
        op.f("ix_pallet_requests_request_id"), "pallet_requests", ["request_id"]
    )

    # ---- why the quantities differ, per product, in the warehouse's words
    op.create_table(
        "pallet_discrepancies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pallet_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("qty_requested", sa.Float(), nullable=False),
        sa.Column("qty_sent", sa.Float(), nullable=False),
        sa.Column("reasons", JSON_VARIANT, nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["pallet_id"],
            ["pallet_transfers.id"],
            name=op.f("fk_pallet_discrepancies_pallet_id_pallet_transfers"),
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_pallet_discrepancies_product_id_products"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pallet_discrepancies")),
        sa.UniqueConstraint(
            "pallet_id", "product_id", name="uq_pallet_discrepancy_product"
        ),
    )
    op.create_index(
        op.f("ix_pallet_discrepancies_pallet_id"), "pallet_discrepancies", ["pallet_id"]
    )
    op.create_index(
        op.f("ix_pallet_discrepancies_product_id"),
        "pallet_discrepancies",
        ["product_id"],
    )

    # ---- a delivery count's adjustments hang off the delivery, not a request
    with op.batch_alter_table("adjustments") as batch:
        batch.add_column(sa.Column("pallet_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            op.f("fk_adjustments_pallet_id_pallet_transfers"),
            "pallet_transfers",
            ["pallet_id"],
            ["id"],
        )
    op.create_index(op.f("ix_adjustments_pallet_id"), "adjustments", ["pallet_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_adjustments_pallet_id"), table_name="adjustments")
    with op.batch_alter_table("adjustments") as batch:
        batch.drop_constraint(
            op.f("fk_adjustments_pallet_id_pallet_transfers"), type_="foreignkey"
        )
        batch.drop_column("pallet_id")

    op.drop_table("pallet_discrepancies")
    op.drop_table("pallet_requests")

    with op.batch_alter_table("pallet_transfers") as batch:
        batch.drop_constraint(
            op.f("fk_pallet_transfers_declared_by_id_users"), type_="foreignkey"
        )
        for column in (
            "count_checked_at",
            "count_barcode_url",
            "count_picking_url",
            "count_picking_name",
            "count_picking_id",
            "count_error",
            "count_reference",
            "count_status",
            "declared_at",
            "declared_by_id",
            "note",
        ):
            batch.drop_column(column)
