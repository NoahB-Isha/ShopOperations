"""The delivery form — the warehouse tells the app what they just sent.

Noah's rework (2026-08-17): the warehouse works in Odoo and always will.
They pull a request however suits them (split it, part-ship it, build their
own pickings), pile everything into III/Staging2, and when there's enough
for a pallet they make ONE staging2 → floor-staging transfer in Odoo. No
amount of polling can tell the app which requests that pallet is carrying —
so a human answers three questions instead:

  1. which transfer did you just send?      -> candidate_pickings()
  2. which requests are in it?              -> suggest_requests()
  3. why do these quantities differ?        -> discrepancy_review()

Submitting (declare()) is what links the pallet to the requests, freezes its
contents, allocates sent quantities back onto the request lines, and records
the warehouse's reasons in their own words. When the pallet is validated in
Odoo the linked requests close as DONE against it (land()), and ONE count
transfer is prepared for the whole pallet — the floor counts a pallet once,
not once per request it happened to contain.

Everything here is honest about what it doesn't know: an undeclared delivery
closes nobody's request, and a product nobody requested is "extra", not a
discrepancy.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import Settings
from ..models import (
    DiscrepancyReason,
    OdooLocation,
    OdooWriteOutcome,
    PalletDiscrepancy,
    PalletRequestLink,
    PalletTransfer,
    Product,
    TransferEventKind,
    TransferRequest,
    TransferRequestStatus,
    is_due,
    utcnow,
)
from ..odoo.connection import get_connection
from ..odoo.errors import OdooError, OdooWriteError
from ..odoo.operations import new_reference
from ..odoo.protocol import safe_fields
from ..odoo.writer import OdooWriter
from .service import _event, _move_quantities, barcode_url

log = logging.getLogger("transfers.delivery")

# What the warehouse can say about a gap, in the words Noah wrote them in.
REASON_LABELS: dict[str, str] = {
    DiscrepancyReason.NO_STOCK.value: "We don't have enough stock",
    DiscrepancyReason.FULL_CASE.value: "Sending a full case",
    DiscrepancyReason.ANOTHER_TRANSFER.value: "I'll include it in another transfer",
    DiscrepancyReason.OTHER.value: "Other",
}

# a request can still be put on a delivery in these states (done/cancelled
# ones are finished business)
LINKABLE_STATUSES = (
    TransferRequestStatus.REQUESTED.value,
    TransferRequestStatus.WORKING.value,
    TransferRequestStatus.SENT.value,
)

# Odoo states worth offering as "the transfer you just sent" — validated
# first, but a draft/ready one counts too: plenty of people fill the form
# while the pallet is still on the dock.
CANDIDATE_STATES = ("done", "assigned", "confirmed", "waiting", "draft")


class DeliveryError(ValueError):
    """Something the warehouse can fix, phrased for them (HTTP 422)."""


# --------------------------------------------------------------- read models
@dataclass
class CandidatePicking:
    odoo_picking_id: int
    name: str
    state: str
    date: str  # ISO-ish, whatever Odoo gave us; display only
    item_count: int
    total_units: float
    already_declared: bool
    declared_pallet_id: int | None = None
    from_staging2: bool = True  # False for a picking found by manual search


@dataclass
class RequestSuggestion:
    request_id: int
    display_name: str
    status: str
    created_by: str
    created_at: datetime
    line_count: int
    matched_items: int  # how many of its products are on this pallet
    total_requested: float
    reason: str  # why the app is suggesting it, in plain words
    suggested: bool  # there's real evidence — show it up front
    auto_select: bool  # evidence strong enough to tick the box


@dataclass
class DiscrepancyRow:
    product_id: int
    sku: str
    barcode: str
    name: str
    qty_requested: float
    qty_sent: float
    delta: float  # sent - requested; negative = short
    requested_by: list[str] = field(default_factory=list)  # request display names
    reasons: list[str] = field(default_factory=list)  # already-saved answers
    note: str = ""


@dataclass
class ExtraRow:
    """On the pallet, but nobody asked for it — a warehouse top-up, not a
    discrepancy. Shown for confirmation, never questioned."""

    product_id: int
    sku: str
    barcode: str
    name: str
    qty_sent: float


@dataclass
class DeliveryPreview:
    picking: CandidatePicking | None
    suggestions: list[RequestSuggestion]
    review: list[DiscrepancyRow]
    extras: list[ExtraRow]
    threshold: float
    note: str = ""


# ------------------------------------------------------------- pure helpers
def allocate_sent(
    lines: list[tuple[int, int, float]], sent_by_product: dict[int, float]
) -> dict[int, float]:
    """Split what's on the pallet across the request lines that asked for it.

    `lines` is [(line_id, product_id, qty_requested)] in the order the
    requests were placed; the return is line_id -> quantity sent.

    Oldest request first, each filled up to what it asked for, and any
    surplus lands on the LAST line that wanted the product (a pallet with
    more than was asked for is real stock arriving, and it has to be
    attributed somewhere). Pure, deterministic, and the rule is one sentence
    long — which matters, because the floor will ask why their request shows
    6 when they asked for 12."""
    remaining = {pid: float(qty) for pid, qty in sent_by_product.items()}
    out: dict[int, float] = {}
    last_line_for: dict[int, int] = {}
    for line_id, product_id, qty_requested in lines:
        available = remaining.get(product_id, 0.0)
        take = min(max(float(qty_requested), 0.0), max(available, 0.0))
        out[line_id] = round(take, 3)
        remaining[product_id] = round(available - take, 3)
        last_line_for[product_id] = line_id
    for product_id, left in remaining.items():
        surplus_line = last_line_for.get(product_id)
        if surplus_line is not None and left > 0:
            out[surplus_line] = round(out.get(surplus_line, 0.0) + left, 3)
    return out


def needs_reason(qty_requested: float, qty_sent: float, threshold: float) -> bool:
    """Is this gap big enough to ask about? Only for products somebody
    actually requested — an unrequested extra on the pallet is not a
    discrepancy, it's a top-up."""
    if qty_requested <= 0:
        return False
    return abs(float(qty_sent) - float(qty_requested)) > float(threshold)


