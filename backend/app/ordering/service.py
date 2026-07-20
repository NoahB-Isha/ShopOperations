"""Ordering service — where inputs, the pure engine, and persistence meet.

Order lifecycle:
  * GENERATE (draft): run the engine over the snapshot (or an upload), keep
    EVERY candidate as a line — the review table is the draft order. The full
    Suggestion is frozen into `suggestion_json` per line and the rules into
    `rules_json`; DECISIONS.md's "orders pin the snapshot they were computed
    from" lands here.
  * REVIEW: per-line overrides move `final_*` (each one logged as a
    qty_change event with actor).
  * PLACE: exports (CSV+XLSX, stored as attachments forever), initial
    sea/air legs, the order email through the gate ladder, status → placed.
  * TRACK: replies → proposals → confirmed events (timeline.py).

Category rules are code defaults merged with the admin-editable
`ordering_rules` AppSetting (overridable without code changes).
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import Settings
from ..models import (
    AnalogyStatus,
    AppSetting,
    AttachmentSource,
    ForecastAnalogy,
    LegMethod,
    LegStatus,
    OrderAttachment,
    OrderDestination,
    OrderEventKind,
    OrderLeg,
    Product,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderStatus,
    PurchaseOrderType,
    User,
    Vendor,
    VendorKind,
    utcnow,
)
from ..odoo.operations import new_reference
from .emailer import dispatch_order_email
from .engine import Suggestion, suggest_all
from .export import export_rows, rows_to_csv, rows_to_xlsx
from .inputs import (
    SnapshotBundle,
    build_bundle_from_sales_csv,
    build_bundle_from_workbook,
    build_snapshot_bundle,
    snapshots_for_products,
)
from .rules import OrderingRules
from .timeline import add_event

log = logging.getLogger("ordering.service")

RULES_SETTING_KEY = "ordering_rules"
INDIA_LIST_SETTING_KEY = "india_product_list"


class OrderingError(ValueError):
    """User-facing ordering failure (409/400 at the router)."""


# ----------------------------------------------------------------- settings
def load_rules(db: Session) -> OrderingRules:
    setting = db.get(AppSetting, RULES_SETTING_KEY)
    overrides = setting.value if setting and isinstance(setting.value, dict) else None
    return OrderingRules().merged(overrides)


def get_app_setting(db: Session, key: str) -> dict:
    setting = db.get(AppSetting, key)
    return dict(setting.value) if setting and isinstance(setting.value, dict) else {}


def set_app_setting(db: Session, key: str, value: dict, actor: User | None = None) -> None:
    setting = db.get(AppSetting, key)
    if setting is None:
        setting = AppSetting(key=key)
        db.add(setting)
    setting.value = value
    setting.updated_by_id = actor.id if actor else None
    setting.updated_at = utcnow()


# ------------------------------------------------------- India product list
def set_india_product_list(
    db: Session, *, filename: str, data: bytes, actor: User | None
) -> dict:
    """Store the buyer's current India product list (any spreadsheet with
    names/SKUs/barcodes). It becomes the authoritative scope for India order
    generation until replaced. The original file is kept for download."""
    import base64

    from ..catalog.matching import SpreadsheetError, match_products, parse_table

    try:
        rows = parse_table(data, filename)
    except SpreadsheetError as e:
        raise OrderingError(str(e)) from e
    if not rows:
        raise OrderingError("no rows found in the file")
    report = match_products(db, rows)
    if not report.hits:
        raise OrderingError(
            f"none of the {report.total_rows} row(s) matched a product — "
            "check the file has names, SKUs or barcodes"
        )
    value = {
        "filename": filename or "product-list.csv",
        "uploaded_at": utcnow().isoformat(),
        "content_b64": base64.b64encode(data).decode(),
        "skus": sorted({h.product.global_sku for h in report.hits}),
        "matched": len(report.hits),
        "total_rows": report.total_rows,
        "unmatched_rows": [preview for _, preview in report.unmatched][:100],
    }
    set_app_setting(db, INDIA_LIST_SETTING_KEY, value, actor)
    return {k: v for k, v in value.items() if k != "content_b64"}


def india_product_list_meta(db: Session) -> dict | None:
    value = get_app_setting(db, INDIA_LIST_SETTING_KEY)
    if not value.get("skus"):
        return None
    return {k: v for k, v in value.items() if k != "content_b64"}


def india_product_list_file(db: Session) -> tuple[str, bytes] | None:
    import base64

    value = get_app_setting(db, INDIA_LIST_SETTING_KEY)
    if not value.get("content_b64"):
        return None
    return str(value.get("filename") or "product-list.csv"), base64.b64decode(
        value["content_b64"]
    )


def clear_india_product_list(db: Session, actor: User | None) -> None:
    set_app_setting(db, INDIA_LIST_SETTING_KEY, {}, actor)


# ----------------------------------------------------------------- creation
def _bundle_for_source(
    db: Session,
    rules: OrderingRules,
    source: str,
    upload: bytes | None,
    upload_name: str = "",
) -> SnapshotBundle:
    if source == "odoo":
        meta = india_product_list_meta(db)
        restrict = set(meta["skus"]) if meta else None
        bundle = build_snapshot_bundle(db, rules, restrict_skus=restrict)
        if meta:
            bundle.warnings.append(
                f"scoped to {meta['filename']} ({meta['matched']} matched products)"
            )
        return bundle
    if upload is None:
        raise OrderingError("an upload is required for this snapshot source")
    if source == "workbook" or upload_name.lower().endswith((".xlsx", ".xlsm")):
        return build_bundle_from_workbook(db, rules, upload)
    return build_bundle_from_sales_csv(db, rules, upload)


def create_import_order(
    db: Session,
    settings: Settings,
    *,
    name: str,
    destination: str = OrderDestination.III.value,
    created_by: User | None = None,
    source: str = "odoo",
    upload: bytes | None = None,
    upload_name: str = "",
    notes: str = "",
) -> PurchaseOrder:
    """Generate the draft India order — the review table's backing rows."""
    if destination not in (OrderDestination.III.value, OrderDestination.CAN.value):
        raise OrderingError("destination must be III or CAN")
    rules = load_rules(db)
    bundle = _bundle_for_source(db, rules, source, upload, upload_name)
    if not bundle.snapshots:
        raise OrderingError(
            "no order candidates found — " + "; ".join(bundle.warnings or ["snapshot is empty"])
        )
    suggestions = suggest_all(bundle.snapshots, rules)
    graduated = graduate_analogies(db, bundle.graduable_analogy_ids)

    order = PurchaseOrder(
        name=name.strip() or f"Import {utcnow().date().isoformat()}",
        reference=new_reference("PO"),
        order_type=PurchaseOrderType.IMPORT.value,
        destination=destination,
        rules_json=asdict(rules),
        snapshot_at=bundle.snapshot_at,
        snapshot_source=bundle.source,
        created_by_id=created_by.id if created_by else None,
        notes=notes,
    )
    db.add(order)
    db.flush()
    products_by_sku = _products_by_sku(db, [s.global_sku for s in suggestions])
    for s in suggestions:
        db.add(_line_from_suggestion(order, s, products_by_sku.get(s.global_sku)))
    db.flush()

    n_ordering = sum(1 for s in suggestions if s.suggested_sea_round or s.suggested_air_round)
    note = (
        f"Draft generated from {bundle.source} snapshot: {len(suggestions)} candidates, "
        f"{n_ordering} with suggested quantities."
    )
    if graduated:
        note += f" {graduated} analogy forecast(s) graduated to real history."
    for warning in bundle.warnings:
        note += f"\n⚠ {warning}"
    add_event(
        db, order, OrderEventKind.STATUS, status="created", note=note, actor=created_by
    )
    if destination == OrderDestination.CAN.value:
        add_event(
            db,
            order,
            OrderEventKind.NOTE,
            note=(
                "Canada destination: the USA→CAN flow (sale order + transfer + customs "
                "paperwork) is modelled but not built out yet — handle those steps manually."
            ),
        )
    return order


