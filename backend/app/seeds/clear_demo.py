"""Clear demo/test data ahead of a real test deployment.

    docker compose exec backend python -m app.seeds.clear_demo           # dry-run
    docker compose exec backend python -m app.seeds.clear_demo --apply   # do it

Removes the DEMO FLOW records testing created — transfer requests, center
orders, purchase orders (with their lines/events/attachments/emails/
proposals), notifications, restock checklist state, OOS marks, forecast
analogies, the seed catalogs ("Center starter kit", "Festival specials"),
E2E leftover catalogs, and the seed vendor ("Botanie Soap Co." — its
products are un-assigned, not deleted).

Deliberately KEEPS everything real or structural:
  - users (demo logins stay — they're how testers sign in under dev auth),
    zones, centers, contacts
  - products and everything synced from Odoo (stock, sales, incoming,
    snapshot history — this stack's history is live/reconstructed, not seed)
  - the Odoo write audit log (the paper trail is never cleared)
  - feature flags and app settings (india_product_list, ordering_email);
    only the reports narrative cache is dropped

Note: app records deleted here do NOT touch Odoo. Draft pickings the tests
rendered in Odoo stay there for humans to delete — the audit log keeps the
record of every one.
"""
from __future__ import annotations

import sys

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from ..db import get_sessionmaker
from ..models import (
    Adjustment,
    AppSetting,
    CenterOrder,
    CenterOrderEvent,
    CenterOrderLine,
    FloorOosMark,
    ForecastAnalogy,
    Notification,
    OrderAttachment,
    OrderEmailMessage,
    OrderEventProposal,
    OrderLeg,
    OrderList,
    OrderListCenter,
    OrderListLine,
    OrderListZone,
    Product,
    PurchaseOrder,
    PurchaseOrderEvent,
    PurchaseOrderLine,
    RestockAccum,
    RestockCheckoff,
    RestockFoldState,
    RestockLine,
    TransferEvent,
    TransferRequest,
    TransferRequestLine,
    Vendor,
)

SEED_CATALOGS = ("Center starter kit", "Festival specials")
E2E_CATALOG_PREFIX = "E2E catalog %"
SEED_VENDOR = "Botanie Soap Co."
NARRATIVE_CACHE_KEY = "reports_narrative_cache"


def _count(db: Session, model) -> int:
    return db.scalar(select(func.count()).select_from(model)) or 0


def clear_demo_data(db: Session, apply: bool) -> list[tuple[str, int]]:
    """Returns (label, rows) for everything that was (or would be) removed.
    Children first — plain deletes, no ORM cascade reliance."""
    report: list[tuple[str, int]] = []

    def wipe(label: str, model, where=None) -> None:
        stmt = select(func.count()).select_from(model)
        if where is not None:
            stmt = stmt.where(where)
        n = db.scalar(stmt) or 0
        report.append((label, n))
        if apply and n:
            dstmt = delete(model)
            if where is not None:
                dstmt = dstmt.where(where)
            db.execute(dstmt)

    # --- notifications reference center orders: they go first
    wipe("notifications (outbox)", Notification)

    # --- BWHSE→Floor transfer flow
    wipe("transfer adjustments", Adjustment)
    wipe("transfer events", TransferEvent)
    wipe("transfer request lines", TransferRequestLine)
    wipe("transfer requests", TransferRequest)

    # --- center orders
    wipe("center order events", CenterOrderEvent)
    wipe("center order lines", CenterOrderLine)
    wipe("center orders", CenterOrder)

    # --- purchasing (India + domestic test orders); events reference email
    # messages (source_message_id), so events go before messages
    wipe("order reply proposals", OrderEventProposal)
    wipe("purchase order events", PurchaseOrderEvent)
    wipe("order email messages", OrderEmailMessage)
    wipe("order attachments", OrderAttachment)
    wipe("purchase order legs", OrderLeg)
    wipe("purchase order lines", PurchaseOrderLine)
    wipe("purchase orders", PurchaseOrder)
    wipe("forecast analogies", ForecastAnalogy)

    # --- floor state: OOS marks + the restock checklist/accumulator
    wipe("floor OOS marks", FloorOosMark)
    wipe("restock checklist lines", RestockLine)
    wipe("restock accumulators", RestockAccum)
    wipe("restock check-offs", RestockCheckoff)
    wipe("restock fold state", RestockFoldState)

    # --- seed + e2e catalogs (real catalogs stay)
    doomed_lists = [
        lid
        for (lid,) in db.execute(
            select(OrderList.id).where(
                OrderList.name.in_(SEED_CATALOGS)
                | OrderList.name.like(E2E_CATALOG_PREFIX)
            )
        )
    ]
    for label, model in (
        ("catalog lines (seed/e2e)", OrderListLine),
        ("catalog zone grants (seed/e2e)", OrderListZone),
        ("catalog center grants (seed/e2e)", OrderListCenter),
    ):
        wipe(label, model, model.order_list_id.in_(doomed_lists or [-1]))
    wipe("catalogs (seed/e2e)", OrderList, OrderList.id.in_(doomed_lists or [-1]))

    # --- the seed vendor: un-assign its products, drop the vendor row
    vendor = db.scalar(select(Vendor).where(Vendor.name == SEED_VENDOR))
    if vendor is not None:
        n = db.scalar(
            select(func.count()).select_from(Product).where(Product.vendor_id == vendor.id)
        ) or 0
        report.append((f"products un-assigned from “{SEED_VENDOR}”", n))
        report.append(("seed vendor", 1))
        if apply:
            db.execute(
                update(Product)
                .where(Product.vendor_id == vendor.id)
                .values(vendor_id=None, moq=None)
            )
            db.delete(vendor)
    else:
        report.append((f"seed vendor “{SEED_VENDOR}”", 0))

    # --- stale narrative cache (references the pre-clear data)
    cache = db.get(AppSetting, NARRATIVE_CACHE_KEY)
    report.append(("reports narrative cache", 1 if cache else 0))
    if apply and cache is not None:
        db.delete(cache)

    if apply:
        db.commit()
    return report


def main() -> None:
    apply = "--apply" in sys.argv
    db = get_sessionmaker()()
    try:
        mode = "CLEARING" if apply else "DRY-RUN (nothing deleted — add --apply)"
        print(f"Demo-data clear — {mode}\n")
        report = clear_demo_data(db, apply)
        width = max(len(label) for label, _ in report)
        total = 0
        for label, n in report:
            marker = "-" if n == 0 else ("x" if apply else "~")
            print(f"  [{marker}] {label:<{width}}  {n}")
            total += n
        print(f"\n  {'removed' if apply else 'would remove'}: {total} row(s)")
        print(
            "  kept: users, zones/centers, products, stock/sales/snapshot history,\n"
            "        feature flags, ordering settings, the Odoo write audit log"
        )
        if apply:
            print(
                "\n  Reminder: draft pickings created in Odoo during testing are NOT\n"
                "  touched — clean those up in Odoo (the audit log lists every one)."
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
