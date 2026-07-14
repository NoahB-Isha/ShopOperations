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

from ..center_orders.reasonability import assess_order
from ..centers.importer import run_import
from ..config import get_settings
from ..db import get_sessionmaker
from ..models import (
    Adjustment,
    Center,
    CenterOrder,
    CenterOrderEvent,
    CenterOrderEventKind,
    CenterOrderLine,
    CenterOrderStatus,
    FeatureFlag,
    IncomingMove,
    NotificationKind,
    OdooWriteOutcome,
    OrderList,
    OrderListCenter,
    OrderListLine,
    OrderListZone,
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
    not_clothing,
    utcnow,
)
from ..notify import service as notify_service
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
        (
            "write_prepare_count_transfer",
            "OdooWriter.prepare_count_transfer may write live (duplicate the placement "
            "picking as the STAGING→FLOOR count, mark To Do, check availability). "
            "Enable only after create_internal_transfer is proven.",
        ),
        (
            "notify_whatsapp_live",
            "Order notifications may actually send over the WhatsApp bridge. "
            "Off = sends are recorded as simulated.",
        ),
        (
            "notify_email_live",
            "Order notifications may actually send email (the WhatsApp fallback). "
            "Off = sends are recorded as simulated.",
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
            not_clothing(),  # clothing is out of scope for ordering flows
        )
        .order_by(StockLevel.qty.desc())
        .limit(n)
    )
    return [p for p, _ in rows]


