"""The BWHSE→Floor transfer request state machine — pure, no I/O.

    requested → working_on_it → sent → counting → done
        ↘ cancelled (from requested / working_on_it only)

Placing a request renders the BWHSE→STAGING draft in Odoo immediately (the
request adopts the picking's name). Warehouse acknowledges ("working on it")
and finishes ("sent" — their part ends there). The app then prepares the
STAGING→FLOOR count transfer for Odoo's barcode app and listens for its
validation; sent-vs-counted mismatches become adjustments, not chat messages.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..models import Role
from ..models import TransferRequestStatus as S

# (from, to) -> roles that may perform the transition (admin always may)
TRANSITIONS: dict[tuple[str, str], set[Role]] = {
    (S.REQUESTED.value, S.WORKING.value): {Role.WAREHOUSE},
    (S.WORKING.value, S.SENT.value): {Role.WAREHOUSE},
    # requested→sent directly: small orders get grabbed without an ack
    (S.REQUESTED.value, S.SENT.value): {Role.WAREHOUSE},
    # sent→counting happens when the count transfer is prepared (app-driven);
    # counting→done happens when Odoo validation is detected (or the manual
    # fallback closes a simulated one) — floor owns both.
    (S.SENT.value, S.COUNTING.value): {Role.WAREHOUSE, Role.SHOPPE_FLOOR},
    (S.COUNTING.value, S.DONE.value): {Role.SHOPPE_FLOOR},
    (S.SENT.value, S.DONE.value): {Role.SHOPPE_FLOOR},  # manual close, no count picking
    (S.REQUESTED.value, S.CANCELLED.value): {Role.WAREHOUSE, Role.SHOPPE_FLOOR},
    (S.WORKING.value, S.CANCELLED.value): {Role.WAREHOUSE, Role.SHOPPE_FLOOR},
}

ACTIVE_STATUSES = (
    S.REQUESTED.value,
    S.WORKING.value,
    S.SENT.value,
    S.COUNTING.value,
)


class InvalidTransition(ValueError):
    """The flow doesn't go that way from here (HTTP 409)."""


class NotAllowedError(PermissionError):
    """The flow goes that way, but not for this role (HTTP 403)."""


def check_transition(current: str, to: str, role_names: set[str]) -> None:
    allowed_roles = TRANSITIONS.get((current, to))
    if allowed_roles is None:
        raise InvalidTransition(f"A request can't go from '{current}' to '{to}'.")
    if Role.ADMIN.value in role_names:
        return
    if not ({r.value for r in allowed_roles} & role_names):
        names = " or ".join(sorted(r.value for r in allowed_roles))
        raise NotAllowedError(f"Only {names} can move a request from '{current}' to '{to}'.")


@dataclass(frozen=True)
class Discrepancy:
    line_id: int
    product_id: int
    qty_expected: float  # what warehouse sent
    qty_counted: float  # what the validated count found
    delta: float  # counted - expected; negative = missing


def reconcile(lines: list) -> list[Discrepancy]:
    """Sent vs counted for every line that moved (either direction counts —
    surprise extras in staging matter too)."""
    out: list[Discrepancy] = []
    for line in lines:
        sent = float(line.qty_sent or 0)
        counted = float(line.qty_counted or 0)
        if sent == 0 and counted == 0:
            continue
        if counted != sent:
            out.append(
                Discrepancy(
                    line_id=line.id,
                    product_id=line.product_id,
                    qty_expected=sent,
                    qty_counted=counted,
                    delta=round(counted - sent, 3),
                )
            )
    return out
