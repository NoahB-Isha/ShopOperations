"""Clear the transfer/delivery flow back to a known starting point.

Two weeks of testing left the board full of requests that were never really
pulled and fifteen pallets the app discovered in Odoo but nobody declared.
Noah's call (2026-08-18): wipe it, keep anything asked for in the last 24
hours, and start the real process from the NEXT pallet.

What this is careful about:

  * **Odoo is not ours to rewrite.** The app removes only pickings it created
    itself AND that are still drafts (a draft moves no stock) — the same rule
    `cancel_placement_draft` follows. Anything validated, or made by a human,
    is reported with a deep link and left exactly where it is.
  * **Discovery needs a floor, not just a delete.** `poll_manual_pallets`
    de-dupes against the pallet rows it already has, so deleting the fifteen
    would rediscover the same fifteen on the next poll. The reset writes a
    `discover_from` watermark, which is what "start from the next pallet"
    actually means.
  * **Recent asks survive.** The cutoff is on the request's own created_at,
    so a floor request raised this morning keeps its lines, its events and
    its Odoo draft.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from ..config import Settings
from ..models import (
    OdooWriteOutcome,
    PalletDiscrepancy,
    PalletRequestLink,
    PalletTransfer,
    TransferEvent,
    TransferRequest,
    TransferRequestLine,
    elapsed_since,
    utcnow,
)
from ..odoo.connection import get_connection
from ..odoo.errors import OdooError, OdooWriteError
from ..odoo.urls import odoo_record_url
from ..odoo.writer import OdooWriter
from ..ordering.service import get_app_setting, set_app_setting
from .pallet import MANUAL_PALLET_STATE_KEY

log = logging.getLogger("transfers.reset")


@dataclass
class OdooLeftover:
    """A picking the reset did NOT touch, and why."""

    picking_name: str
    url: str
    state: str
    belonged_to: str
    reason: str


@dataclass
class ResetReport:
    applied: bool
    keep_hours: int
    cutoff: str
    requests_cleared: int
    requests_kept: int
    pallets_cleared: int
    events_cleared: int = 0
    drafts_removed: list[str] = field(default_factory=list)
    already_gone: list[str] = field(default_factory=list)  # deleted in Odoo already
    leftovers: list[OdooLeftover] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    discover_from: str = ""
    note: str = ""


class ResetError(RuntimeError):
    """Something the admin can retry, phrased for them (HTTP 422)."""


def _picking_states(settings: Settings, ids: list[int]) -> dict[int, str]:
    """One read for every picking the reset might touch.

    Odoo not answering is a REFUSAL, not an empty dict: with no states, every
    picking reads as unknown, so the app would delete its own rows while
    reporting "cancel these in Odoo" about drafts it could have removed
    itself — and once the row is gone it can never remove them. Retrying is
    free; orphaning drafts nobody has a record of is not.

    An id the read simply doesn't return is a different thing entirely: that
    picking is already gone from Odoo (deleted during testing), and asking
    someone to go cancel it would be a wild goose chase. Callers tell the two
    apart by asking whether the id is in the result."""
    if not ids:
        return {}
    try:
        conn = get_connection(settings, read_only=True)
    except OdooError as e:
        raise ResetError(
            f"Can't reach Odoo to check these pickings ({e}). Nothing was changed — "
            "try again when Odoo answers."
        ) from e
    try:
        return {
            int(row["id"]): str(row.get("state") or "")
            for row in conn.search_read("stock.picking", [["id", "in", ids]], ["state"])
        }
    except OdooError as e:
        raise ResetError(
            f"Odoo refused the picking read ({e}). Nothing was changed — try again."
        ) from e


def reset_delivery_flow(
    db: Session,
    settings: Settings,
    keep_hours: int = 24,
    apply: bool = False,
    actor_user_id: int | None = None,
) -> ResetReport:
    """Preview by default. `apply=True` deletes the rows, removes the app's own
    still-draft pickings from Odoo, and stamps the discovery watermark."""
    now = utcnow()
    cutoff = now - timedelta(hours=max(0, keep_hours))

    requests = db.scalars(
        select(TransferRequest)
        .options(selectinload(TransferRequest.lines))
        .order_by(TransferRequest.id)
        .execution_options(populate_existing=True)
    ).all()
    # elapsed_since, not a bare comparison: SQLite hands created_at back naive
    # and Postgres hands it back aware (see models/base — this is the one copy
    # of that dance, and getting it wrong raises inside the maintenance action)
    keep_seconds = max(0, keep_hours) * 3600
    doomed = [r for r in requests if elapsed_since(r.created_at, now) > keep_seconds]
    kept = [r for r in requests if elapsed_since(r.created_at, now) <= keep_seconds]
    pallets = db.scalars(select(PalletTransfer).order_by(PalletTransfer.id)).all()

    # every app-created picking these rows point at, placement and count alike
    candidates: dict[int, tuple[str, str]] = {}  # odoo id -> (name, owner)
    for req in doomed:
        if req.picking_status == OdooWriteOutcome.CREATED.value and req.odoo_picking_id:
            candidates[req.odoo_picking_id] = (req.odoo_picking_name, req.display_name)
        if req.count_status == OdooWriteOutcome.CREATED.value and req.count_picking_id:
            candidates[req.count_picking_id] = (req.count_picking_name, req.display_name)
    for p in pallets:
        if p.picking_status == OdooWriteOutcome.CREATED.value and p.odoo_picking_id:
            candidates[p.odoo_picking_id] = (p.odoo_picking_name, p.display_name)
        if p.count_status == OdooWriteOutcome.CREATED.value and p.count_picking_id:
            candidates[p.count_picking_id] = (p.count_picking_name, p.display_name)

    states = _picking_states(settings, list(candidates))
    drafts_removed: list[str] = []
    already_gone: list[str] = []
    leftovers: list[OdooLeftover] = []
    writer = OdooWriter(db, settings, actor_user_id=actor_user_id)
    for picking_id, (name, owner) in sorted(candidates.items()):
        url = odoo_record_url(settings, "stock.picking", picking_id)
        if picking_id not in states:
            # the read succeeded and this one wasn't in it: it's already gone
            already_gone.append(name or f"picking {picking_id}")
            continue
        state = states[picking_id]
        if state == "draft":
            if not apply:
                drafts_removed.append(name or f"picking {picking_id}")
                continue
            try:
                writer.unlink_app_record("stock.picking", picking_id)
                drafts_removed.append(name or f"picking {picking_id}")
            except (OdooWriteError, ValueError) as e:
                leftovers.append(
                    OdooLeftover(
                        picking_name=name or f"picking {picking_id}",
                        url=url,
                        state=state,
                        belonged_to=owner,
                        reason=f"the app couldn't remove it ({e}) — delete it in Odoo",
                    )
                )
            continue
        leftovers.append(
            OdooLeftover(
                picking_name=name or f"picking {picking_id}",
                url=url,
                state=state or "unknown",
                belonged_to=owner,
                reason=(
                    "already validated — the stock really moved, so it stays; cancel or "
                    "leave it in Odoo as you see fit"
                    if state == "done"
                    else f"'{state}' in Odoo, not a draft — cancel it there"
                ),
            )
        )

    doomed_ids = [r.id for r in doomed]
    pallet_ids = [p.id for p in pallets]
    event_count = (
        db.scalar(
            select(func.count(TransferEvent.id)).where(
                TransferEvent.request_id.in_(doomed_ids or [-1])
            )
        )
        or 0
    )
    report = ResetReport(
        applied=apply,
        keep_hours=keep_hours,
        cutoff=cutoff.isoformat(),
        requests_cleared=len(doomed),
        requests_kept=len(kept),
        pallets_cleared=len(pallets),
        events_cleared=event_count,
        drafts_removed=drafts_removed,
        already_gone=already_gone,
        leftovers=leftovers,
        kept=[r.display_name for r in kept],
        discover_from=now.isoformat() if apply else "",
    )

    if not apply:
        report.note = (
            f"Would clear {len(doomed)} request(s) and {len(pallets)} pallet(s), keeping "
            f"{len(kept)} request(s) from the last {keep_hours}h. "
            f"{len(drafts_removed)} app draft(s) would be removed from Odoo; "
            f"{len(leftovers)} picking(s) would be left for a human."
        )
        return report

    # ---- rows: children first, in FK order
    if pallet_ids:
        db.execute(delete(PalletDiscrepancy).where(PalletDiscrepancy.pallet_id.in_(pallet_ids)))
        db.execute(delete(PalletRequestLink).where(PalletRequestLink.pallet_id.in_(pallet_ids)))
    if doomed_ids:
        db.execute(delete(PalletRequestLink).where(PalletRequestLink.request_id.in_(doomed_ids)))
    if doomed_ids:
        db.execute(delete(TransferEvent).where(TransferEvent.request_id.in_(doomed_ids)))
        db.execute(
            delete(TransferRequestLine).where(TransferRequestLine.request_id.in_(doomed_ids))
        )
        db.execute(delete(TransferRequest).where(TransferRequest.id.in_(doomed_ids)))
    if pallet_ids:
        db.execute(delete(PalletTransfer).where(PalletTransfer.id.in_(pallet_ids)))

    # ---- the watermark: everything already in Odoo is history from here
    poll_state = get_app_setting(db, MANUAL_PALLET_STATE_KEY) or {}
    set_app_setting(
        db,
        MANUAL_PALLET_STATE_KEY,
        {
            **poll_state,
            # Odoo's own datetime format, since it goes straight into a domain
            "discover_from": now.strftime("%Y-%m-%d %H:%M:%S"),
            "reset_at": now.isoformat(),
        },
    )
    db.commit()

    report.note = (
        f"Cleared {len(doomed)} request(s), {len(pallets)} pallet(s) and {event_count} "
        f"event(s). Kept {len(kept)} request(s) from the last "
        f"{keep_hours}h. The next pallet validated in Odoo is the first one the app will "
        "see. "
    )
    if leftovers:
        report.note += (
            f"{len(leftovers)} picking(s) are still in Odoo — the app only removes its own "
            "drafts; the list says what each one is."
        )
    return report