def seed_phase2_flows(db: Session) -> None:
    """Demo catalogs (order lists) with zone/center grants, plus transfer
    requests across the new flow states — so every phase-2 screen has life.
    Idempotent: skips whenever any row already exists."""
    products = _top_stocked_products(db, 10)
    if len(products) < 6:
        print("  phase 2: not enough stocked products for demo flows — skipped")
        return
    floor = db.scalar(select(User).where(User.email == f"floor@{DEMO_DOMAIN}"))
    wh = db.scalar(select(User).where(User.email == f"warehouse@{DEMO_DOMAIN}"))
    admin = db.scalar(select(User).where(User.email == f"admin@{DEMO_DOMAIN}"))

    # --- order lists = orderable catalogs, granted to zones then centers
    zone1 = db.scalar(select(Zone).where(Zone.name.ilike("Zone 1%")))
    zone2 = db.scalar(select(Zone).where(Zone.name.ilike("Zone 2%")))
    if zone1 and db.scalar(select(OrderList)) is None:
        starter = OrderList(
            name="Center starter kit",
            notes="The safe default catalog for any pop-up shop.",
            created_by_id=admin.id if admin else None,
        )
        specials = OrderList(
            name="Festival specials",
            notes="Seasonal additions — grant per center as needed.",
            created_by_id=admin.id if admin else None,
        )
        db.add_all([starter, specials])
        db.flush()
        for pos, p in enumerate(products[:8]):
            db.add(OrderListLine(order_list_id=starter.id, product_id=p.id, position=pos))
        for pos, p in enumerate(products[6:10]):
            db.add(OrderListLine(order_list_id=specials.id, product_id=p.id, position=pos))
        for zone in (zone1, zone2):
            if zone is not None:
                db.add(
                    OrderListZone(
                        order_list_id=starter.id, zone_id=zone.id,
                        granted_by_id=admin.id if admin else None,
                    )
                )
        db.add(
            OrderListZone(
                order_list_id=specials.id, zone_id=zone1.id,
                granted_by_id=admin.id if admin else None,
            )
        )
        # the Zone 1 coordinator has already opened the starter kit to two centers
        z1_centers = db.scalars(
            select(Center)
            .where(Center.zone_id == zone1.id, Center.is_active.is_(True))
            .order_by(Center.name)
            .limit(2)
        ).all()
        coord = db.scalar(select(User).where(User.email == f"coordinator@{DEMO_DOMAIN}"))
        for c in z1_centers:
            db.add(
                OrderListCenter(
                    order_list_id=starter.id, center_id=c.id,
                    granted_by_id=coord.id if coord else None,
                )
            )
        print(
            f"  phase 2: catalogs seeded (starter kit → zones 1+2, "
            f"{len(z1_centers)} center grant(s))"
        )

    # --- transfer requests across the flow: requested / working / done
    if floor and wh and db.scalar(select(TransferRequest)) is None:
        def mk_request(status: str, note: str, items: list, sent=None, counted=None):
            req = TransferRequest(
                status=status,
                notes=note,
                created_by_id=floor.id,
                picking_status=OdooWriteOutcome.SIMULATED.value,
                picking_reference="",
            )
            db.add(req)
            db.flush()
            lines = []
            for i, (p, qty) in enumerate(items):
                line = TransferRequestLine(
                    request_id=req.id,
                    product_id=p.id,
                    qty_requested=qty,
                    qty_sent=sent[i] if sent else None,
                    qty_counted=counted[i] if counted else None,
                )
                db.add(line)
                lines.append(line)
            db.flush()
            db.add(
                TransferEvent(
                    request_id=req.id, kind=TransferEventKind.STATUS.value,
                    status=TransferRequestStatus.REQUESTED.value,
                    actor_user_id=floor.id, note=f"{len(items)} item(s) requested",
                )
            )
            db.add(
                TransferEvent(
                    request_id=req.id, kind=TransferEventKind.ODOO.value,
                    actor_user_id=floor.id,
                    note="Odoo draft simulated (feature flag) — nothing was written",
                )
            )
            return req, lines

        mk_request(
            TransferRequestStatus.REQUESTED.value,
            "Demo: fresh request waiting for the warehouse",
            [(products[0], 6), (products[1], 4)],
        )

        working, _ = mk_request(
            TransferRequestStatus.WORKING.value,
            "Demo: warehouse is working on it",
            [(products[2], 10), (products[3], 12)],
        )
        db.add(
            TransferEvent(
                request_id=working.id, kind=TransferEventKind.STATUS.value,
                status=TransferRequestStatus.WORKING.value,
                actor_user_id=wh.id, note="warehouse is working on it",
            )
        )

        done, done_lines = mk_request(
            TransferRequestStatus.DONE.value,
            "Demo: yesterday's cart — count found one missing",
            [(products[4], 10)],
            sent=[9.0],
            counted=[8.0],
        )
        for status, actor, note in [
            (TransferRequestStatus.WORKING.value, wh.id, "warehouse is working on it"),
            (TransferRequestStatus.SENT.value, wh.id, "sent to floor staging"),
            (TransferRequestStatus.COUNTING.value, wh.id, "ready to count"),
            (TransferRequestStatus.DONE.value, floor.id, "closed manually (no live count transfer)"),
        ]:
            db.add(
                TransferEvent(
                    request_id=done.id, kind=TransferEventKind.STATUS.value,
                    status=status, actor_user_id=actor, note=note,
                )
            )
        db.add(
            Adjustment(
                request_id=done.id, line_id=done_lines[0].id, product_id=products[4].id,
                qty_expected=9, qty_counted=8, delta=-1,
                note=f"Count on #{done.id}",
            )
        )
        db.add(
            TransferEvent(
                request_id=done.id, kind=TransferEventKind.DISCREPANCY.value,
                actor_user_id=floor.id,
                note="1 discrepancy(ies) → adjustments queue: sent 9, counted 8 (-1)",
            )
        )
        print("  phase 2: transfer requests seeded (requested / working on it / done+adjustment)")

    db.commit()


def _ensure_grants_for(db: Session, order_list: OrderList, center: Center, granter: User | None) -> None:
    """Zone grant + center grant so the center can actually order from the list."""
    if center.zone_id and not db.scalar(
        select(OrderListZone).where(
            OrderListZone.order_list_id == order_list.id,
            OrderListZone.zone_id == center.zone_id,
        )
    ):
        db.add(OrderListZone(order_list_id=order_list.id, zone_id=center.zone_id,
                             granted_by_id=granter.id if granter else None))
    if not db.scalar(
        select(OrderListCenter).where(
            OrderListCenter.order_list_id == order_list.id,
            OrderListCenter.center_id == center.id,
        )
    ):
        db.add(OrderListCenter(order_list_id=order_list.id, center_id=center.id,
                               granted_by_id=granter.id if granter else None))