def validate_reasons(reasons: list[str], note: str) -> list[str]:
    """Clean one row's answer. At least one reason is required; OTHER (or an
    empty pick) needs the note — that's the "required if none are selected"
    rule, enforced server-side so it holds for every client."""
    cleaned = [r for r in dict.fromkeys(reasons) if r in REASON_LABELS]
    if not cleaned:
        if not note.strip():
            raise DeliveryError("Pick a reason, or write one in.")
        cleaned = [DiscrepancyReason.OTHER.value]
    if DiscrepancyReason.OTHER.value in cleaned and not note.strip():
        raise DeliveryError("“Other” needs a short note saying what happened.")
    return cleaned


def reason_summary(reasons: list[str], note: str) -> str:
    """One human-readable phrase for a timeline entry."""
    labels = [REASON_LABELS[r] for r in reasons if r in REASON_LABELS]
    text = "; ".join(labels)
    if note.strip():
        text = f"{text} — {note.strip()}" if text else note.strip()
    return text


# ------------------------------------------------------------ Odoo readers
def _location(db: Session, key: str) -> OdooLocation | None:
    return db.scalar(select(OdooLocation).where(OdooLocation.key == key))


def _product_ids_by_odoo_id(db: Session) -> dict[int, int]:
    return {
        odoo_id: pid
        for pid, odoo_id in db.execute(
            select(Product.id, Product.odoo_product_id).where(
                Product.odoo_product_id.is_not(None)
            )
        )
    }


def picking_contents(db: Session, conn, odoo_picking_id: int) -> dict[int, float]:
    """app product_id -> quantity on one picking (done quantity where it has
    been validated, demand otherwise — `_move_quantities` already picks)."""
    by_odoo_pid = _move_quantities(conn, odoo_picking_id)
    ids = _product_ids_by_odoo_id(db)
    out: dict[int, float] = {}
    for odoo_pid, qty in by_odoo_pid.items():
        product_id = ids.get(odoo_pid)
        if product_id is None or qty <= 0:
            continue
        out[product_id] = round(out.get(product_id, 0.0) + float(qty), 3)
    return out


def picking_contents_bulk(
    db: Session, conn, odoo_picking_ids: list[int]
) -> dict[int, dict[int, float]]:
    """The same thing for a whole list, in ONE stock.move read.

    The candidate list wants item counts for a dozen pickings; asking per
    picking is a dozen round trips against a throttled client (~3 seconds of
    nothing, every time the form opens) for one query's worth of data."""
    if not odoo_picking_ids:
        return {}
    ids = _product_ids_by_odoo_id(db)
    fields = safe_fields(
        conn, "stock.move", ["picking_id", "product_id", "quantity", "product_uom_qty", "state"]
    )
    moves = conn.search_read(
        "stock.move", [["picking_id", "in", list(odoo_picking_ids)]], fields
    )
    out: dict[int, dict[int, float]] = {pid: {} for pid in odoo_picking_ids}
    for m in moves:
        if str(m.get("state") or "") == "cancel":
            continue  # a cancelled move delivered nothing
        picking = m.get("picking_id")
        picking_id = picking[0] if isinstance(picking, list) else picking
        bucket = out.get(picking_id if isinstance(picking_id, int) else -1)
        if bucket is None:
            continue
        pid_field = m.get("product_id")
        odoo_pid = pid_field[0] if isinstance(pid_field, list) else pid_field
        product_id = ids.get(odoo_pid if isinstance(odoo_pid, int) else -1)
        if product_id is None:
            continue
        qty = m.get("quantity")
        if qty in (None, False):
            qty = m.get("product_uom_qty") or 0.0
        if float(qty or 0) <= 0:
            continue
        bucket[product_id] = round(bucket.get(product_id, 0.0) + float(qty), 3)
    return out


def _declared_pallets_by_picking(db: Session) -> dict[int, PalletTransfer]:
    rows = db.scalars(
        select(PalletTransfer).where(PalletTransfer.odoo_picking_id.is_not(None))
    ).all()
    return {int(p.odoo_picking_id or 0): p for p in rows}


