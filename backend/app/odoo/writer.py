"""OdooWriter — the single gateway for every write the app makes to Odoo.

No other code may call create/write/unlink (read-only connections refuse them
outright). Every operation here:

  * is a typed, named operation — never a raw pass-through;
  * writes an audit row (who, when, payload, resulting ids, success) whether
    it ran live, dry, or failed;
  * supports dry-run, and is FORCED into dry-run by (in order) an explicit
    request, the global kill switch (ODOO_WRITES_ENABLED=false), the
    operation's feature flag being off, or fixture mode;
  * creates records in DRAFT state only, stamped with an ILAPP- reference,
    and returns the Odoo deep link for the human handoff;
  * is idempotent — a retry with the same reference returns the existing
    record instead of duplicating it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import FeatureFlag, OdooLocation, OdooWriteAudit, Product
from .connection import get_connection
from .errors import OdooError, OdooWriteError
from .operations import (
    TransferLine,
    build_internal_transfer_payload,
    is_app_reference,
    new_reference,
)
from .protocol import OdooConnection
from .urls import odoo_record_url

# operation name -> feature flag key (new operations get added HERE, nowhere else)
OPERATION_FLAGS = {
    "create_internal_transfer": "write_create_internal_transfer",
}


@dataclass
class WriteResult:
    operation: str
    reference: str
    dry_run: bool
    dry_run_reason: str  # "" when live
    success: bool
    odoo_model: str
    record_ids: list[int] = field(default_factory=list)
    deep_link: str = ""
    payload: dict = field(default_factory=dict)
    message: str = ""
    audit_id: int | None = None


class WriterValidationError(ValueError):
    """Bad input to a write operation (unknown product, empty lines…)."""


class OdooWriter:
    def __init__(
        self,
        db: Session,
        settings: Settings,
        conn: OdooConnection | None = None,
        actor_user_id: int | None = None,
    ):
        self.db = db
        self.settings = settings
        self.actor_user_id = actor_user_id
        self._conn = conn

    @property
    def conn(self) -> OdooConnection:
        if self._conn is None:
            self._conn = get_connection(self.settings, read_only=False)
        return self._conn

    # ----------------------------------------------------------------- gates
    def _flag_enabled(self, key: str) -> bool:
        flag = self.db.get(FeatureFlag, key)
        return bool(flag and flag.enabled)

    def _forced_dry_run_reason(
        self, operation: str, requested: bool, ignore_feature_flag: bool
    ) -> str:
        if requested:
            return "requested"
        if not self.settings.odoo_writes_enabled:
            return "kill_switch"
        if not ignore_feature_flag:
            flag_key = OPERATION_FLAGS.get(operation)
            if flag_key and not self._flag_enabled(flag_key):
                return "feature_flag"
        if self.settings.odoo_mode == "fixture":
            return "fixture_mode"
        return ""

    # ----------------------------------------------------------------- audit
    def _audit(
        self,
        *,
        operation: str,
        reference: str,
        dry_run: bool,
        dry_run_reason: str,
        success: bool,
        odoo_model: str,
        record_ids: list[int],
        payload: dict,
        response: dict,
        error: str,
        started: float,
    ) -> int:
        row = OdooWriteAudit(
            actor_user_id=self.actor_user_id,
            operation=operation,
            reference=reference,
            dry_run=dry_run,
            dry_run_reason=dry_run_reason,
            success=success,
            odoo_model=odoo_model,
            odoo_record_ids=record_ids,
            request_payload=payload,
            response=response,
            error=error,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        self.db.add(row)
        self.db.commit()
        return row.id

    # ------------------------------------------------------- transfer helpers
    def _resolve_location(self, key: str) -> OdooLocation:
        loc = self.db.scalar(select(OdooLocation).where(OdooLocation.key == key))
        if loc is None:
            raise WriterValidationError(
                f"Location '{key}' isn't mapped yet — run a stock sync so the app "
                "discovers Odoo location ids."
            )
        return loc

    def _transfer_env(self) -> tuple[int | None, str, list[str]]:
        """(picking_type_id, move one2many field name, warnings). Field names
        are checked against the instance rather than assumed; failures fall
        back to sane defaults so dry-runs work even with Odoo unreachable."""
        warnings: list[str] = []
        move_field = "move_ids"
        picking_type_id: int | None = None
        try:
            fg = self.conn.fields_get("stock.picking")
            if "move_ids" not in fg and "move_ids_without_package" in fg:
                move_field = "move_ids_without_package"
        except OdooError as e:
            warnings.append(f"could not verify stock.picking fields ({e}); assuming move_ids")
        try:
            types = self.conn.search_read(
                "stock.picking.type", [["code", "=", "internal"]], ["id", "name"], order="id asc"
            )
            if types:
                picking_type_id = types[0]["id"]
            else:
                warnings.append("no internal picking type found on the instance")
        except OdooError as e:
            warnings.append(f"could not resolve internal picking type ({e})")
        return picking_type_id, move_field, warnings

    # ---------------------------------------------------------- operations
    def create_internal_transfer(
        self,
        *,
        source_key: str,
        dest_key: str,
        lines: list[dict],
        note: str = "",
        reference: str | None = None,
        dry_run: bool = False,
        ignore_feature_flag: bool = False,  # canary protocol only
    ) -> WriteResult:
        """Create a DRAFT internal transfer (stock.picking) between two of the
        app's known locations. `lines` = [{"product_id": <app id>, "qty": n}]."""
        started = time.monotonic()
        operation = "create_internal_transfer"

        # ---- validate inputs (before any gate, so dry-runs are honest)
        if not lines:
            raise WriterValidationError("A transfer needs at least one line.")
        if source_key == dest_key:
            raise WriterValidationError("Source and destination are the same location.")
        source = self._resolve_location(source_key)
        dest = self._resolve_location(dest_key)
        transfer_lines: list[TransferLine] = []
        for line in lines:
            qty = float(line.get("qty", 0))
            if qty <= 0:
                raise WriterValidationError(f"Quantity must be positive (got {qty}).")
            product = self.db.get(Product, int(line.get("product_id", 0)))
            if product is None:
                raise WriterValidationError(f"Unknown product id {line.get('product_id')}.")
            if not product.is_stock_tracked or not product.odoo_product_id:
                raise WriterValidationError(
                    f"'{product.name}' is not stock-tracked in Odoo — it can't go on a transfer."
                )
            transfer_lines.append(
                TransferLine(
                    product_odoo_id=product.odoo_product_id,
                    description=f"{product.global_sku} {product.name}"[:120],
                    qty=qty,
                )
            )

        reference = reference or new_reference("XFER")
        reason = self._forced_dry_run_reason(operation, dry_run, ignore_feature_flag)
        picking_type_id, move_field, env_warnings = self._transfer_env()
        payload = build_internal_transfer_payload(
            picking_type_id=picking_type_id,
            source_location_id=source.odoo_id,
            dest_location_id=dest.odoo_id,
            reference=reference,
            lines=transfer_lines,
            move_field=move_field,
            note=note,
        )

        if reason:
            audit_id = self._audit(
                operation=operation,
                reference=reference,
                dry_run=True,
                dry_run_reason=reason,
                success=True,
                odoo_model="stock.picking",
                record_ids=[],
                payload=payload,
                response={"warnings": env_warnings},
                error="",
                started=started,
            )
            return WriteResult(
                operation=operation,
                reference=reference,
                dry_run=True,
                dry_run_reason=reason,
                success=True,
                odoo_model="stock.picking",
                payload=payload,
                message=_dry_run_message(reason),
                audit_id=audit_id,
            )

        # ---- live path
        try:
            existing = self.conn.search_read(
                "stock.picking", [["origin", "=", reference]], ["id", "name", "state"]
            )
            if existing:
                rec = existing[0]
                audit_id = self._audit(
                    operation=operation,
                    reference=reference,
                    dry_run=False,
                    dry_run_reason="",
                    success=True,
                    odoo_model="stock.picking",
                    record_ids=[rec["id"]],
                    payload=payload,
                    response={"idempotent_hit": True, "record": rec},
                    error="",
                    started=started,
                )
                return WriteResult(
                    operation=operation,
                    reference=reference,
                    dry_run=False,
                    dry_run_reason="",
                    success=True,
                    odoo_model="stock.picking",
                    record_ids=[rec["id"]],
                    deep_link=odoo_record_url(self.settings, "stock.picking", rec["id"]),
                    payload=payload,
                    message=f"Transfer already exists as {rec.get('name')} (idempotent retry).",
                    audit_id=audit_id,
                )

            picking_id = self.conn.call_kw("stock.picking", "create", [payload])
            if isinstance(picking_id, list):
                picking_id = picking_id[0]
            readback = self.conn.call_kw(
                "stock.picking", "read", [[picking_id], ["name", "state", "origin"]]
            )
            rec = readback[0] if readback else {}
        except OdooError as e:
            audit_id = self._audit(
                operation=operation,
                reference=reference,
                dry_run=False,
                dry_run_reason="",
                success=False,
                odoo_model="stock.picking",
                record_ids=[],
                payload=payload,
                response={},
                error=str(e),
                started=started,
            )
            raise OdooWriteError(f"Odoo rejected the transfer: {e}") from e

        deep_link = odoo_record_url(self.settings, "stock.picking", picking_id)
        audit_id = self._audit(
            operation=operation,
            reference=reference,
            dry_run=False,
            dry_run_reason="",
            success=True,
            odoo_model="stock.picking",
            record_ids=[picking_id],
            payload=payload,
            response={"record": rec, "warnings": env_warnings},
            error="",
            started=started,
        )
        return WriteResult(
            operation=operation,
            reference=reference,
            dry_run=False,
            dry_run_reason="",
            success=True,
            odoo_model="stock.picking",
            record_ids=[picking_id],
            deep_link=deep_link,
            payload=payload,
            message=f"Draft transfer {rec.get('name', picking_id)} created — review it in Odoo.",
            audit_id=audit_id,
        )

    def unlink_app_record(self, model: str, record_id: int) -> WriteResult:
        """Delete a record — permitted ONLY for records the app itself created,
        verified by the ILAPP-/APP-TEST- reference prefix (never by account,
        which is shared with a human). Used by the canary protocol."""
        started = time.monotonic()
        operation = "unlink_app_record"
        rows = self.conn.call_kw(model, "read", [[record_id], ["origin", "name"]]) or []
        if not rows:
            raise WriterValidationError(f"{model} #{record_id} not found.")
        ref = str(rows[0].get("origin") or rows[0].get("name") or "")
        if not is_app_reference(ref):
            raise WriterValidationError(
                f"Refusing to unlink {model} #{record_id}: its reference {ref!r} is not "
                "app-prefixed, so the app didn't create it."
            )
        if not self.settings.odoo_writes_enabled or self.settings.odoo_mode == "fixture":
            reason = "kill_switch" if self.settings.odoo_mode == "live" else "fixture_mode"
            audit_id = self._audit(
                operation=operation,
                reference=ref,
                dry_run=True,
                dry_run_reason=reason,
                success=True,
                odoo_model=model,
                record_ids=[record_id],
                payload={"model": model, "id": record_id},
                response={},
                error="",
                started=started,
            )
            return WriteResult(
                operation=operation,
                reference=ref,
                dry_run=True,
                dry_run_reason=reason,
                success=True,
                odoo_model=model,
                record_ids=[record_id],
                message=_dry_run_message(reason),
                audit_id=audit_id,
            )
        try:
            self.conn.call_kw(model, "unlink", [[record_id]])
        except OdooError as e:
            self._audit(
                operation=operation,
                reference=ref,
                dry_run=False,
                dry_run_reason="",
                success=False,
                odoo_model=model,
                record_ids=[record_id],
                payload={"model": model, "id": record_id},
                response={},
                error=str(e),
                started=started,
            )
            raise OdooWriteError(f"Unlink failed: {e}") from e
        audit_id = self._audit(
            operation=operation,
            reference=ref,
            dry_run=False,
            dry_run_reason="",
            success=True,
            odoo_model=model,
            record_ids=[record_id],
            payload={"model": model, "id": record_id},
            response={},
            error="",
            started=started,
        )
        return WriteResult(
            operation=operation,
            reference=ref,
            dry_run=False,
            dry_run_reason="",
            success=True,
            odoo_model=model,
            record_ids=[record_id],
            message=f"{model} #{record_id} removed.",
            audit_id=audit_id,
        )


def _dry_run_message(reason: str) -> str:
    return {
        "requested": "Dry run — nothing was written to Odoo.",
        "kill_switch": "Dry run — the global kill switch (ODOO_WRITES_ENABLED) is off.",
        "feature_flag": "Dry run — this operation's feature flag is off (canary it first).",
        "fixture_mode": "Dry run — running in fixture mode (no Odoo credentials).",
    }.get(reason, "Dry run.")
