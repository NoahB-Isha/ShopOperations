"""Deterministic demo fixture generator.

Produces a fixture directory the OdooSimulator can serve: ~1,200 products,
quants at BWHSE/FLOOR/STAGING, 24 months of POS + online sales history, and
a plate of incoming moves. Seeded RNG -> the same catalog every run, so demo
walkthroughs and screenshots stay stable.

Run: `python -m app.odoo.fixtures.generate` (or `make fixtures`).
"""
from __future__ import annotations

import json
import math
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

LOCATIONS = [
    {"id": 11, "complete_name": "III/Stock", "usage": "view"},
    {"id": 12, "complete_name": "III/Stock/BWHSE", "usage": "internal"},
    # hyphenated like production (verified live 2026-07-10)
    {"id": 13, "complete_name": "III/Stock/III-FLOOR-STAGING", "usage": "internal"},
    {"id": 14, "complete_name": "III/Stock/III-FLOOR", "usage": "internal"},
    {"id": 15, "complete_name": "Partner Locations/Customers", "usage": "customer"},
    {"id": 16, "complete_name": "Partner Locations/Vendors", "usage": "supplier"},
    # warehouse bins — production stores most BWHSE stock in bins like these
    {"id": 17, "complete_name": "III/Stock/BWHSE/A/1/1/1", "usage": "internal"},
    {"id": 18, "complete_name": "III/Stock/BWHSE/B/2/1/1", "usage": "internal"},
]

PICKING_TYPES = [
    {"id": 1, "name": "III: Receipts", "code": "incoming"},
    {"id": 5, "name": "III: Internal Transfers", "code": "internal"},
    {"id": 7, "name": "III: Delivery Orders", "code": "outgoing"},
]

# (category, sku prefix, monthly velocity, (min price, max price), share of catalog)
CATEGORIES = [
    ("Copper", "CA", 55, (18, 90), 0.10),
    ("Rudraksha", "RU", 40, (12, 160), 0.09),
    ("Sacred Ash & Offerings", "SA", 90, (4, 25), 0.08),
    ("Incense & Dhoop", "IN", 120, (6, 30), 0.10),
    ("Ayurveda & Wellness", "AY", 45, (9, 60), 0.10),
    ("Bloom", "BL", 35, (8, 45), 0.07),
    ("Oral Care", "OC", 60, (5, 18), 0.04),
    ("Snacks", "SN", 150, (3, 15), 0.08),
    ("Home & Living", "HL", 25, (14, 120), 0.09),
    ("Books & Media", "BM", 30, (8, 40), 0.08),
    ("Silver Jewelry", "SJ", 8, (25, 220), 0.05),
    ("Gold Jewelry", "GJ", 2, (150, 1200), 0.02),
    ("Yoga Props", "YP", 20, (10, 85), 0.06),
    ("Apparel", "AP", 15, (15, 70), 0.04),  # clothing — out of scope for ordering flows
]

BASES = {
    "Copper": ["Copper Water Bottle", "Hammered Copper Bottle", "Copper Tumbler", "Copper Jug",
               "Copper Travel Flask", "Copper Straw Set", "Copper Cleaning Kit", "Copper Pot"],
    "Rudraksha": ["Rudraksha Mala", "Panchamukhi Bead", "Rudraksha Bracelet", "Spatika Mala",
                  "Rudraksha Pendant", "Isha Consecrated Bead", "Mala Counter", "Bead Care Oil"],
    "Sacred Ash & Offerings": ["Vibhuti Pouch", "Vibhuti Jar", "Kumkum Jar", "Turmeric Powder",
                               "Sacred Ash Locket", "Offering Tray", "Abhaya Sutra", "Rakhsa Thread"],
    "Incense & Dhoop": ["Sandalwood Incense", "Rose Incense", "Sambrani Cup", "Dhoop Sticks",
                        "Loban Resin", "Incense Holder", "Camphor Tablets", "Guggul Dhoop"],
    "Ayurveda & Wellness": ["Neem Powder", "Triphala Tablets", "Ashwagandha Powder", "Herbal Balm",
                            "Nasika Oil", "Kansa Wand", "Chyawanprash", "Herbal Tea Blend"],
    "Bloom": ["Bloom Ghee", "Bloom Honey", "Bloom Millet Mix", "Bloom Spice Blend",
              "Bloom Pickle", "Bloom Jaggery", "Bloom Papad", "Bloom Health Mix"],
    "Oral Care": ["Neem Toothpaste", "Charcoal Toothpaste", "Copper Tongue Cleaner",
                  "Herbal Tooth Powder", "Miswak Stick", "Mouth Freshener", "Gum Care Oil", "Kids Toothpaste"],
    "Snacks": ["Roasted Chana", "Banana Chips", "Millet Cookies", "Masala Peanuts",
               "Dry Fruit Laddu", "Ragi Chips", "Trail Mix", "Murukku"],
    "Home & Living": ["Brass Lamp", "Cotton Throw", "Meditation Cushion", "Clay Diya Set",
                      "Wall Hanging", "Jute Basket", "Bell Chime", "Coir Doormat"],
    "Books & Media": ["Inner Engineering Book", "Karma Book", "Adiyogi Book", "Chant CD",
                      "Meditation Guide", "Sadhguru Quotes Deck", "Journal", "Calendar"],
    "Silver Jewelry": ["Silver Pendant", "Silver Ring", "Silver Anklet", "Silver Earrings",
                       "Silver Bracelet", "Silver Toe Ring", "Nataraja Pendant", "Silver Chain"],
    "Gold Jewelry": ["Gold Pendant", "Gold Ring", "Gold Earrings", "Gold Chain"],
    "Yoga Props": ["Yoga Mat", "Meditation Shawl", "Yoga Block", "Bolster",
                   "Mat Bag", "Copper Neti Pot", "Eye Pillow", "Asana Strap"],
    "Apparel": ["Kurta", "Meditation Pants", "Isha T-Shirt", "Shawl", "Dhoti", "Scarf"],
}

