"""Sales dashboard: period math, aggregation correctness (channels, revenue
honesty, breakdowns), the generated narrative/Q&A fallbacks, and the
acceptance perf bar (overview under a second on seed-sized data)."""
from __future__ import annotations

import time
from datetime import date

from app.config import get_settings
from app.models import (
    CustomerFirstSeen,
    Product,
    Role,
    SalesCenterMonthly,
    SalesMonthly,
    SalesOrdersMonthly,
    utcnow,
)
from app.reporting.narrative import answer_question, build_facts, narrative
from app.reporting.queries import breakdown, orders_summary, resolve_period, sales_overview
from sqlalchemy import insert, select

from .util import login, mk_product, mk_user

TODAY = utcnow().date()


def _month_shift(anchor: tuple[int, int], n: int) -> tuple[int, int]:
    total = anchor[0] * 12 + (anchor[1] - 1) + n
    return total // 12, total % 12 + 1


CUR = (TODAY.year, TODAY.month)
M1 = _month_shift(CUR, -1)  # last month
M2 = _month_shift(CUR, -2)
M3 = _month_shift(CUR, -3)
M4 = _month_shift(CUR, -4)


# ------------------------------------------------------------- period math
def test_resolve_period_shapes():
    today = date(2026, 7, 21)
    p = resolve_period("mtd", today)
    assert p.months == [(2026, 7)] and p.prior_months == [(2026, 6)]
    p = resolve_period("last_month", today)
    assert p.months == [(2026, 6)]
    p = resolve_period("3m", today)
    assert p.months == [(2026, 5), (2026, 6), (2026, 7)]
    assert p.prior_months == [(2026, 2), (2026, 3), (2026, 4)]
    p = resolve_period("qtd", today)
    assert p.months == [(2026, 7)]
    p = resolve_period("ytd", today)
    assert p.months[0] == (2026, 1) and p.months[-1] == (2026, 7)
    assert len(p.prior_months) == len(p.months)
    assert resolve_period("garbage", today).key == "3m"


# ------------------------------------------------------------- aggregation
def _seed_sales(db):
    copper = mk_product(db, "CA0000000031", "Copper Bottle", category="Copper", price=30.0, odoo_id=701)
    incense = mk_product(db, "IN0000000032", "Rose Incense", category="Incense", price=8.0, odoo_id=702)
    rows = [
        # current period (last 3 months incl. current): mixed channels
        {"product_id": copper.id, "year": M1[0], "month": M1[1], "channel": "shoppe", "units": 10, "amount": 300.0},
        {"product_id": copper.id, "year": M1[0], "month": M1[1], "channel": "online", "units": 5, "amount": 150.0},
        {"product_id": copper.id, "year": M2[0], "month": M2[1], "channel": "city_center", "units": 4, "amount": 120.0},
        # legacy pre-split row: NULL amount → estimated at units × $30
        {"product_id": copper.id, "year": M2[0], "month": M2[1], "channel": "pos", "units": 2, "amount": None},
        {"product_id": incense.id, "year": M1[0], "month": M1[1], "channel": "shoppe", "units": 20, "amount": 160.0},
        # prior period (months -4..-6 for the 3m window ending now)
        {"product_id": copper.id, "year": M4[0], "month": M4[1], "channel": "shoppe", "units": 8, "amount": 240.0},
    ]
    db.execute(insert(SalesMonthly), rows)
    db.execute(
        insert(SalesCenterMonthly),
        [
            {"config_name": "Austin", "center_id": None, "year": M2[0], "month": M2[1], "units": 4, "amount": 120.0},
            {"config_name": "Dallas", "center_id": None, "year": M4[0], "month": M4[1], "units": 2, "amount": 60.0},
        ],
    )
    # order-level facts: 17 orders in the window, 8 in the prior period
    db.execute(
        insert(SalesOrdersMonthly),
        [
            {"year": M1[0], "month": M1[1], "channel": "shoppe", "orders": 10, "amount": 460.0,
             "orders_with_customer": 9, "distinct_customers": 8, "new_customers": 3, "returning_customers": 5},
            {"year": M1[0], "month": M1[1], "channel": "online", "orders": 5, "amount": 150.0,
             "orders_with_customer": 5, "distinct_customers": 5, "new_customers": 2, "returning_customers": 3},
            {"year": M2[0], "month": M2[1], "channel": "city_center", "orders": 2, "amount": 120.0,
             "orders_with_customer": 1, "distinct_customers": 1, "new_customers": 1, "returning_customers": 0},
            {"year": M4[0], "month": M4[1], "channel": "shoppe", "orders": 8, "amount": 240.0,
             "orders_with_customer": 8, "distinct_customers": 6, "new_customers": 6, "returning_customers": 0},
        ],
    )
    # first-seen memory matching the new_customers above (period-exact count)
    first_seen_rows = [
        {"partner_id": 9100 + i, "channel": "shoppe", "first_order_on": date(M1[0], M1[1], 5)}
        for i in range(3)
    ] + [
        {"partner_id": 9200 + i, "channel": "online", "first_order_on": date(M1[0], M1[1], 9)}
        for i in range(2)
    ] + [
        {"partner_id": 9300, "channel": "city_center", "first_order_on": date(M2[0], M2[1], 12)},
        # prior-period first-seen — must NOT count as new in this window
        {"partner_id": 9400, "channel": "shoppe", "first_order_on": date(M4[0], M4[1], 2)},
    ]
    db.execute(insert(CustomerFirstSeen), first_seen_rows)
    db.commit()
    return copper, incense