def candidate_pickings(
    db: Session, settings: Settings, search: str = ""
) -> tuple[list[CandidatePicking], str]:
    """Question 1: "which transfer are you sending?"

    Recent staging2 → floor-staging pickings, newest first. `search` is the
    "Don't see it?" escape hatch — it matches a picking name ANYWHERE in
    Odoo (any locations, any state), because the whole point of that button
    is that the transfer didn't come out of the usual place.

    Returns (candidates, note) — the note explains an empty list honestly
    rather than pretending nothing was sent."""
    staging2 = _location(db, "staging2")
    staging = _location(db, "staging")
    term = search.strip()
    if not term and (staging2 is None or staging is None):
        return [], (
            "III/Staging2 or floor staging isn't mapped yet — run a stock sync, then "
            "search for the transfer by name."
        )
    try:
        conn = get_connection(settings, read_only=True)
        if term:
            domain: list = [["name", "ilike", term]]
        else:
            domain = [
                ["location_id", "child_of", staging2.odoo_id],  # type: ignore[union-attr]
                ["location_dest_id", "child_of", staging.odoo_id],  # type: ignore[union-attr]
                ["state", "in", list(CANDIDATE_STATES)],
            ]
        rows = conn.search_read(
            "stock.picking",
            domain,
            ["name", "state", "scheduled_date", "date_done", "location_id", "location_dest_id"],
            order="id desc",
        )
    except OdooError as e:
        return [], f"Odoo is unreachable right now ({e}) — try again in a minute."

    rows = rows[: max(1, settings.delivery_candidate_limit)]
    declared = _declared_pallets_by_picking(db)
    try:
        bulk = picking_contents_bulk(db, conn, [r["id"] for r in rows])
    except OdooError:
        bulk = {}  # candidates with no readable lines are still selectable
    out: list[CandidatePicking] = []
    for row in rows:
        contents = bulk.get(row["id"], {})
        pallet = declared.get(row["id"])
        source = row.get("location_id")
        source_id = source[0] if isinstance(source, list) else source
        out.append(
            CandidatePicking(
                odoo_picking_id=row["id"],
                name=str(row.get("name") or f"#{row['id']}"),
                state=str(row.get("state") or ""),
                date=str(row.get("date_done") or row.get("scheduled_date") or ""),
                item_count=len(contents),
                total_units=round(sum(contents.values()), 3),
                already_declared=pallet is not None and pallet.is_declared,
                declared_pallet_id=pallet.id if pallet is not None else None,
                from_staging2=staging2 is not None and source_id == staging2.odoo_id,
            )
        )
    note = ""
    if not out:
        note = (
            f"No transfer matching “{term}” in Odoo."
            if term
            else "Nothing has gone from III/Staging2 to floor staging recently."
        )
    return out, note


# ------------------------------------------------------- question 2: which
def _linkable_requests(db: Session, exclude_pallet_id: int | None) -> list[TransferRequest]:
    """Open requests not already riding a DIFFERENT delivery."""
    taken = {
        request_id
        for request_id, pallet_id in db.execute(
            select(PalletRequestLink.request_id, PalletRequestLink.pallet_id)
        )
        if pallet_id != exclude_pallet_id
    }
    requests = db.scalars(
        select(TransferRequest)
        .options(selectinload(TransferRequest.lines))
        .where(TransferRequest.status.in_(LINKABLE_STATUSES))
        .order_by(TransferRequest.id)
        .execution_options(populate_existing=True)
    ).all()
    return [r for r in requests if r.id not in taken]


def suggest_requests(
    db: Session,
    contents: dict[int, float],
    exclude_pallet_id: int | None = None,
    already_linked: set[int] | None = None,
) -> list[RequestSuggestion]:
    """Question 2: "which transfers are included in this bulk transfer?"

    Two kinds of evidence, and the app says which one it's using:

      * the request is STAGED — its own Odoo picking was validated, so its
        stock is sitting in Staging2 waiting for exactly this pallet;
      * some of its products are physically on the pallet.

    Staged AND on the pallet is strong enough to tick the box for them.
    Everything else is offered unticked, because a guess the warehouse has
    to un-tick is worse than one they have to tick.

    EVERY open request comes back, evidence or not — the ones without any are
    flagged `suggested=False` so the form can tuck them behind "add another
    transfer". That's the "button to add more" without a second endpoint, and
    it means one place decides what's linkable."""
    from ..models import User  # local: keeps this module's import block small

    names = {
        u.id: (u.display_name or u.email or f"user {u.id}")
        for u in db.scalars(select(User))
    }
    out: list[RequestSuggestion] = []
    for req in _linkable_requests(db, exclude_pallet_id):
        matched = sum(1 for line in req.lines if line.product_id in contents)
        staged = req.status == TransferRequestStatus.SENT.value
        linked = req.id in (already_linked or set())
        if not (staged or matched or linked):
            reason = "no sign of it on this pallet — add it if you know better"
        elif staged and matched:
            reason = f"staged in Staging 2 · {matched} of its items are on this transfer"
        elif staged:
            reason = "staged in Staging 2 — but none of its items are on this transfer"
        elif matched:
            reason = (
                f"{matched} of its items are on this transfer "
                f"(the app hasn't seen it validated yet)"
            )
        else:
            reason = "you added it"
        out.append(
            RequestSuggestion(
                request_id=req.id,
                display_name=req.display_name,
                status=req.status,
                created_by=names.get(req.created_by_id or 0, "unknown"),
                created_at=req.created_at,
                line_count=len(req.lines),
                matched_items=matched,
                total_requested=round(sum(line.qty_requested for line in req.lines), 3),
                reason=reason,
                suggested=bool(staged or matched or linked),
                auto_select=linked or (staged and matched > 0),
            )
        )
    out.sort(
        key=lambda s: (not s.auto_select, not s.suggested, -s.matched_items, s.request_id)
    )
    return out