VARIANTS = ["Small", "Medium", "Large", "Classic", "Deluxe", "Set of 2", "Set of 3", "Gift Box",
            "250g", "500g", "950ml", "700ml", "5mm", "7mm", "Natural", "Dark", "Om Engraved",
            "Travel Size", "Family Pack", "Premium"]


def _seasonality(category: str, month: int) -> float:
    base = 1.0 + 0.18 * math.sin((month - 3) * math.pi / 6)  # gentle annual wave
    if month == 12:  # gifting bump
        base *= 1.5 if category in ("Copper", "Books & Media", "Home & Living", "Silver Jewelry") else 1.15
    if category == "Bloom" and month in (6, 7, 8):
        base *= 1.25
    return base


def generate_fixtures(
    out_dir: Path,
    product_count: int = 1200,
    months: int = 24,
    seed: int = 42,
    now: datetime | None = None,
) -> dict[str, int]:
    rng = random.Random(seed)
    now = now or datetime.now(UTC)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------- products
    products: list[dict] = []
    used_names: set[str] = set()
    pid = 100
    serial = 0
    for cat, prefix, velocity, (lo, hi), share in CATEGORIES:
        target = max(4, round(product_count * share))
        bases = BASES[cat]
        for i in range(target):
            base = bases[i % len(bases)]
            variant = VARIANTS[(i // len(bases)) % len(VARIANTS)]
            name = f"{base} — {variant}" if i >= len(bases) else base
            if name in used_names:
                name = f"{name} ({i})"
            used_names.add(name)
            pid += 1
            serial += 1
            # ~12% get US/domestic-style codes; the rest look like India refs
            if rng.random() < 0.12 or cat == "Snacks":
                code = f"US-{prefix}{serial:04d}"
            else:
                code = f"{prefix}{serial:010d}"
            price = round(rng.uniform(lo, hi), 2)
            products.append(
                {
                    "id": pid,
                    "default_code": code,
                    "name": name,
                    "display_name": f"[{code}] {name}",
                    "categ_id": [CATEGORIES.index((cat, prefix, velocity, (lo, hi), share)) + 1, cat],
                    "list_price": price,
                    "standard_price": round(price * rng.uniform(0.35, 0.6), 2),
                    "barcode": f"890{pid:010d}",
                    "sale_ok": True,
                    "active": rng.random() > 0.03,
                    "_velocity": velocity,  # generator-internal, stripped below
                    "_category": cat,
                }
            )
    products = products[:product_count]

    # ------------------------------------------------------------- quants
    quants: list[dict] = []
    qid = 1
    bwhse_spots = [
        (12, "III/Stock/BWHSE"),
        (17, "III/Stock/BWHSE/A/1/1/1"),
        (18, "III/Stock/BWHSE/B/2/1/1"),
    ]
    for p in products:
        vel = p["_velocity"] * rng.uniform(0.1, 2.2)
        if rng.random() < 0.85:
            qid += 1
            loc_id, loc_name = rng.choice(bwhse_spots)  # much of BWHSE lives in bins
            quants.append(_quant(qid, p, loc_id, loc_name, round(max(0, rng.gauss(vel * 3, vel)), 0)))
        if rng.random() < 0.30:
            qid += 1
            quants.append(_quant(qid, p, 14, "III/Stock/III-FLOOR", float(rng.randint(0, 48))))
        if rng.random() < 0.012:
            qid += 1
            quants.append(_quant(qid, p, 13, "III/Stock/III-FLOOR-STAGING", float(rng.randint(1, 24))))

    # ------------------------------------------------------------- sales
    # One synthetic order per channel per month, holding one line per selling
    # product — same monthly aggregates as real line-level data, tiny footprint.
    pos_orders, pos_lines, sale_orders, sale_lines = [], [], [], []
    sellers = [p for p in products if rng.random() < 0.62 and p["active"]]
    line_id = 1
    for m_back in range(months):
        total = now.year * 12 + (now.month - 1) - m_back
        y, mo = total // 12, total % 12 + 1
        stamp = f"{y}-{mo:02d}-15 12:00:00"
        pos_oid, sale_oid = 5000 + m_back, 8000 + m_back
        pos_orders.append({"id": pos_oid, "name": f"III/POS/{y}{mo:02d}", "date_order": stamp, "state": "done"})
        sale_orders.append({"id": sale_oid, "name": f"S{y}{mo:02d}", "date_order": stamp, "state": "sale"})
        for p in sellers:
            monthly = p["_velocity"] * _seasonality(p["_category"], mo) * rng.uniform(0.5, 1.5)
            pos_qty = round(monthly * rng.uniform(0.45, 0.75))
            online_qty = round(monthly * rng.uniform(0.2, 0.5))
            ref = [p["id"], p["display_name"]]
            if pos_qty > 0:
                line_id += 1
                pos_lines.append({"id": line_id, "order_id": [pos_oid, f"III/POS/{y}{mo:02d}"], "product_id": ref, "qty": pos_qty})
            if online_qty > 0:
                line_id += 1
                sale_lines.append({"id": line_id, "order_id": [sale_oid, f"S{y}{mo:02d}"], "product_id": ref, "product_uom_qty": online_qty})

    # ------------------------------------------------------------- incoming
    incoming: list[dict] = []
    arrivals = rng.sample(sellers, min(45, len(sellers)))
    for i, p in enumerate(arrivals, start=1):
        eta = now + timedelta(days=rng.randint(5, 150))
        incoming.append(
            {
                "id": 9000 + i,
                "product_id": [p["id"], p["display_name"]],
                "product_qty": float(rng.choice([24, 48, 96, 144, 240])),
                "date": eta.strftime("%Y-%m-%d %H:%M:%S"),
                "state": rng.choice(["assigned", "confirmed", "waiting"]),
                "picking_id": [900 + i, f"III/IN/{900 + i:05d}"],
                "picking_code": "incoming",
            }
        )

    for p in products:
        p.pop("_velocity"), p.pop("_category")

    schema = {
        "product.product": ["id", "default_code", "name", "display_name", "categ_id",
                            "standard_price", "list_price", "barcode", "sale_ok", "active"],
        "stock.location": ["id", "complete_name", "usage"],
        "stock.quant": ["id", "product_id", "location_id", "quantity"],
        "stock.picking": ["id", "name", "origin", "state", "location_id", "location_dest_id",
                          "picking_type_id", "move_ids", "note"],
        "stock.move": ["id", "name", "product_id", "product_uom_qty", "product_qty", "date",
                       "state", "location_id", "location_dest_id", "picking_id", "picking_code"],
        "stock.picking.type": ["id", "name", "code"],
        "pos.order": ["id", "name", "date_order", "state"],
        "pos.order.line": ["id", "order_id", "product_id", "qty"],
        "sale.order": ["id", "name", "date_order", "state"],
        "sale.order.line": ["id", "order_id", "product_id", "product_uom_qty"],
    }

    files = {
        "product.product": products,
        "stock.location": LOCATIONS,
        "stock.picking.type": PICKING_TYPES,
        "stock.quant": quants,
        "pos.order": pos_orders,
        "pos.order.line": pos_lines,
        "sale.order": sale_orders,
        "sale.order.line": sale_lines,
        "stock.move": incoming,
        "stock.picking": [],
    }
    for model, rows in files.items():
        (out_dir / f"{model}.json").write_text(json.dumps(rows))
    (out_dir / "_schema.json").write_text(json.dumps(schema, indent=1))

    return {model: len(rows) for model, rows in files.items()}


def _quant(qid: int, product: dict, loc_id: int, loc_name: str, qty: float) -> dict:
    return {
        "id": qid,
        "product_id": [product["id"], product["display_name"]],
        "location_id": [loc_id, loc_name],
        "quantity": float(qty),
    }


if __name__ == "__main__":
    from ...config import get_settings

    counts = generate_fixtures(get_settings().fixtures_path)
    print(f"Fixtures written to {get_settings().fixtures_path}:")
    for model, n in counts.items():
        print(f"  {model:<18} {n}")
