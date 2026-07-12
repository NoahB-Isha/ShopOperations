"""The BWHSE→Floor transfer request state machine — pure, no I/O.

    requested → picked → in_staging → counted → on_floor
        ↘ cancelled (from requested/picked only)

Floor volunteers request and count; warehouse picks and stages. Both sides
watch one shared timeline. The staging count reconciles what warehouse says
it sent against what the floor actually found; every mismatch becomes an
adjustment for the warehouse queue instead of a WhatsApp message.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..models import Role
from ..models import TransferRequestStatus as S

# (from, to) -> roles that may perform the transition (admin always may)
TRANSITIONS: dict[tuple[str, str], set[Role]] = {
    (S.REQUESTED.value, S.PICKED.value): {Role.WAREHOUSE},
    (S.PICKED.value, S.IN_STAGING.value): {Role.WAREHOUSE},
    (S.IN_STAGING.value, S.COUNTED.value): {Role.SHOPPE_FLOOR},
    (S.COUNTED.value, S.ON_FLOOR.value): {Role.SHOPPE_FLOOR},
    (S.REQUESTED.value, S.CANCELLED.value): {Role.WAREHOUSE, Role.SHOPPE_FLOOR},
    (S.PICKED.value, S.CANCELLED.value): {Role.WAREHOUSE},
}

# Odoo draft legs: which request states may render each draft, and who may.
# Leg 1 mirrors the physical BWHSE→STAGING move (sent quantities); leg 2 the
# STAGING→FLOOR move (counted quantities). Both are DRAFTS a human validates.
LEG_BWHSE_STAGING = "bwhse_staging"
LEG_STAGING_FLOOR = "staging_floor"
ODOO_LEGS: dict[str, dict] = {
    LEG_BWHSE_STAGING: {
        "source_key": "bwhse",
        "dest_key": "staging",
        "qty_field": "qty_sent",
        "states": {S.PICKED.value, S.IN_STAGING.value, S.COUNTED.value, S.ON_FLOOR.value},
        "roles": {Role.WAREHOUSE},
        "label": "BWHSE → Staging",
    },
    LEG_STAGING_FLOOR: {
        "source_key": "staging",
        "dest_key": "floor",
        "qty_field": "qty_counted",
        "states": {S.COUNTED.value, S.ON_FLOOR.value},
        "roles": {Role.WAREHOUSE, Role.SHOPPE_FLOOR},
        "label": "Staging → Floor",
    },
}


class InvalidTransition(ValueError):
    """The flow doesn't go that way from here (HTTP 409)."""


class NotAllowedError(PermissionError):
    """The flow goes that way, but not for this role (HTTP 403)."""


def check_transition(current: str, to: str, role_names: set[str]) -> None:
    allowed_roles = TRANSITIONS.get((current, to))
    if allowed_roles is None:
        raise InvalidTransition(
            f"A request can't go from '{current}' to '{to}'."
        )
    if Role.ADMIN.value in role_names:
        return
    if not ({r.value for r in allowed_roles} & role_names):
        names = " or ".join(sorted(r.value for r in allowed_roles))
        raise NotAllowedError(f"Only {names} can move a request from '{current}' to '{to}'.")


def check_leg(leg: str, status: str, role_names: set[str]) -> dict:
    spec = ODOO_LEGS.get(leg)
    if spec is None:
        raise InvalidTransition(f"Unknown Odoo draft leg '{leg}'.")
    if status not in spec["states"]:
        raise InvalidTransition(
            f"The {spec['label']} draft isn't available while the request is '{status}'."
        )
    if Role.ADMIN.value not in role_names and not (
        {r.value for r in spec["roles"]} & role_names
    ):
        raise NotAllowedError(f"Your role can't create the {spec['label']} draft.")
    return spec


@dataclass(frozen=True)
class Discrepancy:
    line_id: int
    product_id: int
    qty_expected: float  # what warehouse recorded as sent
    qty_counted: float  # what the floor found in staging
    delta: float  # counted - expected; negative = missing


def reconcile(lines: list) -> list[Discrepancy]:
    """Sent vs counted for every line that was actually sent (or counted —
    surprise items in staging count too)."""
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
