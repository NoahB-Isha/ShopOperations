"""Reasonability assessment for center orders — rules first, LLM polish second.

The rules layer is pure and always runs: it compares the order against the
center's own order history (the app's data — Odoo has no per-center sales)
and current stock at the fulfillment source. The optional LLM pass only
rewrites the order-level summary in friendlier language and may raise (never
hide) concerns — rule findings are the floor, and everything here is
ADVISORY: it never blocks an order.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import (
    Center,
    CenterOrder,
    CenterOrderLine,
    CenterOrderStatus,
    Product,
    StockLevel,
    utcnow,
)

log = logging.getLogger("reasonability")

LEVELS = ("ok", "info", "warn")
# History that reflects real demand: orders a coordinator let through.
HISTORY_STATUSES = (CenterOrderStatus.APPROVED.value, CenterOrderStatus.SHIPPED.value)


def _worst(*levels: str) -> str:
    return max(levels, key=lambda lv: LEVELS.index(lv) if lv in LEVELS else 0, default="ok")


@dataclass
class Badge:
    code: str
    level: str  # info | warn
    text: str

    def as_dict(self) -> dict:
        return {"code": self.code, "level": self.level, "text": self.text}


@dataclass
class Assessment:
    level: str = "ok"
    summary: str = ""
    source: str = "rules"  # rules | rules+llm
    order_badges: list[Badge] = field(default_factory=list)
    lines: dict[int, list[Badge]] = field(default_factory=dict)  # product_id -> badges

    def line_level(self, product_id: int) -> str:
        return _worst(*(b.level for b in self.lines.get(product_id, [])))

    def as_dict(self) -> dict:
        return {
            "level": self.level,
            "summary": self.summary,
            "source": self.source,
            "order_badges": [b.as_dict() for b in self.order_badges],
            "lines": {
                str(pid): [b.as_dict() for b in badges] for pid, badges in self.lines.items()
            },
            "computed_at": utcnow().isoformat(),
        }


# ------------------------------------------------------------- center history
@dataclass
class CenterHistory:
    order_count: int = 0
    avg_total_units: float = 0.0
    # product_id -> (times ordered, average qty per order)
    per_product: dict[int, tuple[int, float]] = field(default_factory=dict)
    # product_id -> days since the center last ordered it
    days_since_last: dict[int, float] = field(default_factory=dict)


def load_center_history(db: Session, center_id: int, window_days: int) -> CenterHistory:
    since = utcnow() - timedelta(days=window_days)
    rows = db.execute(
        select(CenterOrder, CenterOrderLine)
        .join(CenterOrderLine, CenterOrderLine.order_id == CenterOrder.id)
        .where(
            CenterOrder.center_id == center_id,
            CenterOrder.status.in_(HISTORY_STATUSES),
            CenterOrder.created_at >= since,
        )
    ).all()
    hist = CenterHistory()
    if not rows:
        return hist
    now = utcnow()
    totals: dict[int, float] = {}
    qtys: dict[int, list[float]] = {}
    for order, line in rows:
        totals[order.id] = totals.get(order.id, 0.0) + line.qty_final
        qtys.setdefault(line.product_id, []).append(line.qty_final)
        created = order.created_at
        if created is not None:
            if created.tzinfo is None:
                created = created.replace(tzinfo=now.tzinfo)
            age_days = (now - created).total_seconds() / 86400
            prev = hist.days_since_last.get(line.product_id)
            if prev is None or age_days < prev:
                hist.days_since_last[line.product_id] = age_days
    hist.order_count = len(totals)
    hist.avg_total_units = sum(totals.values()) / len(totals)
    hist.per_product = {
        pid: (len(qs), sum(qs) / len(qs)) for pid, qs in qtys.items()
    }
    return hist


# ------------------------------------------------------------------- rules
@dataclass
class LineFacts:
    """Everything the rules need about one order line — plain data, no ORM."""

    product_id: int
    name: str
    qty: float
    on_hand: float | None  # at the fulfillment source; None = untracked
    case_size: int = 1


def _x(ratio: float) -> str:
    """'3.3×' but never '12.0×' — chips stay terse."""
    return f"{ratio:.1f}".removesuffix(".0") + "×"


def assess_rules(
    lines: list[LineFacts],
    history: CenterHistory,
    settings: Settings,
) -> Assessment:
    """Badge texts are chip-length ON PURPOSE — they sit under the product
    name on a phone, so they never repeat it and never explain twice what the
    availability badge already shows."""
    a = Assessment()
    total_units = sum(line.qty for line in lines)

    for line in lines:
        badges: list[Badge] = []
        prior = history.per_product.get(line.product_id)

        if prior and prior[0] >= 2 and prior[1] > 0:
            ratio = line.qty / prior[1]
            if ratio >= settings.reasonability_spike_factor:
                badges.append(Badge("volume_spike", "warn", f"{_x(ratio)} usual volume"))
        elif prior is None and history.order_count >= 1:
            badges.append(Badge("first_time_item", "info", "first time ordering this"))

        if line.on_hand is not None:
            if line.qty > line.on_hand:
                badges.append(
                    Badge("exceeds_stock", "warn", f"only {line.on_hand:g} in stock")
                )
            elif 0 < line.on_hand <= settings.catalog_low_stock_threshold:
                badges.append(
                    Badge(
                        "low_stock_data",
                        "info",
                        f"low count ({line.on_hand:g}) — verify",
                    )
                )

        if line.case_size > 1 and line.qty % line.case_size:
            badges.append(
                Badge("case_mismatch", "info", f"not a full case ({line.case_size}/case)")
            )

        days_ago = history.days_since_last.get(line.product_id)
        if days_ago is not None and days_ago <= 7:
            badges.append(
                Badge("repeat_recent", "info", f"ordered {max(1, round(days_ago))}d ago")
            )

        if badges:
            a.lines[line.product_id] = badges

    # ---- order level
    if history.order_count == 0:
        a.order_badges.append(Badge("first_order", "info", "first order — no history yet"))
    elif history.order_count >= 2 and history.avg_total_units > 0:
        ratio = total_units / history.avg_total_units
        if ratio >= settings.reasonability_spike_factor:
            a.order_badges.append(
                Badge("huge_order", "warn", f"{_x(ratio)} usual order size")
            )
    if total_units >= settings.reasonability_huge_order_units:
        a.order_badges.append(
            Badge("very_large_order", "warn", f"{total_units:g} units — double-check")
        )

    line_levels = [b.level for badges in a.lines.values() for b in badges]
    a.level = _worst(*(b.level for b in a.order_badges), *line_levels)
    a.summary = _template_summary(a)
    return a


def _template_summary(a: Assessment) -> str:
    warns = sum(
        1 for badges in list(a.lines.values()) + [a.order_badges] for b in badges if b.level == "warn"
    )
    infos = sum(
        1 for badges in list(a.lines.values()) + [a.order_badges] for b in badges if b.level == "info"
    )
    if warns == 0 and infos == 0:
        return "Looks reasonable against this center's history and current stock."
    parts = []
    if warns:
        parts.append(f"{warns} item(s) worth a second look")
    if infos:
        parts.append(f"{infos} minor note(s)")
    return " and ".join(parts) + " — details on each line."


# --------------------------------------------------------------- LLM polish
_LLM_SCHEMA = {
    "type": "object",
    "properties": {
        "level": {"type": "string", "enum": ["ok", "info", "warn"]},
        "summary": {"type": "string"},
    },
    "required": ["level", "summary"],
    "additionalProperties": False,
}

_LLM_SYSTEM = (
    "You review restock orders that volunteer-run pop-up shops place with a warehouse. "
    "You are given the order plus rule-engine findings and history stats as JSON. "
    "Write ONE friendly, plain-English sentence (max 160 characters) summarising how "
    "reasonable the order looks, mentioning at most the two most important concerns. "
    "Never invent numbers that are not in the data. Pick level 'warn' only for genuine "
    "concerns, 'info' for minor notes, 'ok' otherwise. This is advisory — a human decides."
)


def polish_with_llm(settings: Settings, a: Assessment, context: dict) -> Assessment:
    """Rewrite the order-level summary with the Anthropic API. Best-effort:
    any failure leaves the rules result untouched. The LLM may raise the
    level but never lower it below what the rules found."""
    if not settings.anthropic_api_key.get_secret_value():
        return a
    try:
        import anthropic

        client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key.get_secret_value(),
            timeout=settings.reasonability_llm_timeout_seconds,
            max_retries=0,  # this is a live request path; degrade fast instead
        )
        payload = {"assessment": a.as_dict(), "order": context}
        response = client.messages.create(
            model=settings.reasonability_llm_model,
            max_tokens=300,
            system=_LLM_SYSTEM,
            output_config={"format": {"type": "json_schema", "schema": _LLM_SCHEMA}},
            messages=[{"role": "user", "content": json.dumps(payload, default=str)}],
        )
        if response.stop_reason == "refusal":
            return a
        text = next((b.text for b in response.content if b.type == "text"), "")
        data = json.loads(text)
        summary = str(data.get("summary") or "").strip()
        level = str(data.get("level") or "")
        if summary:
            a.summary = summary[:200]
            a.level = _worst(a.level, level)  # LLM can escalate, never suppress
            a.source = "rules+llm"
    except Exception as e:  # noqa: BLE001 — advisory path must never break ordering
        log.warning("reasonability LLM polish skipped: %s", e)
    return a


# ------------------------------------------------------------------ facade
def line_facts_for(
    db: Session, lines: list[tuple[Product, float]], source_key: str
) -> list[LineFacts]:
    ids = {p.id for p, _ in lines}
    stock = {
        pid: float(qty)
        for pid, qty in db.execute(
            select(StockLevel.product_id, StockLevel.qty).where(
                StockLevel.product_id.in_(ids or {-1}),
                StockLevel.location_key == source_key,
            )
        )
    }
    return [
        LineFacts(
            product_id=p.id,
            name=p.name,
            qty=qty,
            on_hand=(
                stock.get(p.id, 0.0)
                if (p.is_stock_tracked and p.odoo_product_id)
                else None
            ),
            case_size=p.case_size or 1,
        )
        for p, qty in lines
    ]


def assess_order(
    db: Session,
    settings: Settings,
    center: Center,
    lines: list[tuple[Product, float]],
    source_key: str,
    use_llm: bool = True,
) -> Assessment:
    history = load_center_history(db, center.id, settings.reasonability_history_days)
    facts = line_facts_for(db, lines, source_key)
    a = assess_rules(facts, history, settings)
    if use_llm:
        context = {
            "center": center.name,
            "prior_orders_in_window": history.order_count,
            "avg_order_units": round(history.avg_total_units, 1),
            "lines": [
                {"name": f.name, "qty": f.qty, "on_hand": f.on_hand} for f in facts
            ],
        }
        a = polish_with_llm(settings, a, context)
    return a