def _line_from_suggestion(
    order: PurchaseOrder, s: Suggestion, product: Product | None
) -> PurchaseOrderLine:
    return PurchaseOrderLine(
        order_id=order.id,
        product_id=product.id if product else None,
        global_sku=s.global_sku,
        suggested_sea_qty=s.suggested_sea_round,
        suggested_air_qty=s.suggested_air_round,
        baseline_sea_qty=s.baseline_sea_round,
        baseline_air_qty=s.baseline_air_round,
        origin_sea_qty=s.suggested_sea_round,
        origin_air_qty=s.suggested_air_round,
        final_sea_qty=s.suggested_sea_round,
        final_air_qty=s.suggested_air_round,
        target_moh_used=s.target_moh,
        case_size=s.case_size,
        suggestion_json=asdict(s),
    )


def _products_by_sku(db: Session, skus: list[str]) -> dict[str, Product]:
    if not skus:
        return {}
    rows = db.execute(select(Product).where(Product.global_sku.in_(skus))).scalars()
    return {p.global_sku: p for p in rows}


def graduate_analogies(db: Session, analogy_ids: list[int]) -> int:
    """Retire analogies whose products now have enough real history."""
    n = 0
    for aid in analogy_ids:
        analogy = db.get(ForecastAnalogy, aid)
        if analogy and analogy.status == AnalogyStatus.ACTIVE.value:
            analogy.status = AnalogyStatus.GRADUATED.value
            analogy.graduated_at = utcnow()
            n += 1
    return n