def seed_phase3_flows(db: Session) -> None:
    """Center orders across the lifecycle: approved history (feeds the
    reasonability engine and the duplicate button), a fresh pending order, a
    deliberately ABSURD pending order that trips the warning badges, a
    department water order that never touches Odoo, and simulated WhatsApp
    notifications. Idempotent — skips if any order exists."""
    settings = get_settings()
    if db.scalar(select(CenterOrder)) is not None:
        print("  phase 3: center orders already present — skipped")
        return
    admin = db.scalar(select(User).where(User.email == f"admin@{DEMO_DOMAIN}"))
    coord = db.scalar(select(User).where(User.email == f"coordinator@{DEMO_DOMAIN}"))
    orderer = db.scalar(select(User).where(User.email == f"orderer@{DEMO_DOMAIN}"))
    liaison = db.scalar(select(User).where(User.email == f"liaison@{DEMO_DOMAIN}"))
    kitchen_user = db.scalar(select(User).where(User.email == f"kitchen@{DEMO_DOMAIN}"))
    austin = db.scalar(select(Center).where(Center.name.ilike("Austin%")))
    kitchen = db.scalar(select(Center).where(Center.name == "Kitchen"))
    starter = db.scalar(select(OrderList).where(OrderList.name == "Center starter kit"))
    specials = db.scalar(select(OrderList).where(OrderList.name == "Festival specials"))
    if not (austin and orderer and coord and starter and starter.lines):
        print("  phase 3: missing demo prerequisites (Austin/orderer/starter list) — skipped")
        return

    # demo phones so the WhatsApp path has someone to (simulated-)message
    for user, phone in [
        (orderer, "+15125550171"), (coord, "+16155550142"),
        (liaison, "+19315550117"), (kitchen_user, "+19315550163"),
    ]:
        if user is not None and not user.phone:
            user.phone = phone

    # the demo coordinator must coordinate AUSTIN's zone (wherever the roster
    # put it) — that's who approves the demo orders and gets the pings
    if austin.zone_id:
        ensure_role(db, coord, Role.ZONE_COORDINATOR, zone_id=austin.zone_id)

    # make sure Austin can order from both demo catalogs, whatever its zone
    _ensure_grants_for(db, starter, austin, admin)
    if specials is not None:
        _ensure_grants_for(db, specials, austin, admin)
    db.flush()

    products = [line.product for line in starter.lines]
    now = utcnow()

    # --- an OOS-timeline demo item: out of stock, shipment due in ~5 weeks
    if specials is not None and specials.lines:
        oos_product = specials.lines[0].product
        level = db.scalar(
            select(StockLevel).where(
                StockLevel.product_id == oos_product.id, StockLevel.location_key == "bwhse"
            )
        )
        if level is not None:
            level.qty = 0
        db.add(
            IncomingMove(
                odoo_move_id=990001, product_id=oos_product.id, qty=48,
                expected_date=(now + timedelta(days=35)).date(),
                state="assigned", picking_ref="WH/IN/DEMO",
            )
        )

    # --- a low-stock item so exceeds_stock/low-count caveats have a target
    low_product = products[-1]
    low_level = db.scalar(
        select(StockLevel).where(
            StockLevel.product_id == low_product.id, StockLevel.location_key == "bwhse"
        )
    )
    if low_level is not None:
        low_level.qty = 3

    def mk_order(
        *, center: Center, creator: User, days_ago: float, status: str,
        items: list[tuple[Product, float]], source_key: str,
        decided_by: User | None = None, decision_note: str = "", notes: str = "",
        picking_status: str = OdooWriteOutcome.NONE.value, odoo_note: str = "",
    ) -> CenterOrder:
        created = now - timedelta(days=days_ago)
        order = CenterOrder(
            center_id=center.id, status=status, notes=notes,
            created_by_id=creator.id, source_location_key=source_key,
            picking_status=picking_status,
            decided_by_id=decided_by.id if decided_by else None,
            decided_at=(created + timedelta(hours=3)) if decided_by else None,
            decision_note=decision_note,
            created_at=created, updated_at=created,
        )
        db.add(order)
        db.flush()
        for p, qty in items:
            db.add(
                CenterOrderLine(
                    order_id=order.id, product_id=p.id, qty_requested=qty,
                    unit_price=float(p.retail_price or 0),
                )
            )
        db.add(
            CenterOrderEvent(
                order_id=order.id, kind=CenterOrderEventKind.STATUS.value,
                status=CenterOrderStatus.PENDING.value, actor_user_id=creator.id,
                note=f"{len(items)} item(s) requested", created_at=created,
            )
        )
        if decided_by is not None and status in (
            CenterOrderStatus.APPROVED.value, CenterOrderStatus.SHIPPED.value,
        ):
            db.add(
                CenterOrderEvent(
                    order_id=order.id, kind=CenterOrderEventKind.STATUS.value,
                    status=CenterOrderStatus.APPROVED.value, actor_user_id=decided_by.id,
                    note=decision_note or "approved",
                    created_at=created + timedelta(hours=3),
                )
            )
        if odoo_note:
            db.add(
                CenterOrderEvent(
                    order_id=order.id, kind=CenterOrderEventKind.ODOO.value,
                    actor_user_id=(decided_by or creator).id, note=odoo_note,
                    created_at=created + timedelta(hours=3),
                )
            )
        db.flush()
        db.refresh(order)
        return order

    # --- Austin's approved history (what "usual volume" means here)
    usual: list[list[tuple[Product, float]]] = [
        [(products[0], 6), (products[1], 4), (products[2], 6), (products[3], 2)],
        [(products[0], 6), (products[2], 4), (products[4 % len(products)], 3)],
        [(products[0], 8), (products[1], 6), (products[3], 2)],
    ]
    for i, items in enumerate(usual):
        mk_order(
            center=austin, creator=orderer, days_ago=35 - i * 12,
            status=CenterOrderStatus.APPROVED.value, items=items, source_key="bwhse",
            decided_by=coord, decision_note="approved",
            picking_status=OdooWriteOutcome.SIMULATED.value,
            odoo_note="Odoo draft simulated (feature flag) — nothing was written",
        )

    # --- Kitchen's department history: water, straight from the floor, no Odoo
    water = db.scalar(select(Product).where(Product.global_sku == "MAN-WATER"))
    if kitchen and kitchen_user and liaison and water:
        mk_order(
            center=kitchen, creator=kitchen_user, days_ago=9,
            status=CenterOrderStatus.APPROVED.value, items=[(water, 4.0)], source_key="floor",
            decided_by=liaison, decision_note="approved",
            odoo_note="no Odoo transfer — nothing on this order is Odoo-tracked; "
            "fulfilled directly from the Shoppe floor",
        )

    # --- a fresh, sensible pending order (the coordinator's approval demo)
    fresh_items: list[tuple[Product, float]] = [(products[0], 6), (products[1], 4), (products[2], 6)]
    fresh = mk_order(
        center=austin, creator=orderer, days_ago=0.05,
        status=CenterOrderStatus.PENDING.value, items=fresh_items, source_key="bwhse",
        notes="Regular biweekly restock",
    )
    a = assess_order(db, settings, austin, fresh_items, "bwhse", use_llm=False)
    fresh.reasonability, fresh.reasonability_level = a.as_dict(), a.level

    # --- the deliberately ABSURD pending order (reasonability must fire)
    absurd_items: list[tuple[Product, float]] = [(products[0], 80), (low_product, 25)]
    absurd = mk_order(
        center=austin, creator=orderer, days_ago=0.02,
        status=CenterOrderStatus.PENDING.value, items=absurd_items, source_key="bwhse",
        notes="Big festival coming up!!",
    )
    a = assess_order(db, settings, austin, absurd_items, "bwhse", use_llm=False)
    absurd.reasonability, absurd.reasonability_level = a.as_dict(), a.level
    if a.level in ("info", "warn"):
        db.add(
            CenterOrderEvent(
                order_id=absurd.id, kind=CenterOrderEventKind.REASONABILITY.value,
                note=a.summary,
            )
        )

    # --- notifications for the two live pending orders (simulated: flags off)
    pinged: list = []
    for order in (fresh, absurd):
        pinged += notify_service.enqueue_order_notifications(
            db, settings, order, NotificationKind.ORDER_PLACED
        )
    db.commit()
    notify_service.deliver_now(db, settings, pinged)
    print(
        f"  phase 3: center orders seeded — {len(usual)} approved (history), "
        f"1 dept water order, 2 pending (one absurd: {absurd.reasonability_level}), "
        f"{len(pinged)} notification(s) recorded"
    )


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

        # 7. phase-3 flows: center orders, reasonability, notifications
        seed_phase3_flows(db)

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
