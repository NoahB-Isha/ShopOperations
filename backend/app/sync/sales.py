"""Sales-history sync — the one heavy query, handled politely.

On this instance the sale.report aggregate returns nothing (learned the hard
way in the ops project), so sales come from POS order lines + online sale
order lines, joined to their parent orders for dates, counting only confirmed
states.

Channels: every POS order is classified by its pos.config (verified live
2026-07-21 — 53 configs: 'III Floor' plus one per city center plus campus
one-offs like 'III-Snack' and 'iii - Events'):
  * shoppe        — the campus floor configs (name starts with 'III Floor')
  * city_center   — config name matches a Center (normalized), rolled up into
                    sales_center_monthly as well (config-level, no product dim)
  * campus_other  — everything else (snacks, events, tent, self-service…)
Online sale orders stay channel 'online'. Rows written before the split carry
the legacy value 'pos' until an admin re-runs the backfill (admin → "rebuild
sales history"); the dashboard counts them as Shoppe, labeled as legacy.
Ambiguous config names are admin-fixable via the `sales_channel_aliases`
AppSetting ({config name: channel}) without a deploy.

Amounts: line revenue is captured alongside units (pos: price_subtotal_incl,
online: price_total — both tax-in). NULL amounts mean "row predates capture".

Order headers feed `sales_orders_monthly` (order counts, header revenue, and
the customer-loyalty split). POS orders technically almost always carry a
partner — but it's usually the register's own HOUSE account ('Isha Life USA
- III FLOOR POS' holds ~99% of Shoppe orders; verified live 2026-07-25), not
a person. Dominant partners are detected per channel (`detect_house_partners`)
and excluded from every customer metric, so walk-ins count as orders, never
as customers; online partners are real people. `customer_first_seen`
remembers each partner's earliest order per channel so new-vs-returning
stays correct across incremental windows; only partner ids are stored,
never contact details.

Cadence policy: ONE full backfill (24 months) at setup; afterwards hourly
incrementals touch only the current month, and the previous month is
re-pulled once a day to catch late edits. Markers live in sync_state.extra.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import (
    AppSetting,
    Center,
    CustomerFirstSeen,
    Product,
    SalesCenterMonthly,
    SalesChannel,
    SalesDaily,
    SalesMonthly,
    SalesOrdersMonthly,
    SyncState,
    utcnow,
)
from ..odoo.protocol import OdooConnection

# (line model, qty field, amount field, parent order model, confirmed states, source kind)
SOURCES = [
    ("pos.order.line", "qty", "price_subtotal_incl", "pos.order", ("paid", "done", "invoiced"), "pos"),
    ("sale.order.line", "product_uom_qty", "price_total", "sale.order", ("sale", "done"), "online"),
]

ALIASES_SETTING_KEY = "sales_channel_aliases"
_CHANNEL_VALUES = {c.value for c in SalesChannel}


def _first_of_month_minus(anchor: date, months: int) -> date:
    total = anchor.year * 12 + (anchor.month - 1) - months
    return date(total // 12, total % 12 + 1, 1)


def _model_exists(conn: OdooConnection, model: str) -> bool:
    try:
        return bool(conn.fields_get(model))
    except Exception:
        return False


def _normalize(name: str) -> str:
    """Config/center names compared alphanumeric-lowercase only, so
    'WashingtonD.C.' matches 'Washington D.C.' and 'DALLAS' matches 'Dallas'."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def classify_pos_config(
    config_name: str, center_names: set[str], aliases: dict[str, str]
) -> str:
    """App channel for one pos.config. `center_names` and `aliases` keys are
    pre-normalized. Admin aliases win; then the floor prefix; then a center
    match; everything else is campus-other (honest bucket, never a guess)."""
    n = _normalize(config_name)
    alias = aliases.get(n, "")
    if alias in _CHANNEL_VALUES:
        return alias
    if not n:
        return SalesChannel.CAMPUS_OTHER.value
    if n.startswith("iiifloor"):
        return SalesChannel.SHOPPE.value
    if n in center_names:
        return SalesChannel.CITY_CENTER.value
    return SalesChannel.CAMPUS_OTHER.value


