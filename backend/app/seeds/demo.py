"""Demo seed: a fully working stack with ZERO real credentials.

    docker compose up && make seed

Loads: coordinator sheet (real file if present in docs/reference, otherwise a
bundled sample), demo login users for every role, the III Departments zone
with non-Odoo items, feature flags (all writes OFF), then runs every sync
domain against the fixture simulator (~1,200 products, 24 months of sales).

Idempotent — safe to re-run.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..centers.importer import run_import
from ..config import get_settings
from ..db import get_sessionmaker
from ..models import (
    Center,
    FeatureFlag,
    Product,
    ProductSource,
    Role,
    RoleAssignment,
    User,
    Zone,
    ZoneKind,
)
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