# --------------------------------------------------- question 3: why differ
def discrepancy_review(
    db: Session,
    settings: Settings,
    contents: dict[int, float],
    request_ids: list[int],
    saved: dict[int, PalletDiscrepancy] | None = None,
) -> tuple[list[DiscrepancyRow], list[ExtraRow]]:
    """Question 3: item-by-item, what differs by more than the threshold.

    Per PRODUCT, summed across every selected request — a pallet carries one
    pile of each item and the warehouse thinks about it that way. Products
    nobody requested come back as `extras` (information, not a question)."""
    requests = (
        db.scalars(
            select(TransferRequest)
            .options(selectinload(TransferRequest.lines))
            .where(TransferRequest.id.in_(request_ids))
            .order_by(TransferRequest.id)
        ).all()
        if request_ids
        else []
    )
    requested: dict[int, float] = {}
    askers: dict[int, list[str]] = {}
    for req in requests:
        for line in req.lines:
            requested[line.product_id] = round(
                requested.get(line.product_id, 0.0) + float(line.qty_requested), 3
            )
            askers.setdefault(line.product_id, []).append(req.display_name)

    product_ids = set(requested) | set(contents)
    products = {
        p.id: p for p in db.scalars(select(Product).where(Product.id.in_(product_ids or {-1})))
    }
    threshold = settings.transfer_discrepancy_threshold
    review: list[DiscrepancyRow] = []
    extras: list[ExtraRow] = []
    for product_id in product_ids:
        product = products.get(product_id)
        if product is None:
            continue
        sent = round(float(contents.get(product_id, 0.0)), 3)
        asked = round(float(requested.get(product_id, 0.0)), 3)
        if asked <= 0:
            if sent > 0:
                extras.append(
                    ExtraRow(
                        product_id=product_id,
                        sku=product.global_sku,
                        barcode=product.barcode or "",
                        name=product.name,
                        qty_sent=sent,
                    )
                )
            continue
        if not needs_reason(asked, sent, threshold):
            continue
        existing = (saved or {}).get(product_id)
        review.append(
            DiscrepancyRow(
                product_id=product_id,
                sku=product.global_sku,
                barcode=product.barcode or "",
                name=product.name,
                qty_requested=asked,
                qty_sent=sent,
                delta=round(sent - asked, 3),
                requested_by=askers.get(product_id, []),
                reasons=list(existing.reasons or []) if existing else [],
                note=existing.note if existing else "",
            )
        )
    # short items first (the ones the floor will feel), biggest gap first
    review.sort(key=lambda r: (r.delta > 0, -abs(r.delta), r.name))
    extras.sort(key=lambda r: r.name)
    return review, extras


def preview(
    db: Session,
    settings: Settings,
    *,
    odoo_picking_id: int,
    request_ids: list[int],
) -> DeliveryPreview:
    """Everything the form needs for one selected picking + selection."""
    pallet = _declared_pallets_by_picking(db).get(odoo_picking_id)
    try:
        conn = get_connection(settings, read_only=True)
        rows = conn.search_read(
            "stock.picking",
            [["id", "=", odoo_picking_id]],
            ["name", "state", "scheduled_date", "date_done", "location_id"],
        )
        contents = picking_contents(db, conn, odoo_picking_id) if rows else {}
    except OdooError as e:
        return DeliveryPreview(
            picking=None,
            suggestions=[],
            review=[],
            extras=[],
            threshold=settings.transfer_discrepancy_threshold,
            note=f"Odoo is unreachable right now ({e}) — try again in a minute.",
        )
    if not rows:
        raise DeliveryError(f"Picking #{odoo_picking_id} isn't in Odoo (any more).")
    row = rows[0]
    staging2 = _location(db, "staging2")
    source = row.get("location_id")
    source_id = source[0] if isinstance(source, list) else source
    picking = CandidatePicking(
        odoo_picking_id=odoo_picking_id,
        name=str(row.get("name") or f"#{odoo_picking_id}"),
        state=str(row.get("state") or ""),
        date=str(row.get("date_done") or row.get("scheduled_date") or ""),
        item_count=len(contents),
        total_units=round(sum(contents.values()), 3),
        already_declared=pallet is not None and pallet.is_declared,
        declared_pallet_id=pallet.id if pallet is not None else None,
        from_staging2=staging2 is not None and source_id == staging2.odoo_id,
    )
    linked = {link.request_id for link in pallet.request_links} if pallet else set()
    saved = {d.product_id: d for d in pallet.discrepancies} if pallet else {}
    review, extras = discrepancy_review(db, settings, contents, request_ids, saved)
    return DeliveryPreview(
        picking=picking,
        suggestions=suggest_requests(
            db, contents, exclude_pallet_id=pallet.id if pallet else None, already_linked=linked
        ),
        review=review,
        extras=extras,
        threshold=settings.transfer_discrepancy_threshold,
    )


# ------------------------------------------------------------------ declare
@dataclass
class ReasonIn:
    product_id: int
    reasons: list[str]
    note: str = ""


