"""Pure payload builders for every named write operation.

No I/O here: given resolved ids, these functions render the exact `call_kw`
payload the writer will send. Unit tests assert on these shapes (test layer A
from the integration policy).
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass

# Every record the app creates carries an app prefix in its reference so
# app-created records are identifiable regardless of which Odoo account made
# them (the account is shared with a human). Canary records use APP-TEST-.
APP_REF_PREFIX = "ILAPP-"
CANARY_REF_PREFIX = "APP-TEST-"


def new_reference(kind: str = "XFER") -> str:
    token = secrets.token_hex(5).upper()
    return f"{APP_REF_PREFIX}{kind}-{token}"


def is_app_reference(ref: str | None) -> bool:
    r = (ref or "").strip()
    return r.startswith(APP_REF_PREFIX) or r.startswith(CANARY_REF_PREFIX)


@dataclass(frozen=True)
class TransferLine:
    product_odoo_id: int
    description: str
    qty: float


def build_internal_transfer_payload(
    *,
    picking_type_id: int | None,
    source_location_id: int,
    dest_location_id: int,
    reference: str,
    lines: list[TransferLine],
    move_field: str = "move_ids",
    note: str = "",
) -> dict:
    """`stock.picking` create-vals for a DRAFT internal transfer. The app never
    confirms or validates it — a human does that in Odoo."""
    vals: dict = {
        "picking_type_id": picking_type_id,
        "location_id": source_location_id,
        "location_dest_id": dest_location_id,
        "origin": reference,
        move_field: [
            (
                0,
                0,
                {
                    # Odoo 19 removed stock.move.name; the move description
                    # field is now description_picking (verified live 2026-07-12).
                    "description_picking": line.description,
                    "product_id": line.product_odoo_id,
                    "product_uom_qty": line.qty,
                    "location_id": source_location_id,
                    "location_dest_id": dest_location_id,
                },
            )
            for line in lines
        ],
    }
    if note:
        vals["note"] = note
    return vals
