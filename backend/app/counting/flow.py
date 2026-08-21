"""Inventory count review rules — pure, no I/O.

Two things live here and nowhere else:

  * **what a submission's status is.** It is never set directly: it is rolled
    up from its items, because the spec requires mixed outcomes (7 approved,
    1 rejected, 2 out for recount) and a submission-level status that tells
    the truth about them.

  * **what order the review queue is in.** Recounts rank above first-time
    submissions — a recount is someone's second trip to the same shelf, and
    leaving it to age wastes that trip.
"""
from __future__ import annotations

from ..models import CountItemStatus as I
from ..models import CountStatus as S

# a reviewer's decision is final for that item; only these two can still move
OPEN_ITEM_STATUSES = (I.PENDING.value, I.RECOUNT.value)
DECIDED_ITEM_STATUSES = (I.APPROVED.value, I.REJECTED.value)

# reasons are mandatory on these, per spec §11
REASON_REQUIRED = ("reject", "recount")


class CountError(ValueError):
    """Something the user can fix, phrased for them (HTTP 422)."""


def roll_up(item_statuses: list[str]) -> str:
    """A submission's status, derived from its items.

    Order of the checks is the meaning: an outstanding recount is the most
    useful thing to say about a submission, even when other items are already
    decided, because it names what the submission is WAITING on."""
    if not item_statuses:
        return S.PENDING.value
    if any(s == I.RECOUNT.value for s in item_statuses):
        return S.RECOUNT.value
    if all(s in DECIDED_ITEM_STATUSES for s in item_statuses):
        return S.COMPLETED.value
    if any(s in DECIDED_ITEM_STATUSES for s in item_statuses):
        return S.PARTIAL.value
    return S.PENDING.value


def queue_rank(item_status: str, attempts: int) -> tuple:
    """Sort key for the review queue: recounts first, then the most-recounted
    (someone has been back twice — decide it), then oldest by id at the call
    site. Decided items sink; they're history, not work."""
    is_open = item_status in OPEN_ITEM_STATUSES
    awaiting_recount = item_status == I.RECOUNT.value
    has_recounts = attempts > 1
    return (
        not is_open,  # open work first
        not has_recounts,  # a submitted recount outranks a first count
        not awaiting_recount,  # then recounts still being counted
        -attempts,  # more trips = more urgent
    )


def can_review(item_status: str) -> bool:
    """Approve / reject / request-recount only apply while an item is open."""
    return item_status in OPEN_ITEM_STATUSES


def check_reason(action: str, note: str) -> None:
    if action in REASON_REQUIRED and not note.strip():
        raise CountError(
            "A reason is required — it becomes part of the item's permanent history, and the "
            "person who counted will read it."
        )