# ---------------------------------------------------------------- domestic
def domestic_suggestions(db: Session, vendor: Vendor) -> list[Suggestion]:
    """MOQ-rule reorder suggestions for one vendor's products (DOMESTIC sheet:
    order one MOQ when months-on-hand drops below the trigger)."""
    rules = load_rules(db)
    products = (
        db.execute(
            select(Product)
            .options(selectinload(Product.tags))
            .where(
                Product.vendor_id == vendor.id,
                Product.is_active.is_(True),
                Product.ordering_exclude.is_(False),
            )
            .order_by(Product.name)
        )
        .scalars()
        .all()
    )
    snaps = snapshots_for_products(db, rules, products, {vendor.id: vendor})
    return suggest_all(snaps, rules)


def create_domestic_order(
    db: Session,
    *,
    vendor: Vendor,
    quantities: dict[str, int],
    name: str = "",
    destination: str = OrderDestination.III.value,
    created_by: User | None = None,
) -> PurchaseOrder:
    """A per-vendor domestic PO from chosen quantities ({sku: qty}); reuses
    the exact order/timeline machinery with a one-leg 'sea' column as the
    plain quantity (no sea/air for domestic)."""
    quantities = {k: int(q) for k, q in (quantities or {}).items() if int(q or 0) > 0}
    if not quantities:
        raise OrderingError("no quantities to order")
    if vendor.kind == VendorKind.INDIA.value:
        raise OrderingError("domestic orders are for non-India vendors")
    suggestions = {s.global_sku: s for s in domestic_suggestions(db, vendor)}
    order = PurchaseOrder(
        name=name.strip() or f"{vendor.name} {utcnow().date().isoformat()}",
        reference=new_reference("PO"),
        order_type=PurchaseOrderType.DOMESTIC.value,
        destination=destination,
        vendor_id=vendor.id,
        rules_json={"vendor": vendor.name},
        snapshot_at=utcnow(),
        created_by_id=created_by.id if created_by else None,
    )
    db.add(order)
    db.flush()
    products_by_sku = _products_by_sku(db, list(quantities))
    for sku, qty in quantities.items():
        product = products_by_sku.get(sku)
        if product is None or product.vendor_id != vendor.id:
            raise OrderingError(f"{sku} is not one of {vendor.name}'s products")
        s = suggestions.get(sku)
        suggestion_json = asdict(s) if s else {"name": product.name, "us_sku": product.us_sku,
                                               "category": product.category}
        db.add(
            PurchaseOrderLine(
                order_id=order.id,
                product_id=product.id,
                global_sku=sku,
                suggested_sea_qty=s.suggested_sea_round if s else 0,
                origin_sea_qty=qty,
                final_sea_qty=qty,
                target_moh_used=s.target_moh if s else 0.0,
                case_size=s.case_size if s else (product.case_size or 1),
                suggestion_json=suggestion_json,
            )
        )
    db.flush()
    add_event(
        db,
        order,
        OrderEventKind.STATUS,
        status="created",
        note=f"Domestic order for {vendor.name}: {len(quantities)} item(s).",
        actor=created_by,
    )
    return order


# ---------------------------------------------------------------- overrides
def override_line(
    db: Session,
    order: PurchaseOrder,
    line: PurchaseOrderLine,
    *,
    sea: int | None,
    air: int | None,
    actor: User | None,
) -> list[str]:
    """Buyer override on a DRAFT order. Every change is a timeline event."""
    if order.status != PurchaseOrderStatus.DRAFT.value:
        raise OrderingError("only draft orders take direct overrides — use timeline events")
    changes: list[str] = []
    payload: dict = {}
    for value, attr, key in ((sea, "final_sea_qty", "sea"), (air, "final_air_qty", "air")):
        if value is None:
            continue
        value = int(value)
        if value < 0:
            raise OrderingError("quantities can't be negative")
        old = getattr(line, attr)
        if value != old:
            payload[key] = {"from": old, "to": value}
            setattr(line, attr, value)
            changes.append(f"{key} {old} → {value}")
    if changes:
        add_event(
            db,
            order,
            OrderEventKind.QTY_CHANGE,
            line=line,
            note="; ".join(changes),
            payload=payload,
            actor=actor,
        )
    return changes