def declare(
    db: Session,
    settings: Settings,
    *,
    actor_user_id: int | None,
    odoo_picking_id: int,
    request_ids: list[int],
    reasons: list[ReasonIn],
    note: str = "",
) -> PalletTransfer:
    """Submit the form: link the requests, freeze what's on the pallet,
    allocate sent quantities onto the request lines, and record the reasons.

    If the pallet is already validated in Odoo (the usual order — they make
    the transfer, then tell the app) it lands in the same breath."""
    try:
        conn = get_connection(settings, read_only=True)
        rows = conn.search_read(
            "stock.picking",
            [["id", "=", odoo_picking_id]],
            ["name", "state", "location_id", "location_dest_id"],
        )
        if not rows:
            raise DeliveryError(f"Picking #{odoo_picking_id} isn't in Odoo (any more).")
        contents = picking_contents(db, conn, odoo_picking_id)
    except OdooError as e:
        raise DeliveryError(
            f"Couldn't read the transfer from Odoo ({e}) — try again in a minute."
        ) from e

    row = rows[0]
    state = str(row.get("state") or "")
    if state == "cancel":
        raise DeliveryError(
            f"{row.get('name')} is cancelled in Odoo — pick the transfer you actually sent."
        )
    if not contents:
        raise DeliveryError(
            f"{row.get('name')} has no lines the app recognises — check it in Odoo "
            "(unmapped products can't be tracked against a request)."
        )

    # ---- the requests
    requests = list(
        db.scalars(
            select(TransferRequest)
            .options(selectinload(TransferRequest.lines))
            .where(TransferRequest.id.in_(request_ids or [-1]))
            .order_by(TransferRequest.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    missing = set(request_ids) - {r.id for r in requests}
    if missing:
        raise DeliveryError(f"Unknown transfer request(s): {sorted(missing)}.")
    if not requests:
        raise DeliveryError("Pick at least one transfer that's included in this delivery.")
    for req in requests:
        if req.status not in LINKABLE_STATUSES:
            raise DeliveryError(
                f"{req.display_name} is already {req.status} — it can't ride this delivery."
            )

    pallet = _declared_pallets_by_picking(db).get(odoo_picking_id)
    if pallet is None:
        pallet = PalletTransfer(created_by_id=actor_user_id, picking_reference="")
        db.add(pallet)
        db.flush()
    if pallet.status in ("counting", "counted"):
        raise DeliveryError(
            f"{pallet.display_name} has already been counted onto the floor — "
            "raise a new request for anything still missing."
        )
    taken = {
        request_id
        for request_id, pallet_id in db.execute(
            select(PalletRequestLink.request_id, PalletRequestLink.pallet_id)
        )
        if pallet_id != pallet.id
    }
    clash = sorted({r.display_name for r in requests if r.id in taken})
    if clash:
        raise DeliveryError(
            f"Already sent on another delivery: {', '.join(clash)}. "
            "Remove them, or fix that delivery instead."
        )

    # ---- the reasons (every big gap needs one)
    review, _extras = discrepancy_review(db, settings, contents, [r.id for r in requests])
    given = {r.product_id: r for r in reasons}
    cleaned: dict[int, tuple[list[str], str]] = {}
    unanswered: list[str] = []
    for row_ in review:
        answer = given.get(row_.product_id)
        if answer is None:
            unanswered.append(row_.name)
            continue
        cleaned[row_.product_id] = (
            validate_reasons(answer.reasons, answer.note),
            answer.note.strip(),
        )
    if unanswered:
        listed = ", ".join(unanswered[:5]) + ("…" if len(unanswered) > 5 else "")
        raise DeliveryError(f"These still need a reason: {listed}")

    # ---- write it all down
    pallet.odoo_picking_id = odoo_picking_id
    pallet.odoo_picking_name = str(row.get("name") or "")
    pallet.odoo_picking_url = _picking_url(settings, odoo_picking_id)
    pallet.note = note.strip()
    pallet.declared_by_id = actor_user_id
    pallet.declared_at = utcnow()
    products = {
        p.id: p for p in db.scalars(select(Product).where(Product.id.in_(contents or {-1})))
    }
    pallet.lines = [
        {
            "product_id": pid,
            "sku": (products[pid].odoo_internal_ref or products[pid].global_sku)
            if pid in products
            else "",
            "name": products[pid].name if pid in products else f"product {pid}",
            "qty": qty,
        }
        for pid, qty in sorted(contents.items(), key=lambda kv: kv[0])
    ]

    # Re-declaring replaces both sets. Clear and FLUSH first: within one flush
    # SQLAlchemy emits inserts before deletes, and re-submitting the same form
    # would then trip uq_pallet_request. Appending to the collections rather
    # than db.add()-ing rows also keeps them loaded, which land() (below, in
    # this same call) reads to close the requests.
    pallet.request_links.clear()
    pallet.discrepancies.clear()
    db.flush()
    for req in requests:
        pallet.request_links.append(PalletRequestLink(request_id=req.id))
    for product_id, (codes, row_note) in cleaned.items():
        matching = next((r for r in review if r.product_id == product_id), None)
        pallet.discrepancies.append(
            PalletDiscrepancy(
                product_id=product_id,
                qty_requested=matching.qty_requested if matching else 0.0,
                qty_sent=matching.qty_sent if matching else 0.0,
                reasons=codes,
                note=row_note,
            )
        )

    # ---- sent quantities: the pallet is the truth now, not the draft
    ordered_lines = [
        (line.id, line.product_id, float(line.qty_requested))
        for req in requests
        for line in req.lines
    ]
    allocated = allocate_sent(ordered_lines, contents)
    for req in requests:
        for line in req.lines:
            line.qty_sent = allocated.get(line.id, 0.0)

    # ---- the timeline, for the floor's benefit
    others = len(requests) - 1
    with_others = f" with {others} other request{'s' if others != 1 else ''}" if others else ""
    for req in requests:
        if req.status != TransferRequestStatus.SENT.value:
            # requested/working → sent: the warehouse is the only role that
            # can make this move, and only they can reach this function
            req.status = TransferRequestStatus.SENT.value
        _event(
            db, req, TransferEventKind.ODOO,
            f"on delivery {pallet.display_name} to the floor{with_others}",
            actor_user_id,
        )
        shortfalls = _shortfall_notes(req, cleaned, review)
        if shortfalls:
            _event(
                db, req, TransferEventKind.DISCREPANCY,
                "not everything asked for is on this delivery — " + "; ".join(shortfalls),
                actor_user_id,
            )
    db.flush()

    if state == "done":
        land(db, settings, pallet, actor_user_id=actor_user_id)
    db.commit()
    return pallet


def _shortfall_notes(
    req: TransferRequest,
    cleaned: dict[int, tuple[list[str], str]],
    review: list[DiscrepancyRow],
) -> list[str]:
    """"…with a clear note that it wasn't included" (Noah, 2026-08-17): for
    every line this request doesn't get in full, say the numbers and the
    warehouse's own reason."""
    by_product = {r.product_id: r for r in review}
    notes: list[str] = []
    for line in req.lines:
        sent = float(line.qty_sent or 0)
        asked = float(line.qty_requested)
        if sent >= asked:
            continue
        text = f"{line.product.name}: {sent:g} of {asked:g}"
        if sent == 0:
            text = f"{line.product.name}: none of {asked:g}"
        answer = cleaned.get(line.product_id)
        if answer:
            text += f" ({reason_summary(*answer)})"
        elif line.product_id in by_product:
            text += " (no reason given)"
        notes.append(text)
    return notes


def _picking_url(settings: Settings, picking_id: int) -> str:
    from ..odoo.urls import odoo_record_url

    return odoo_record_url(settings, "stock.picking", picking_id)


# ---------------------------------------------------------------- it landed
def land(
    db: Session,
    settings: Settings,
    pallet: PalletTransfer,
    actor_user_id: int | None = None,
) -> int:
    """The pallet reached floor staging (validated in Odoo).

    Every request it carries closes as DONE against it — the floor's ask is
    answered, and the link to the received transfer is on the request. Then
    ONE count transfer is prepared for the whole pallet: the floor counts a
    pallet once, and its own differences reconcile against the delivery.

    An UNDECLARED pallet lands as a pallet and closes nothing: the app has
    no idea whose stock is on it, and guessing would close requests that
    are still waiting. It shows up on the deliveries list asking for its
    details instead. Returns how many requests closed."""
    now = utcnow()
    if pallet.status not in ("counting", "counted"):
        pallet.status = "validated"
    pallet.validated_at = pallet.validated_at or now

    closed = 0
    for link in pallet.request_links:
        req = link.request
        if req.status in (
            TransferRequestStatus.DONE.value,
            TransferRequestStatus.CANCELLED.value,
        ):
            continue
        req.status = TransferRequestStatus.DONE.value
        _event(
            db, req, TransferEventKind.STATUS,
            f"received on the floor — {pallet.display_name} landed at floor staging",
            actor_user_id,
            status=TransferRequestStatus.DONE.value,
        )
        closed += 1

    if pallet.is_declared:
        prepare_delivery_count(db, settings, pallet, actor_user_id)
    return closed


def prepare_delivery_count(
    db: Session,
    settings: Settings,
    pallet: PalletTransfer,
    actor_user_id: int | None = None,
) -> None:
    """ONE floor-staging → floor count transfer for the whole pallet, marked
    To Do with availability checked — ready to scan in Odoo's barcode app.

    Copies the delivery picking, which the warehouse usually made themselves
    (hence allow_foreign_source; see the writer's note on why that's safe).
    Records the honest outcome either way."""
    if pallet.count_status == OdooWriteOutcome.CREATED.value:
        return
    writer = OdooWriter(db, settings, actor_user_id=actor_user_id)
    reference = pallet.count_reference or new_reference("CNT")
    pallet.count_reference = reference
    if not pallet.odoo_picking_id:
        pallet.count_status = OdooWriteOutcome.SIMULATED.value
        pallet.count_error = ""
        return
    try:
        result = writer.prepare_count_transfer(
            source_picking_odoo_id=pallet.odoo_picking_id,
            reference=reference,
            allow_foreign_source=True,
        )
    except (OdooWriteError, ValueError) as e:
        pallet.count_status = OdooWriteOutcome.FAILED.value
        pallet.count_error = str(e)
        return
    pallet.count_error = ""
    if result.dry_run:
        pallet.count_status = OdooWriteOutcome.SIMULATED.value
        return
    pallet.count_status = OdooWriteOutcome.CREATED.value
    pallet.count_picking_id = result.record_ids[0] if result.record_ids else None
    pallet.count_picking_name = result.record_name
    pallet.count_picking_url = result.deep_link
    pallet.count_barcode_url = (
        barcode_url(settings, pallet.count_picking_id) if pallet.count_picking_id else ""
    )
    pallet.status = "counting"


def poll_delivery_counts(db: Session, settings: Settings) -> int:
    """Listener for the delivery count's validation in Odoo. On 'done', the
    counted quantities are compared with what the pallet was carrying and
    the differences queue as adjustments AGAINST THE DELIVERY (request_id
    stays NULL — the requests closed when the pallet landed; this is the
    floor's count of the pallet).

    Throttled per pallet, like every other listener. Returns how many
    deliveries closed out."""
    pallets = db.scalars(
        select(PalletTransfer)
        .options(selectinload(PalletTransfer.request_links))
        .where(
            PalletTransfer.status == "counting",
            PalletTransfer.count_status == OdooWriteOutcome.CREATED.value,
            PalletTransfer.count_picking_id.is_not(None),
        )
    ).all()
    now = utcnow()
    due = [
        p for p in pallets if is_due(p.count_checked_at, settings.odoo_count_poll_seconds, now)
    ]
    if not due:
        return 0
    for pallet in due:
        pallet.count_checked_at = now
    db.commit()  # persist the throttle stamps even if the reads below fail

    try:
        conn = get_connection(settings, read_only=True)
        states = {
            r["id"]: str(r.get("state") or "")
            for r in conn.search_read(
                "stock.picking",
                [["id", "in", [p.count_picking_id for p in due]]],
                ["state"],
            )
        }
    except OdooError as e:
        log.warning("delivery count poll failed: %s", e)
        return 0

    closed = 0
    for pallet in due:
        state = states.get(pallet.count_picking_id or -1, "")
        if state == "cancel":
            pallet.count_status = OdooWriteOutcome.FAILED.value
            pallet.count_error = "cancelled in Odoo"
            pallet.status = "validated"
            continue
        if state != "done":
            continue
        try:
            counted = picking_contents(db, conn, pallet.count_picking_id or 0)
        except OdooError as e:
            log.warning("could not read count %s: %s", pallet.count_picking_name, e)
            continue
        _log_count_differences(pallet, counted)
        pallet.status = "counted"
        closed += 1
    db.commit()
    return closed


def _log_count_differences(pallet: PalletTransfer, counted: dict[int, float]) -> None:
    """Pallet-vs-count differences used to feed an adjustments queue; that
    queue is gone (2026-08-24). The validated count picking in Odoo is the
    durable record — this log line is just the operational breadcrumb."""
    expected = {
        int(line.get("product_id") or 0): float(line.get("qty") or 0)
        for line in (pallet.lines or [])
    }
    diffs = []
    for product_id in set(expected) | set(counted):
        if product_id <= 0:
            continue
        sent = round(expected.get(product_id, 0.0), 3)
        found = round(counted.get(product_id, 0.0), 3)
        if sent != found:
            diffs.append(f"product {product_id}: sent {sent:g}, counted {found:g}")
    if diffs:
        log.info(
            "count %s of %s differs from the pallet: %s",
            pallet.count_picking_name, pallet.display_name, "; ".join(diffs),
        )


# ---------------------------------------- one-time: the pre-form leftovers
# When the delivery form landed (2026-08-17), requests were already mid-flight
# under the old rules: each one had its OWN floor-staging → floor count
# picking prepared and sat in `counting` waiting for the floor to scan it.
# Their stock is really sitting in Staging2, riding whichever pallet the
# warehouse builds next (III/INT/04709 carries several, Noah 2026-08-18) — but
# `counting` isn't a linkable status, so the form couldn't see them and they
# would sit there forever.
#
# Releasing one means: hand it back to `sent` (staged) so the form offers it,
# and drop the app's memory of that per-request count — the pallet gets ONE
# count for everything on it, which is the whole point of the rework.
#
# Two deliberate limits:
#   * a count picking Odoo says is DONE is left alone. The floor really
#     counted it; `poll_close_out` closes it on the next poll, and rewinding
#     would throw away a count that actually happened. Which means the Odoo
#     read has to SUCCEED — no read, no release (fail closed), or a genuinely
#     counted request could be reopened on a guess.
#   * the app cancels nothing in Odoo. Retiring a stale count picking is a
#     new write operation, and this is a one-off; the report names each one
#     with a deep link so a human cancels it where they already work. That
#     matters physically: if the floor later scans a leftover count AND the
#     pallet's count, the same units move twice.
RELEASABLE_STATUSES = (
    TransferRequestStatus.COUNTING.value,
    # a `sent` request with a count already prepared is the same trap
    TransferRequestStatus.SENT.value,
)

# Odoo states that mean the count never happened, so it's safe to retire
_UNCOUNTED_STATES = ("draft", "waiting", "confirmed", "assigned", "cancel")


@dataclass
class ReleasedRequest:
    request_id: int
    display_name: str
    was_status: str
    line_count: int
    total_requested: float
    count_picking_name: str
    count_picking_url: str
    count_state: str  # Odoo's word for it, "" when there was nothing live
    action: str  # released | already_counted | no_count_to_retire
    detail: str  # one sentence, for a human


@dataclass
class ReleaseReport:
    applied: bool
    released: int
    skipped: int
    rows: list[ReleasedRequest] = field(default_factory=list)
    cancel_in_odoo: list[ReleasedRequest] = field(default_factory=list)
    note: str = ""


def _releasable(db: Session) -> list[TransferRequest]:
    """Requests stuck in the old count flow and not already on a delivery."""
    linked = {rid for (rid,) in db.execute(select(PalletRequestLink.request_id))}
    out = []
    for req in db.scalars(
        select(TransferRequest)
        .options(selectinload(TransferRequest.lines))
        .where(TransferRequest.status.in_(RELEASABLE_STATUSES))
        .order_by(TransferRequest.id)
        .execution_options(populate_existing=True)
    ):
        if req.id in linked:
            continue
        if req.status == TransferRequestStatus.COUNTING.value or req.count_status in (
            OdooWriteOutcome.CREATED.value,
            OdooWriteOutcome.SIMULATED.value,
        ):
            out.append(req)
    return out


def release_stale_counts(
    db: Session,
    settings: Settings,
    apply: bool = False,
    actor_user_id: int | None = None,
) -> ReleaseReport:
    """Hand the pre-form requests back to the delivery form. Preview by
    default (`apply=False` changes nothing) — the preview is also how anyone
    finds out what state the hosted stack is actually in."""
    candidates = _releasable(db)
    if not candidates:
        return ReleaseReport(
            applied=apply,
            released=0,
            skipped=0,
            note="Nothing is stuck — every open request is already visible to the form.",
        )

    live_ids = [r.count_picking_id for r in candidates if r.count_picking_id]
    states: dict[int, str] = {}
    if live_ids:
        try:
            conn = get_connection(settings, read_only=True)
            states = {
                int(row["id"]): str(row.get("state") or "")
                for row in conn.search_read(
                    "stock.picking", [["id", "in", live_ids]], ["state", "name"]
                )
            }
        except OdooError as e:
            # fail closed: without Odoo we can't tell a counted request from
            # an uncounted one, and reopening a real count is the worse error
            raise DeliveryError(
                f"Can't reach Odoo to check whether these counts were validated ({e}). "
                "Nothing was changed — try again when Odoo answers."
            ) from e

    rows: list[ReleasedRequest] = []
    to_cancel: list[ReleasedRequest] = []
    released = skipped = 0
    for req in candidates:
        state = states.get(req.count_picking_id or -1, "")
        has_live_picking = bool(req.count_picking_id) and state != ""
        row = ReleasedRequest(
            request_id=req.id,
            display_name=req.display_name,
            was_status=req.status,
            line_count=len(req.lines),
            total_requested=round(sum(line.qty_requested for line in req.lines), 3),
            count_picking_name=req.count_picking_name,
            count_picking_url=req.count_picking_url,
            count_state=state,
            action="",
            detail="",
        )

        if state == "done":
            row.action = "already_counted"
            row.detail = (
                f"{req.count_picking_name} was validated in Odoo — the floor really counted "
                "it, so it closes itself on the next refresh. Left alone."
            )
            skipped += 1
            rows.append(row)
            continue

        if has_live_picking and state not in _UNCOUNTED_STATES:
            # an Odoo state we don't recognise: say so instead of acting on it
            row.action = "already_counted"
            row.detail = (
                f"{req.count_picking_name} is '{state}' in Odoo — not a state this fix knows "
                "how to retire. Left alone; look at it in Odoo."
            )
            skipped += 1
            rows.append(row)
            continue

        row.action = "released" if apply else "would_release"
        if has_live_picking and state != "cancel":
            row.detail = (
                f"back to staged — the pallet form can carry it now. "
                f"{req.count_picking_name} ({state}) is still open in Odoo: cancel it there, "
                "or the same units move twice."
            )
            to_cancel.append(row)
        elif has_live_picking:
            row.detail = (
                f"back to staged — {req.count_picking_name} was already cancelled in Odoo."
            )
        else:
            row.detail = (
                "back to staged — its count was never rendered in Odoo, so there's nothing "
                "to retire."
            )
        rows.append(row)
        released += 1

        if not apply:
            continue

        if req.count_picking_name:
            note = (
                "released for the delivery form — this request was waiting on its own count "
                f"transfer ({req.count_picking_name}"
                + (f", {state} in Odoo" if state else "")
                + "), which the pallet's single count replaces."
            )
            if state and state != "cancel":
                note += " Cancel that transfer in Odoo."
        else:
            note = (
                "released for the delivery form — its own count transfer was never rendered "
                "in Odoo, so nothing was retired."
            )
        req.status = TransferRequestStatus.SENT.value
        req.count_status = OdooWriteOutcome.NONE.value
        req.count_reference = ""
        req.count_error = ""
        req.count_picking_id = None
        req.count_picking_name = ""
        req.count_picking_url = ""
        req.count_barcode_url = ""
        req.count_checked_at = None
        _event(
            db, req, TransferEventKind.STATUS, note, actor_user_id,
            status=TransferRequestStatus.SENT.value,
        )

    if apply:
        db.commit()

    verb = "Released" if apply else "Would release"
    note = f"{verb} {released} request(s); {skipped} left alone."
    if to_cancel:
        note += (
            f" {len(to_cancel)} count transfer(s) are still open in Odoo — cancel them there "
            "so the floor doesn't scan the same units twice."
        )
    return ReleaseReport(
        applied=apply,
        released=released,
        skipped=skipped,
        rows=rows,
        cancel_in_odoo=to_cancel,
        note=note,
    )
