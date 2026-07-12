"""Demo seed: a fully working stack with ZERO real credentials.

    docker compose up && make seed

Loads: coordinator sheet (real file if present in docs/reference, otherwise a
bundled sample), demo login users for every role, the III Departments zone
with non-Odoo items, feature flags (all writes OFF), then runs every sync
domain against the fixture simulator (~1,200 products, 24 months of sales).

Idempotent — safe to re-run.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..centers.importer import run_import
from ..config import get_settings
from ..db import get_sessionmaker
from ..models import (
    Adjustment,
    Center,
    FeatureFlag,
    OrderList,
    OrderListLine,
    OrderListStatus,
    Product,
    ProductSource,
    Role,
    RoleAssignment,
    StockLevel,
    TransferEvent,
    TransferEventKind,
    TransferRequest,
    TransferRequestLine,
    TransferRequestStatus,
    User,
    Zone,
    ZoneKind,
    utcnow,
)
from ..restock.engine import fold_floor_restock
from ..sync.runner import run_all

DEMO_DOMAIN = "demo.ishalife.test"

DEPT_CENTERS = ["Kitchen", "Guest Services", "AV Team", "Front Office", "Snacks Counter"]

MANUAL_ITEMS = [
    ("MAN-WATER", "Spring Water — 24-Pack", "Department Supplies", 6.50),
    ("MAN-COOKIES", "Millet Cookies — Catering Tray", "Department Supplies", 12.00),
    ("MAN-CUPS", "Compostable Cups — Sleeve of 50", "Department Supplies", 4.25),
    ("MAN-TSHIRT-VOL", "Volunteer T-Shirt (untracked)", "Department Supplies", 8.00),
]


def get_or_create_user(db: Session, email: str, name: str) -> User:
    user = db.scalar(select(User).where(func.lower(User.email) == email.lower()))
    if user is None:
        user = User(email=email.lower(), display_name=name)
        db.add(user)
        db.flush()
    return user


def ensure_role(db: Session, user: User, role: Role, zone_id: int | None = None,
                center_id: int | None = None) -> None:
    exists = db.scalar(
        select(RoleAssignment).where(
            RoleAssignment.user_id == user.id,
            RoleAssignment.role == role.value,
            RoleAssignment.zone_id == zone_id,
            RoleAssignment.center_id == center_id,
        )
    )
    if exists is None:
        db.add(RoleAssignment(user_id=user.id, role=role.value, zone_id=zone_id, center_id=center_id))


def seed_departments(db: Session) -> Zone:
    zone = db.scalar(select(Zone).where(Zone.name == "III Departments"))
    if zone is None:
        zone = Zone(name="III Departments", kind=ZoneKind.DEPARTMENTS.value)
        db.add(zone)
        db.flush()
    for name in DEPT_CENTERS:
        if db.scalar(select(Center).where(Center.name == name)) is None:
            db.add(
                Center(
                    name=name, city="III Campus", state="Tennessee", region="Campus",
                    country="US", zone_id=zone.id, is_active=True, activity_raw="Yes",
                )
            )
    db.flush()
    return zone


def seed_manual_products(db: Session) -> int:
    created = 0
    for sku, name, category, price in MANUAL_ITEMS:
        if db.scalar(select(Product).where(Product.global_sku == sku)) is None:
            db.add(
                Product(
                    global_sku=sku, us_sku=sku, name=name, category=category,
                    retail_price=price, source=ProductSource.MANUAL.value,
                    is_stock_tracked=False, dept_orderable=True, is_active=True,
                )
            )
            created += 1
    return created


def seed_flags(db: Session) -> None:
    flags = [
        (
            "write_create_internal_transfer",
            "OdooWriter.create_internal_transfer may write live (draft transfers). "
            "Enable only after its canary passes.",
        ),
    ]
    for key, description in flags:
        if db.get(FeatureFlag, key) is None:
            db.add(FeatureFlag(key=key, enabled=False, description=description))


def _top_stocked_products(db: Session, n: int) -> list[Product]:
    rows = db.execute(
        select(Product, StockLevel.qty)
        .join(StockLevel, StockLevel.product_id == Product.id)
        .where(
            StockLevel.location_key == "bwhse",
            Product.is_stock_tracked.is_(True),
            Product.is_active.is_(True),
            Product.odoo_product_id.is_not(None),
        )
        .order_by(StockLevel.qty.desc())
        .limit(n)
    )
    return [p for p, _ in rows]


def seed_phase2_flows(db: Session) -> None:
    """Demo order lists, a transfer request mid-flow, and one reconciled
    request with an open adjustment — so every phase-2 screen has life in it.
    Idempotent: markers are checked before anything is created."""
    products = _top_stocked_products(db, 8)
    if len(products) < 4:
        print("  phase 2: not enough stocked products for demo flows — skipped")
        return
    floor = db.scalar(select(User).where(User.email == f"floor@{DEMO_DOMAIN}"))
    wh = db.scalar(select(User).where(User.email == f"warehouse@{DEMO_DOMAIN}"))
    admin = db.scalar(select(User).where(User.email == f"admin@{DEMO_DOMAIN}"))

    # --- order lists -> the Zone 1 coordinator's pending queue
    zone1 = db.scalar(select(Zone).where(Zone.name.ilike("Zone 1%")))
    if zone1 and db.scalar(select(OrderList)) is None:
        centers = db.scalars(
            select(Center).where(Center.zone_id == zone1.id, Center.is_active.is_(True))
        ).all()
        # prefer a center whose Odoo location mapped (live write possible)
        dest = next((c for c in centers if c.odoo_location_id), centers[0] if centers else None)
        if dest is not None:
            pending = OrderList(
                name=f"{dest.name} monthly staples",
                notes="Seeded demo list — approve it to see the (gated) Odoo write path.",
                status=OrderListStatus.PENDING_APPROVAL.value,
                zone_id=zone1.id,
                center_id=dest.id,
                created_by_id=admin.id if admin else None,
                assigned_at=utcnow(),
            )
            db.add(pending)
            db.flush()
            for pos, (p, qty) in enumerate(zip(products[:4], [12, 24, 6, 10], strict=False)):
                db.add(
                    OrderListLine(
                        order_list_id=pending.id, product_id=p.id, qty=qty, position=pos
                    )
                )
            draft = OrderList(
                name="Standard center starter kit",
                notes="Clone this for new pop-ups.",
                created_by_id=admin.id if admin else None,
            )
            db.add(draft)
            db.flush()
            for pos, p in enumerate(products[:6]):
                db.add(
                    OrderListLine(order_list_id=draft.id, product_id=p.id, qty=6, position=pos)
                )
            print(f"  phase 2: order lists seeded (pending → {dest.name}, plus a draft)")

    # --- transfer requests: one waiting on a staging count, one reconciled
    if floor and wh and db.scalar(select(TransferRequest)) is None:
        mid = TransferRequest(
            status=TransferRequestStatus.IN_STAGING.value,
            notes="Demo: morning cart from the warehouse",
            created_by_id=floor.id,
        )
        db.add(mid)
        db.flush()
        for p, req_qty, sent in zip(products[:3], [6, 4, 10], [6, 3, 10], strict=False):
            db.add(
                TransferRequestLine(
                    request_id=mid.id, product_id=p.id, qty_requested=req_qty, qty_sent=sent
                )
            )
        for status, actor, note in [
            (TransferRequestStatus.REQUESTED.value, floor.id, "3 item(s) requested"),
            (TransferRequestStatus.PICKED.value, wh.id, "picked 19 unit(s) — 1 line short"),
            (TransferRequestStatus.IN_STAGING.value, wh.id, "delivered to floor staging"),
        ]:
            db.add(
                TransferEvent(
                    request_id=mid.id, kind=TransferEventKind.STATUS.value,
                    status=status, actor_user_id=actor, note=note,
                )
            )

        done = TransferRequest(
            status=TransferRequestStatus.COUNTED.value,
            notes="Demo: yesterday's request — count found one bottle missing",
            created_by_id=floor.id,
        )
        db.add(done)
        db.flush()
        p = products[3]
        line = TransferRequestLine(
            request_id=done.id, product_id=p.id, qty_requested=10, qty_sent=9, qty_counted=8
        )
        db.add(line)
        db.flush()
        db.add(
            Adjustment(
                request_id=done.id, line_id=line.id, product_id=p.id,
                qty_expected=9, qty_counted=8, delta=-1,
                note=f"Staging count on request #{done.id}",
            )
        )
        for status, actor, note in [
            (TransferRequestStatus.REQUESTED.value, floor.id, "1 item requested"),
            (TransferRequestStatus.PICKED.value, wh.id, "picked 9 unit(s) — 1 line short"),
            (TransferRequestStatus.IN_STAGING.value, wh.id, "delivered to floor staging"),
            (TransferRequestStatus.COUNTED.value, floor.id, "staging count recorded"),
        ]:
            db.add(
                TransferEvent(
                    request_id=done.id, kind=TransferEventKind.STATUS.value,
                    status=status, actor_user_id=actor, note=note,
                )
            )
        db.add(
            TransferEvent(
                request_id=done.id, kind=TransferEventKind.DISCREPANCY.value,
                actor_user_id=floor.id,
                note=f"1 discrepancy(ies) → adjustments queue: line {line.id}: sent 9, counted 8 (-1)",
            )
        )
        print("  phase 2: transfer requests seeded (one in staging, one with an open adjustment)")

    db.commit()


def replay_restock_folds(db: Session, days: int = 10) -> None:
    """Run the accumulator day by day over the recent sales history, exactly
    as if the old restock script had run every morning."""
    settings = get_settings()
    today = utcnow().date()
    flagged = 0
    for offset in range(days, -1, -1):
        flagged += fold_floor_restock(db, settings, today - timedelta(days=offset))
    print(f"  restock: folds replayed over {days} days — {flagged} item(s) flagged")


def main() -> None:
    settings = get_settings()
    db = get_sessionmaker()()
    try:
        print("Seeding Isha Life Shop Ops demo…\n")

        # 1. coordinator roster
        xlsx = settings.coordinator_xlsx_path
        if xlsx.exists():
            report = run_import(db, xlsx, apply=True, create_users=True)
            print(
                f"  roster: {report.centers_created} centers created, "
                f"{report.centers_updated} updated, {len(report.followups)} flagged for follow-up, "
                f"{report.users_created} orderer users, zones: {len(report.zones_created)} new"
            )
        else:
            print(f"  roster: workbook not found at {xlsx} — skipping (admin can import later)")

        # 2. departments zone + manual items
        dept_zone = seed_departments(db)
        created = seed_manual_products(db)
        print(f"  departments: zone + {len(DEPT_CENTERS)} dept centers, {created} manual items")

        # 3. feature flags
        seed_flags(db)

        # 4. demo users, one per role
        admin = get_or_create_user(db, f"admin@{DEMO_DOMAIN}", "Demo Admin")
        ensure_role(db, admin, Role.ADMIN)
        wh = get_or_create_user(db, f"warehouse@{DEMO_DOMAIN}", "Demo Warehouse")
        ensure_role(db, wh, Role.WAREHOUSE)
        floor = get_or_create_user(db, f"floor@{DEMO_DOMAIN}", "Demo Shoppe Floor")
        ensure_role(db, floor, Role.SHOPPE_FLOOR)

        zone1 = db.scalar(select(Zone).where(Zone.name.ilike("Zone 1%")))
        coord = get_or_create_user(db, f"coordinator@{DEMO_DOMAIN}", "Demo Coordinator (Lili)")
        if zone1:
            ensure_role(db, coord, Role.ZONE_COORDINATOR, zone_id=zone1.id)

        austin = db.scalar(select(Center).where(Center.name.ilike("Austin%")))
        orderer = get_or_create_user(db, f"orderer@{DEMO_DOMAIN}", "Demo Orderer (Austin)")
        if austin:
            ensure_role(db, orderer, Role.CENTER_ORDERER, center_id=austin.id)

        liaison = get_or_create_user(db, f"liaison@{DEMO_DOMAIN}", "Demo Dept Liaison")
        ensure_role(db, liaison, Role.DEPT_LIAISON, zone_id=dept_zone.id)
        kitchen = db.scalar(select(Center).where(Center.name == "Kitchen"))
        dept_orderer = get_or_create_user(db, f"kitchen@{DEMO_DOMAIN}", "Demo Kitchen Orderer")
        if kitchen:
            ensure_role(db, dept_orderer, Role.DEPT_ORDERER, center_id=kitchen.id)

        db.commit()
        print("  users: 7 demo logins (admin, warehouse, floor, coordinator, orderer, liaison, kitchen)")

        # 5. sync everything (fixture simulator unless real ODOO_* creds are set)
        print(f"\n  syncing all domains ({settings.odoo_mode} mode)…")
        for run in run_all(db, settings, trigger="seed"):
            mark = "ok" if run.status == "success" else "FAILED"
            print(f"    [{mark}] {run.domain:<9} {run.rows} rows {run.error}")

        # 6. phase-2 flows: restock accumulator history + demo lists/requests
        replay_restock_folds(db)
        seed_phase2_flows(db)

        n_products = db.scalar(select(func.count()).select_from(Product))
        print(f"\nDone. {n_products} products in the catalog.")
        print("\nLog in at the web UI with any of these (dev mode shows the code on screen):")
        for label, email in [
            ("Admin", f"admin@{DEMO_DOMAIN}"),
            ("Warehouse", f"warehouse@{DEMO_DOMAIN}"),
            ("Shoppe floor", f"floor@{DEMO_DOMAIN}"),
            ("Zone coordinator", f"coordinator@{DEMO_DOMAIN}"),
            ("Center orderer", f"orderer@{DEMO_DOMAIN}"),
            ("Dept liaison", f"liaison@{DEMO_DOMAIN}"),
            ("Dept orderer", f"kitchen@{DEMO_DOMAIN}"),
        ]:
            print(f"  {label:<17} {email}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