def test_overview_totals_channels_and_estimates(db):
    _seed_sales(db)
    p = resolve_period("3m")
    ov = sales_overview(db, p)

    t = ov["totals"]
    # revenue: 300+150+120+160 real + 2×$30 estimated = 790
    assert t["revenue"] == 790.0
    assert t["units"] == 41.0
    assert round(t["estimated_share"], 4) == round(60 / 790, 4)
    assert t["has_legacy_channel_rows"] is True
    assert t["prior_revenue"] == 240.0
    assert t["revenue_delta_pct"] is not None

    channels = {c["channel"]: c for c in ov["channels"]}
    # legacy 'pos' folds into shoppe for display: 300+160+60 = 520
    assert channels["shoppe"]["revenue"] == 520.0
    assert channels["shoppe"]["label"] == "Shoppe"
    assert channels["online"]["revenue"] == 150.0
    assert channels["city_center"]["revenue"] == 120.0
    assert round(sum(c["share"] for c in ov["channels"]), 6) == 1.0

    # series rows merged after the legacy fold: one shoppe row per month
    m2_shoppe = [
        r for r in ov["series"] if r["channel"] == "shoppe" and r["month"] == f"{M2[0]:04d}-{M2[1]:02d}"
    ]
    assert len(m2_shoppe) == 1 and m2_shoppe[0]["revenue"] == 60.0

    # centers panel from the rollup table
    assert ov["centers"][0]["label"] == "Austin"
    assert ov["centers"][0]["revenue"] == 120.0


def test_breakdown_dimensions(db):
    copper, incense = _seed_sales(db)
    p = resolve_period("3m")
    cats = {r["key"]: r for r in breakdown(db, p, dim="category")}
    assert cats["Copper"]["revenue"] == 630.0  # 300+150+120+60(est)
    assert cats["Incense"]["revenue"] == 160.0
    assert round(cats["Copper"]["share"] + cats["Incense"]["share"], 6) == 1.0
    assert cats["Copper"]["prior_revenue"] == 240.0

    prods = breakdown(db, p, dim="product")
    assert prods[0]["label"] == "Copper Bottle" and prods[0]["sku"] == "CA0000000031"
    assert prods[0]["revenue"] == 630.0

    chans = {r["key"]: r for r in breakdown(db, p, dim="channel")}
    assert chans["shoppe"]["revenue"] == 520.0  # legacy folded

    centers = breakdown(db, p, dim="center")
    assert centers[0]["key"] == "Austin"
    # prior-period center (Dallas) shows with zero current revenue? no — only
    # current-window configs are listed; Dallas is prior-only
    assert all(c["key"] != "Dallas" for c in centers)


def test_reports_api_requires_admin(client, db):
    _seed_sales(db)
    mk_user(db, "admin@test.io", (Role.ADMIN, None, None))
    mk_user(db, "floor@test.io", (Role.SHOPPE_FLOOR, None, None))
    admin = login(client, "admin@test.io")
    floor = login(client, "floor@test.io")
    assert client.get("/api/v1/reports/sales", headers=floor).status_code == 403
    r = client.get("/api/v1/reports/sales", params={"period": "3m"}, headers=admin)
    assert r.status_code == 200 and r.json()["totals"]["revenue"] == 790.0
    r = client.get(
        "/api/v1/reports/breakdown", params={"dim": "product"}, headers=admin
    )
    assert r.status_code == 200 and r.json()["rows"]


def test_orders_summary_and_loyalty(db):
    _seed_sales(db)
    p = resolve_period("3m")
    o = orders_summary(db, p)
    t = o["totals"]
    assert t["orders"] == 17
    assert t["amount"] == 730.0
    assert t["aov"] == round(730.0 / 17, 2)
    assert t["prior_orders"] == 8
    assert t["prior_aov"] == 30.0
    assert t["new_customers"] == 6  # exact, from first_seen — prior-period row excluded
    assert round(t["known_customer_share"], 4) == round(15 / 17, 4)
    # returning share reads the latest complete month (M1): 8 returning of 13 active
    assert t["returning_share_month"] == f"{M1[0]:04d}-{M1[1]:02d}"
    assert round(t["returning_share_last_month"], 4) == round(8 / 13, 4)
    months = {r["month"]: r for r in o["series"]}
    m1 = months[f"{M1[0]:04d}-{M1[1]:02d}"]
    assert m1["orders"] == 15 and m1["aov"] == round(610.0 / 15, 2)
    assert m1["new_customers"] == 5 and m1["returning_customers"] == 8


