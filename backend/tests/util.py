from __future__ import annotations

from app.models import (
    Center,
    FeatureFlag,
    Product,
    ProductSource,
    Role,
    RoleAssignment,
    User,
    Zone,
)
from sqlalchemy.orm import Session


def mk_user(db: Session, email: str, *roles: tuple[Role, int | None, int | None]) -> User:
    user = User(email=email.lower(), display_name=email.split("@")[0])
    db.add(user)
    db.flush()
    for role, zone_id, center_id in roles:
        db.add(
            RoleAssignment(user_id=user.id, role=role.value, zone_id=zone_id, center_id=center_id)
        )
    db.commit()
    return user


def mk_zone(db: Session, name: str, kind: str = "field") -> Zone:
    zone = Zone(name=name, kind=kind)
    db.add(zone)
    db.commit()
    return zone


def mk_center(db: Session, name: str, zone_id: int | None = None, active: bool = True) -> Center:
    center = Center(name=name, city=name, zone_id=zone_id, is_active=active)
    db.add(center)
    db.commit()
    return center


def mk_product(
    db: Session,
    sku: str,
    name: str,
    category: str = "Copper",
    source: str = ProductSource.ODOO.value,
    odoo_id: int | None = None,
    price: float = 10.0,
    stock_tracked: bool = True,
) -> Product:
    p = Product(
        global_sku=sku,
        us_sku=sku,
        name=name,
        category=category,
        retail_price=price,
        source=source,
        odoo_product_id=odoo_id,
        is_stock_tracked=stock_tracked,
        is_active=True,
    )
    db.add(p)
    db.commit()
    return p


def login(client, email: str) -> dict[str, str]:
    r = client.post("/api/v1/auth/request-code", json={"identifier": email})
    assert r.status_code == 200, r.text
    code = r.json()["dev_code"]
    r = client.post("/api/v1/auth/verify", json={"identifier": email, "code": code})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def set_flag(db: Session, key: str, enabled: bool) -> None:
    flag = db.get(FeatureFlag, key)
    if flag is None:
        flag = FeatureFlag(key=key, enabled=enabled, description="test")
        db.add(flag)
    else:
        flag.enabled = enabled
    db.commit()
