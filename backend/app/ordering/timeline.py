"""The order-action timeline — append-only events and how confirmed events
move order state.

Rules of the road (project brief, safety-critical):
  * Events are ONLY inserted, never edited or deleted. State (line
    quantities, statuses, legs) is derived by applying confirmed events in
    order; `origin_*` on each line keeps the starting point forever.
  * Parsed email proposals NEVER touch state directly — a human confirms
    (possibly editing the payload) and the confirmation applies the event.

Event payload shapes (kind -> payload):
  qty_change     {"sea": {"from": 500, "to": 200}, "air": {...}}   either key optional
  substitution   {"substitute_sku": "...", "note": "..."}
  discontinued   {}                                        (zeroes the line)
  method_change  {"from": "sea", "to": "air", "qty": 120}
  split          {"label": "Q3 ADD AIR", "method": "air", "eta": "2026-11-01",
                  "lines": {"SKU": qty, ...}}              (creates a leg)
  availability   {"eta": "2026-08-15", "note": "..."}      (informational)
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from ..models import (
    LegStatus,
    LineStatus,
    OrderEmailMessage,
    OrderEventKind,
    OrderLeg,
    PurchaseOrder,
    PurchaseOrderEvent,
    PurchaseOrderLine,
    User,
)


class EventApplyError(ValueError):
    """The event payload can't be applied to this order (bad line, bad shape)."""


def add_event(
    db: Session,
    order: PurchaseOrder,
    kind: OrderEventKind | str,
    *,
    line: PurchaseOrderLine | None = None,
    status: str = "",
    note: str = "",
    payload: dict | None = None,
    actor: User | None = None,
    actor_label: str = "",
    source_message: OrderEmailMessage | None = None,
    quote: str = "",
    confidence: float | None = None,
) -> PurchaseOrderEvent:
    """Append one timeline event (no commit — caller owns the transaction)."""
    event = PurchaseOrderEvent(
        order_id=order.id,
        line_id=line.id if line else None,
        kind=kind.value if isinstance(kind, OrderEventKind) else kind,
        status=status,
        note=note,
        payload=payload or {},
        actor_user_id=actor.id if actor else None,
        actor_label=actor_label
        or ((actor.display_name or actor.email or "user") if actor else "system"),
        source_message_id=source_message.id if source_message else None,
        source_quote=quote,
        confidence=confidence,
    )
    db.add(event)
    db.flush()  # give it an id so proposals can link applied_event_id
    return event


def apply_event(
    db: Session,
    order: PurchaseOrder,
    kind: str,
    payload: dict,
    line: PurchaseOrderLine | None,
) -> str:
    """Apply a CONFIRMED event's structural effect. Returns a human-readable
    summary of what changed (for the event note). Informational kinds
    (availability, note, email…) change nothing and return ''."""
    if kind == OrderEventKind.QTY_CHANGE.value:
        return _apply_qty_change(line, payload)
    if kind == OrderEventKind.DISCONTINUED.value:
        return _apply_discontinued(line)
    if kind == OrderEventKind.SUBSTITUTION.value:
        return _apply_substitution(line, payload)
    if kind == OrderEventKind.METHOD_CHANGE.value:
        return _apply_method_change(line, payload)
    if kind == OrderEventKind.SPLIT.value:
        return _apply_split(db, order, payload)
    return ""


def _require_line(line: PurchaseOrderLine | None) -> PurchaseOrderLine:
    if line is None:
        raise EventApplyError("this event kind needs a matching order line")
    return line


def _apply_qty_change(line: PurchaseOrderLine | None, payload: dict) -> str:
    line = _require_line(line)
    changes = []
    for leg_key, attr in (("sea", "final_sea_qty"), ("air", "final_air_qty")):
        change = payload.get(leg_key)
        if not isinstance(change, dict) or "to" not in change:
            continue
        try:
            to_qty = int(change["to"])
        except (TypeError, ValueError) as e:
            raise EventApplyError(f"{leg_key} quantity must be a number") from e
        if to_qty < 0:
            raise EventApplyError(f"{leg_key} quantity can't be negative")
        old = getattr(line, attr)
        if to_qty != old:
            change["from"] = old  # record what it actually moved from
            setattr(line, attr, to_qty)
            changes.append(f"{leg_key} {old} → {to_qty}")
    if not changes:
        raise EventApplyError('qty_change payload needs {"sea"|"air": {"to": n}}')
    return "; ".join(changes)


def _apply_discontinued(line: PurchaseOrderLine | None) -> str:
    line = _require_line(line)
    was = f"sea {line.final_sea_qty} / air {line.final_air_qty}"
    line.line_status = LineStatus.DISCONTINUED.value
    line.final_sea_qty = 0
    line.final_air_qty = 0
    return f"discontinued (was {was})"


def _apply_substitution(line: PurchaseOrderLine | None, payload: dict) -> str:
    line = _require_line(line)
    substitute = str(payload.get("substitute_sku") or "").strip()
    if not substitute:
        raise EventApplyError('substitution payload needs {"substitute_sku": "..."}')
    line.line_status = LineStatus.SUBSTITUTED.value
    line.substitute_sku = substitute
    return f"substituted with {substitute}"


def _apply_method_change(line: PurchaseOrderLine | None, payload: dict) -> str:
    line = _require_line(line)
    src = str(payload.get("from") or "sea")
    dst = str(payload.get("to") or "air")
    if {src, dst} != {"sea", "air"}:
        raise EventApplyError("method_change moves between sea and air")
    src_attr = f"final_{src}_qty"
    dst_attr = f"final_{dst}_qty"
    available = getattr(line, src_attr)
    qty = payload.get("qty")
    try:
        qty = int(qty) if qty is not None else available
    except (TypeError, ValueError) as e:
        raise EventApplyError("qty must be a number") from e
    if qty <= 0 or qty > available:
        raise EventApplyError(f"can move 1..{available} units from {src}")
    setattr(line, src_attr, available - qty)
    setattr(line, dst_attr, getattr(line, dst_attr) + qty)
    return f"{qty} units {src} → {dst}"


def _apply_split(db: Session, order: PurchaseOrder, payload: dict) -> str:
    label = str(payload.get("label") or "").strip()
    if not label:
        raise EventApplyError('split payload needs {"label": "Q3 ADD AIR", ...}')
    method = str(payload.get("method") or "sea")
    if method not in ("sea", "air"):
        raise EventApplyError("split method must be sea or air")
    eta = None
    if payload.get("eta"):
        try:
            eta = date.fromisoformat(str(payload["eta"])[:10])
        except ValueError as e:
            raise EventApplyError("split eta must be an ISO date") from e
    lines = payload.get("lines") or {}
    if not isinstance(lines, dict):
        raise EventApplyError("split lines must be {sku: qty}")
    db.add(
        OrderLeg(
            order_id=order.id,
            label=label,
            method=method,
            status=LegStatus.PLANNED.value,
            eta=eta,
            line_quantities={str(k): float(v) for k, v in lines.items()},
        )
    )
    return f"new {method} leg “{label}”" + (f", ETA {eta}" if eta else "")


def find_line(order: PurchaseOrder, *, line_id: int | None = None,
              sku: str | None = None) -> PurchaseOrderLine | None:
    for line in order.lines:
        if line_id is not None and line.id == line_id:
            return line
        if sku and line.global_sku.lower() == sku.strip().lower():
            return line
    return None