def test_scope_filters_overview_and_orders(db):
    _seed_sales(db)
    p = resolve_period("3m")
    online = sales_overview(db, p, scope="online")
    assert online["scope"] == "online"
    assert online["totals"]["revenue"] == 150.0
    assert [c["channel"] for c in online["channels"]] == ["online"]
    assert online["orders"]["totals"]["orders"] == 5
    assert online["orders"]["totals"]["new_customers"] == 2
    assert online["centers"] == []  # centers panel is an all/city-center thing

    in_person = sales_overview(db, p, scope="in_person")
    # shoppe rows + the legacy 'pos' estimate, no online/city_center
    assert in_person["totals"]["revenue"] == 520.0
    assert in_person["orders"]["totals"]["orders"] == 10

    city = sales_overview(db, p, scope="city_center")
    assert city["totals"]["revenue"] == 120.0
    assert city["orders"]["totals"]["orders"] == 2
    assert city["centers"][0]["label"] == "Austin"

    cats = breakdown(db, p, dim="category", scope="online")
    assert {r["key"]: r["revenue"] for r in cats} == {"Copper": 150.0}


# ------------------------------------------------------- narrative and Q&A
def test_narrative_heuristic_and_cache(db):
    _seed_sales(db)
    settings = get_settings()  # no ANTHROPIC_API_KEY in tests → heuristic
    p = resolve_period("3m")
    n1 = narrative(db, settings, p)
    assert n1["source"] == "heuristic"
    assert n1["generated"] is True
    assert "$790" in n1["headline"]
    assert n1["bullets"] and n1["actions"]

    # unchanged data → served from cache, byte-identical
    n2 = narrative(db, settings, p)
    assert n2 == n1

    # new data → new facts hash → regenerated
    copper_id = db.scalar(select(Product.id).order_by(Product.id).limit(1))
    db.execute(
        insert(SalesMonthly),
        [{"product_id": copper_id, "year": M1[0], "month": M1[1], "channel": "campus_other", "units": 3, "amount": 90.0}],
    )
    db.commit()
    n3 = narrative(db, settings, p)
    assert n3["headline"] != n1["headline"]


def test_qa_heuristic_answers(db):
    _seed_sales(db)
    settings = get_settings()
    p = resolve_period("3m")
    facts = build_facts(db, p)
    assert facts["centers"][0]["label"] == "Austin"

    a = answer_question(db, settings, p, "Which centers grew fastest this quarter?")
    assert a["source"] == "heuristic" and a["generated"] is True
    assert "Austin" in a["answer"]
    a = answer_question(db, settings, p, "What were the top products?")
    assert "Copper Bottle" in a["answer"]
    a = answer_question(db, settings, p, "How did online do?")
    assert "Online" in a["answer"]


# ------------------------------------------------------------- performance
def test_overview_under_a_second_on_seed_sized_data(client, db):
    """Acceptance: dashboard loads in under a second on seed data (~1,200
    products × 24 months × 2 channels ≈ 58k monthly rows)."""
    products = [
        {
            "global_sku": f"PF{i:010d}", "us_sku": f"PF{i:010d}", "name": f"Perf Product {i}",
            "category": f"Cat {i % 12}", "retail_price": 10.0, "source": "odoo",
            "odoo_product_id": 10_000 + i, "is_stock_tracked": True, "is_active": True,
        }
        for i in range(1200)
    ]
    db.execute(insert(Product), products)
    db.commit()
    ids = list(db.scalars(select(Product.id)))
    rows = []
    for pid in ids:
        for back in range(24):
            y, m = _month_shift(CUR, -back)
            rows.append({"product_id": pid, "year": y, "month": m, "channel": "shoppe",
                             "units": 5.0, "amount": 50.0})
            rows.append({"product_id": pid, "year": y, "month": m, "channel": "online",
                             "units": 2.0, "amount": 20.0})
    for i in range(0, len(rows), 10_000):
        db.execute(insert(SalesMonthly), rows[i : i + 10_000])
    db.commit()

    mk_user(db, "admin@test.io", (Role.ADMIN, None, None))
    admin = login(client, "admin@test.io")
    start = time.perf_counter()
    r = client.get("/api/v1/reports/sales", params={"period": "12m"}, headers=admin)
    elapsed = time.perf_counter() - start
    assert r.status_code == 200
    assert r.json()["totals"]["units"] > 0
    assert elapsed < 1.0, f"overview took {elapsed:.2f}s"