def _channel_aliases(db: Session) -> dict[str, str]:
    setting = db.get(AppSetting, ALIASES_SETTING_KEY)
    raw = setting.value if setting and isinstance(setting.value, dict) else {}
    return {_normalize(k): str(v) for k, v in raw.items()}


def _order_date(d: str) -> date | None:
    """UTC date from an Odoo datetime string, None when unparseable."""
    if len(d) < 10 or not (d[:4].isdigit() and d[8:10].isdigit()):
        return None
    try:
        return date(int(d[:4]), int(d[5:7]), int(d[8:10]))
    except ValueError:
        return None


# Registers attribute walk-in POS orders to a per-register "house" partner
# ('Isha Life USA - III FLOOR POS' carries ~99% of Shoppe orders; the LA
# register has its own — verified live 2026-07-25). Those records are
# registers, not people: they'd make every customer metric nonsense (1
# "returning customer" per month, 96% "known customers"). A partner
# dominating a group's window — a whole channel OR a single register, since
# campus_other aggregates many registers — is treated as a house account and
# excluded from customer metrics; detections are remembered in
# sync_state.extra so small hourly windows stay honest.
HOUSE_PARTNER_MIN_ORDERS = 50
HOUSE_PARTNER_MIN_SHARE = 0.30
# …and a register default that's only attached to SOME of its register's
# orders never wins on share ('ISHA LIFE USA - LA POS': ~50–130 orders every
# month, ~10% of its channel). No person places 25 orders in one month —
# sustained monthly volume is a register, not a customer.
HOUSE_PARTNER_MONTHLY_ORDERS = 25


def detect_house_partners(
    partner_orders: dict[tuple[int, str], int],
    group_orders: dict[str, int],
    min_orders: int | None = None,
    min_share: float | None = None,
) -> set[tuple[int, str]]:
    """(partner_id, group) pairs whose order share marks them as register
    house accounts — group is a channel or a pos config name, whatever the
    caller tallied by. Pure; thresholds resolve at call time so tests can
    lower them."""
    min_orders = HOUSE_PARTNER_MIN_ORDERS if min_orders is None else min_orders
    min_share = HOUSE_PARTNER_MIN_SHARE if min_share is None else min_share
    out: set[tuple[int, str]] = set()
    for (partner_id, group), n in partner_orders.items():
        total = group_orders.get(group, 0)
        if n >= min_orders and total and n / total >= min_share:
            out.add((partner_id, group))
    return out


def monthly_house_partners(
    orders_bucket: dict[tuple[int, int, str], dict],
    min_monthly: int | None = None,
) -> set[tuple[int, str]]:
    """(partner_id, channel) pairs with implausible-for-a-person monthly
    volume in any single month bucket."""
    min_monthly = HOUSE_PARTNER_MONTHLY_ORDERS if min_monthly is None else min_monthly
    out: set[tuple[int, str]] = set()
    for (_y, _m, channel), ob in orders_bucket.items():
        for partner_id, n in ob["partner_orders"].items():
            if n >= min_monthly:
                out.add((partner_id, channel))
    return out