# ------------------------------------------------------------------- place
def place_order(
    db: Session,
    settings: Settings,
    order: PurchaseOrder,
    actor: User | None = None,
) -> PurchaseOrder:
    """The big button: freeze final quantities into exports (stored forever),
    create the initial legs, dispatch the order email through the gate
    ladder, and mark the order placed."""
    if order.status != PurchaseOrderStatus.DRAFT.value:
        raise OrderingError(f"order is {order.status}; only draft orders can be placed")
    rows = export_rows(order)
    if not rows:
        raise OrderingError("nothing to order — every line has zero quantity")

    csv_text = rows_to_csv(rows)
    xlsx_bytes = rows_to_xlsx(rows, order.display_name)
    base = f"{order.display_name} ORDER LIST"
    attachments = [
        (f"{base}.csv", csv_text.encode("utf-8"), "text/csv"),
        (
            f"{base}.xlsx",
            xlsx_bytes,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
    ]
    for filename, data, content_type in attachments:
        db.add(
            OrderAttachment(
                order_id=order.id,
                source=AttachmentSource.EXPORT.value,
                filename=filename,
                content_type=content_type,
                size_bytes=len(data),
                data=data,
                note="generated at placement",
                uploaded_by_id=actor.id if actor else None,
            )
        )

    _create_initial_legs(db, order, rows)

    message = dispatch_order_email(db, settings, order, attachments)
    add_event(
        db,
        order,
        OrderEventKind.EMAIL,
        status=message.status,
        note=(
            f"Order email to {message.recipients or '(nobody)'} — {message.status}."
            + (" Rendered without sending (dry-run)." if message.status == "simulated" else "")
        ),
        source_message=message,
        actor=actor,
    )

    order.status = PurchaseOrderStatus.PLACED.value
    order.placed_at = utcnow()
    order.placed_by_id = actor.id if actor else None
    n_sea = sum(r["final_sea_qty"] for r in rows)
    n_air = sum(r["final_air_qty"] for r in rows)
    add_event(
        db,
        order,
        OrderEventKind.STATUS,
        status="placed",
        note=f"Placed: {len(rows)} lines, {n_sea} sea units, {n_air} air units. "
        f"CSV + XLSX exports attached to this order.",
        actor=actor,
    )
    return order


def _create_initial_legs(db: Session, order: PurchaseOrder, rows: list[dict]) -> None:
    today = utcnow().date()
    rules = order.rules_json or {}
    sea_lead = int(rules.get("sea_lead_months") or 6)
    air_lead = int(rules.get("air_lead_months") or 4)
    sea_lines = {r["global_sku"]: r["final_sea_qty"] for r in rows if r["final_sea_qty"] > 0}
    air_lines = {r["global_sku"]: r["final_air_qty"] for r in rows if r["final_air_qty"] > 0}
    if sea_lines:
        db.add(
            OrderLeg(
                order_id=order.id,
                label=order.display_name,
                method=LegMethod.SEA.value,
                status=LegStatus.PLANNED.value,
                eta=today + timedelta(days=round(sea_lead * 30.44)),
                line_quantities=sea_lines,
            )
        )
    if air_lines:
        db.add(
            OrderLeg(
                order_id=order.id,
                label=f"{order.display_name} AIR",
                method=LegMethod.AIR.value,
                status=LegStatus.PLANNED.value,
                eta=today + timedelta(days=round(air_lead * 30.44)),
                line_quantities=air_lines,
            )
        )


def cancel_order(
    db: Session, order: PurchaseOrder, actor: User | None, note: str = ""
) -> PurchaseOrder:
    if order.status not in (PurchaseOrderStatus.DRAFT.value, PurchaseOrderStatus.PLACED.value):
        raise OrderingError(f"order is already {order.status}")
    order.status = PurchaseOrderStatus.CANCELLED.value
    add_event(
        db, order, OrderEventKind.STATUS, status="cancelled", note=note, actor=actor
    )
    return order


def close_order(
    db: Session, order: PurchaseOrder, actor: User | None, note: str = ""
) -> PurchaseOrder:
    if order.status != PurchaseOrderStatus.PLACED.value:
        raise OrderingError("only placed orders can be closed")
    order.status = PurchaseOrderStatus.CLOSED.value
    add_event(
        db,
        order,
        OrderEventKind.STATUS,
        status="closed",
        note=note or "Everything arrived / reconciled.",
        actor=actor,
    )
    return order
