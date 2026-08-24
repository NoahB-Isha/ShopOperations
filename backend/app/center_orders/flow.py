"""The city-center / department order state machine — pure, no I/O.

    pending → approved → shipped
        ↘ rejected
        ↘ cancelled

Placing an order stores it PENDING and pings the zone's coordinator. The
coordinator approves (optionally adjusting quantities first), rejects, or the
orderer withdraws it. Approval renders the draft Odoo transfer; SHIPPED is
detected by polling that picking (a human validates it in Odoo — the app only
watches), so it has no role row here: the service flips it, never a user.
"""
from __future__ import annotations

from ..models import CenterOrderStatus as S
from ..models import Role

# Who may decide an order at all. The add-on is in here because a department's
# order is approved by whoever is behind the counter; WHICH orders each of them
# may decide is the router's job (a dept approver reaches departments zones
# only) — this table only says the move exists for the role.
COORDINATOR_ROLES = {Role.ZONE_COORDINATOR, Role.DEPT_ORDER_APPROVER}

# (from, to) -> roles that may perform the transition (admin always may).
# ORDERER entries additionally require owning the order's center — the router
# checks scope; this table only says which roles the move exists for.
TRANSITIONS: dict[tuple[str, str], set[Role]] = {
    (S.PENDING.value, S.APPROVED.value): set(COORDINATOR_ROLES),
    (S.PENDING.value, S.REJECTED.value): set(COORDINATOR_ROLES),
    (S.PENDING.value, S.CANCELLED.value): (
        COORDINATOR_ROLES | {Role.CENTER_ORDERER}
    ),
}

ACTIVE_STATUSES = (S.PENDING.value, S.APPROVED.value)
DECIDED_STATUSES = (S.APPROVED.value, S.SHIPPED.value, S.REJECTED.value, S.CANCELLED.value)


class InvalidTransition(ValueError):
    """The flow doesn't go that way from here (HTTP 409)."""


class NotAllowedError(PermissionError):
    """The flow goes that way, but not for this role (HTTP 403)."""


def check_transition(current: str, to: str, role_names: set[str]) -> None:
    allowed_roles = TRANSITIONS.get((current, to))
    if allowed_roles is None:
        raise InvalidTransition(f"An order can't go from '{current}' to '{to}'.")
    if Role.ADMIN.value in role_names:
        return
    if not ({r.value for r in allowed_roles} & role_names):
        names = " or ".join(sorted(r.value for r in allowed_roles))
        raise NotAllowedError(f"Only {names} can move an order from '{current}' to '{to}'.")