def sync_sales(db: Session, settings: Settings, conn: OdooConnection, state: SyncState) -> int:
    now = utcnow()
    today = now.date()
    extra = dict(state.extra or {})

    if not extra.get("backfill_done_at"):
        since = _first_of_month_minus(today, settings.sales_backfill_months - 1)
        window_label = f"backfill {settings.sales_backfill_months} months"
    elif extra.get("prev_month_synced_on") != today.isoformat():
        since = _first_of_month_minus(today, 1)  # previous month, once a day
        window_label = "incremental (current + previous month)"
    else:
        since = today.replace(day=1)  # hourly: current month only
        window_label = "incremental (current month)"

    since_str = f"{since.isoformat()} 00:00:00"

    id_by_odoo_pid = {
        odoo_id: pid
        for pid, odoo_id in db.execute(
            select(Product.id, Product.odoo_product_id).where(Product.odoo_product_id.is_not(None))
        )
    }
    centers_by_norm = {
        _normalize(name): cid
        for cid, name in db.execute(select(Center.id, Center.name))
    }
    aliases = _channel_aliases(db)

    # bucket[(product_id, year, month, channel)] = [units, amount]
    bucket: dict[tuple[int, int, int, str], list[float]] = {}
    # daily[(product_id, day, channel)] = [units, amount] — recent window only
    # (restock lists need yesterday; SalesMonthly keeps the long history).
    # Days are the UTC date of date_order, consistent with the monthly buckets.
    daily: dict[tuple[int, date, str], list[float]] = {}
    # center_bucket[(config_name, year, month)] = [units, amount] — city
    # centers only, config-level (feeds the dashboard's centers panel)
    center_bucket: dict[tuple[str, int, int], list[float]] = {}
    # orders_bucket[(year, month, channel)] — header-level order facts;
    # partner_orders maps partner_id -> order count inside the bucket
    orders_bucket: dict[tuple[int, int, str], dict] = {}
    # (partner_id, channel) -> earliest order date seen THIS window
    partner_min: dict[tuple[int, str], date] = {}
    # window-level tallies for house-partner (register default) detection —
    # by channel AND by pos config (a low-volume register's default never
    # dominates its aggregated channel, but it dominates its own register)
    window_partner_orders: dict[tuple[int, str], int] = {}
    window_channel_orders: dict[str, int] = {}
    window_config_partner_orders: dict[tuple[int, str], int] = {}
    window_config_orders: dict[str, int] = {}
    daily_floor = today - timedelta(days=settings.sales_daily_retention_days)
    any_source = False
    config_channels: dict[str, str] = {}  # config name -> channel, for admin visibility
    for line_model, qty_field, amount_field, order_model, states, source_kind in SOURCES:
        if not _model_exists(conn, line_model):
            continue
        any_source = True
        lines = conn.search_read(
            line_model,
            [["order_id.date_order", ">=", since_str], ["order_id.state", "in", list(states)]],
            ["product_id", "order_id", qty_field, amount_field],
        )
        if not lines:
            continue
        order_ids = sorted(
            {ln["order_id"][0] for ln in lines if isinstance(ln.get("order_id"), list)}
        )
        order_fields = ["date_order", "partner_id", "amount_total"]
        if source_kind == "pos":
            order_fields.append("config_id")
        # per-order header: parsed date + channel + config + partner + total
        dates: dict[int, str] = {}
        channels: dict[int, str] = {}
        config_by_order: dict[int, str] = {}
        for i in range(0, len(order_ids), 500):
            for o in conn.search_read(
                order_model, [["id", "in", order_ids[i : i + 500]]], order_fields
            ):
                d = str(o.get("date_order") or "")
                dates[o["id"]] = d
                if source_kind == "pos":
                    cfg = o.get("config_id")
                    config_name = str(cfg[1]) if isinstance(cfg, list) and len(cfg) == 2 else ""
                    channel = classify_pos_config(config_name, set(centers_by_norm), aliases)
                    if config_name:
                        config_channels[config_name] = channel
                    config_by_order[o["id"]] = config_name
                else:
                    channel = SalesChannel.ONLINE.value
                channels[o["id"]] = channel
                if len(d) < 7 or not d[:4].isdigit():
                    continue
                year, month = int(d[:4]), int(d[5:7])
                ob = orders_bucket.setdefault(
                    (year, month, channel),
                    {"orders": 0, "amount": 0.0, "partner_orders": {}},
                )
                ob["orders"] += 1
                ob["amount"] += float(o.get("amount_total") or 0.0)
                window_channel_orders[channel] = window_channel_orders.get(channel, 0) + 1
                config_name = config_by_order.get(o["id"], "")
                if config_name:
                    window_config_orders[config_name] = (
                        window_config_orders.get(config_name, 0) + 1
                    )
                partner_field = o.get("partner_id")
                partner_id = partner_field[0] if isinstance(partner_field, list) else None
                if isinstance(partner_id, int):
                    po = ob["partner_orders"]
                    po[partner_id] = po.get(partner_id, 0) + 1
                    pkey = (partner_id, channel)
                    window_partner_orders[pkey] = window_partner_orders.get(pkey, 0) + 1
                    if config_name:
                        cfg_key = (partner_id, config_name)
                        window_config_partner_orders[cfg_key] = (
                            window_config_partner_orders.get(cfg_key, 0) + 1
                        )
                    order_day = _order_date(d)
                    if order_day is not None:
                        prev = partner_min.get(pkey)
                        if prev is None or order_day < prev:
                            partner_min[pkey] = order_day
        for ln in lines:
            pid_field = ln.get("product_id")
            odoo_pid = pid_field[0] if isinstance(pid_field, list) else pid_field
            if not isinstance(odoo_pid, int):
                continue
            product_id = id_by_odoo_pid.get(odoo_pid)
            if product_id is None:
                continue
            order_field = ln.get("order_id")
            oid = order_field[0] if isinstance(order_field, list) else order_field
            if not isinstance(oid, int):
                continue
            d = dates.get(oid, "")
            if len(d) < 7 or not d[:4].isdigit():
                continue
            year, month = int(d[:4]), int(d[5:7])
            qty = float(ln.get(qty_field) or 0.0)
            amount = float(ln.get(amount_field) or 0.0)
            channel = channels.get(oid, SalesChannel.CAMPUS_OTHER.value)
            if channel == SalesChannel.CITY_CENTER.value:
                ckey = (config_by_order.get(oid, ""), year, month)
                crow = center_bucket.setdefault(ckey, [0.0, 0.0])
                crow[0] += qty
                crow[1] += amount
            key = (product_id, year, month, channel)
            row = bucket.setdefault(key, [0.0, 0.0])
            row[0] += qty
            row[1] += amount
            if len(d) >= 10 and d[8:10].isdigit():
                try:
                    day = date(year, month, int(d[8:10]))
                except ValueError:
                    continue
                if day >= daily_floor:
                    dkey = (product_id, day, channel)
                    drow = daily.setdefault(dkey, [0.0, 0.0])
                    drow[0] += qty
                    drow[1] += amount

    if not any_source:
        raise RuntimeError(
            "Neither pos.order.line nor sale.order.line is available — no sales source."
        )

    # Replace only the synced window (transactional; runner commits/rolls back).
    db.execute(
        delete(SalesMonthly).where(
            or_(
                SalesMonthly.year > since.year,
                and_(SalesMonthly.year == since.year, SalesMonthly.month >= since.month),
            )
        )
    )
    for (product_id, year, month, channel), (units, amount) in bucket.items():
        db.add(
            SalesMonthly(
                product_id=product_id,
                year=year,
                month=month,
                channel=channel,
                units=round(units, 3),
                amount=round(amount, 2),
                synced_at=now,
            )
        )

    # Center rollup: same window-replace semantics as the monthly buckets.
    db.execute(
        delete(SalesCenterMonthly).where(
            or_(
                SalesCenterMonthly.year > since.year,
                and_(
                    SalesCenterMonthly.year == since.year,
                    SalesCenterMonthly.month >= since.month,
                ),
            )
        )
    )
    for (config_name, year, month), (units, amount) in center_bucket.items():
        db.add(
            SalesCenterMonthly(
                config_name=config_name,
                center_id=centers_by_norm.get(_normalize(config_name)),
                year=year,
                month=month,
                units=round(units, 3),
                amount=round(amount, 2),
                synced_at=now,
            )
        )

    # House partners (register defaults): freshly detected in this window,
    # plus the ones remembered from bigger windows — hourly slices are too
    # small to re-detect on the 1st of a month.
    remembered = {
        (int(pid), str(ch))
        for pid, ch in (extra.get("house_partners") or [])
        if isinstance(pid, int | float) and str(ch)
    }
    house = remembered | detect_house_partners(window_partner_orders, window_channel_orders)
    # per-register pass: a config's dominant partner is a house account on
    # that config's CHANNEL (config_channels maps register → channel)
    for pid, cfg in detect_house_partners(window_config_partner_orders, window_config_orders):
        house.add((pid, config_channels.get(cfg, SalesChannel.CAMPUS_OTHER.value)))
    # volume pass: sometimes-attached register defaults dodge share checks
    house |= monthly_house_partners(orders_bucket)
    if house:
        # keep the first-seen memory people-only — the period-exact "new
        # customers" query on the dashboard counts these rows directly
        for pid, ch in house:
            db.execute(
                delete(CustomerFirstSeen).where(
                    CustomerFirstSeen.partner_id == pid, CustomerFirstSeen.channel == ch
                )
            )
        partner_min = {k: v for k, v in partner_min.items() if k not in house}

    # Order-header facts: remember each partner's earliest order (append-only
    # min — a full rebuild replays history and converges), then replace the
    # window's monthly order rows with counts + the new/returning split.
    if partner_min:
        existing = {
            (seen.partner_id, seen.channel): seen
            for seen in db.scalars(
                select(CustomerFirstSeen).where(
                    CustomerFirstSeen.partner_id.in_({pid for pid, _ in partner_min})
                )
            )
        }
        for (partner_id, channel), first in partner_min.items():
            seen = existing.get((partner_id, channel))
            if seen is None:
                db.add(
                    CustomerFirstSeen(
                        partner_id=partner_id, channel=channel, first_order_on=first
                    )
                )
            elif first < seen.first_order_on:
                seen.first_order_on = first
        db.flush()

    db.execute(
        delete(SalesOrdersMonthly).where(
            or_(
                SalesOrdersMonthly.year > since.year,
                and_(
                    SalesOrdersMonthly.year == since.year,
                    SalesOrdersMonthly.month >= since.month,
                ),
            )
        )
    )
    first_seen_by_key: dict[tuple[int, str], date] = {}
    all_partner_ids = {pid for ob in orders_bucket.values() for pid in ob["partner_orders"]}
    if all_partner_ids:
        for seen in db.scalars(
            select(CustomerFirstSeen).where(CustomerFirstSeen.partner_id.in_(all_partner_ids))
        ):
            first_seen_by_key[(seen.partner_id, seen.channel)] = seen.first_order_on
    for (year, month, channel), ob in orders_bucket.items():
        # customer metrics count PEOPLE: house accounts drop out here (their
        # orders still count as orders — walk-ins are sales, not customers)
        kept = {
            pid: n
            for pid, n in ob["partner_orders"].items()
            if (pid, channel) not in house
        }
        new_customers = sum(
            1
            for pid in kept
            if (fs := first_seen_by_key.get((pid, channel))) is not None
            and (fs.year, fs.month) == (year, month)
        )
        db.add(
            SalesOrdersMonthly(
                year=year,
                month=month,
                channel=channel,
                orders=ob["orders"],
                amount=round(ob["amount"], 2),
                orders_with_customer=sum(kept.values()),
                distinct_customers=len(kept),
                new_customers=new_customers,
                returning_customers=len(kept) - new_customers,
                synced_at=now,
            )
        )

    # Daily buckets: replace the synced window (clamped to retention) and
    # prune rows that have aged out.
    daily_since = max(since, daily_floor)
    db.execute(delete(SalesDaily).where(SalesDaily.day >= daily_since))
    db.execute(delete(SalesDaily).where(SalesDaily.day < daily_floor))
    for (product_id, day, channel), (units, amount) in daily.items():
        db.add(
            SalesDaily(
                product_id=product_id,
                day=day,
                channel=channel,
                units=round(units, 3),
                amount=round(amount, 2),
                synced_at=now,
            )
        )

    extra.setdefault("backfill_done_at", now.isoformat())
    extra["prev_month_synced_on"] = today.isoformat() if since < today.replace(day=1) else extra.get("prev_month_synced_on")
    extra["last_window"] = window_label
    extra["pos_config_channels"] = dict(sorted(config_channels.items())[:80])
    extra["house_partners"] = [list(pair) for pair in sorted(house)][:40]
    state.extra = extra
    return len(bucket)
