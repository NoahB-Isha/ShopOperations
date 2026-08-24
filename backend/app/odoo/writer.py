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

import math
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
    build_count_transfer_payload,
    build_internal_transfer_payload,
    build_inventory_adjustment_payload,
    is_app_reference,
    new_reference,
)
from .protocol import OdooConnection
from .urls import odoo_record_url

# operation name -> feature flag key (new operations get added HERE, nowhere else)
OPERATION_FLAGS = {
    "create_internal_transfer": "write_create_internal_transfer",
    "prepare_count_transfer": "write_prepare_count_transfer",
    "create_inventory_reduction": "write_create_inventory_reduction",
    "create_inventory_addition": "write_create_inventory_addition",
    "validate_adjustment": "write_validate_inventory_adjustment",
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
    record_name: str = ""  # Odoo display name (e.g. III/INT/00042) when read back
    deep_link: str = ""
    payload: dict = field(default_factory=dict)
    message: str = ""
    audit_id: int | None = None


class WriterValidationError(ValueError):
    """Bad input to a write operation (unknown product, empty lines…)."""


# (odoo base url, picking-type name, which default location) -> resolved env.
# Process-lifetime: these are Odoo configuration records, and re-reading them
# once per adjustment is what tripped Odoo's rate limiter (see _adjustment_env).
_ADJUSTMENT_ENV_CACHE: dict[tuple[str, str, str], tuple[int | None, int | None, list[str]]] = {}
_ADJUSTMENT_TYPE_IDS_CACHE: dict[str, set[int]] = {}


def clear_adjustment_caches() -> None:
    """Forget the resolved operation types (tests; and after changing the
    configured type names)."""
    _ADJUSTMENT_ENV_CACHE.clear()
    _ADJUSTMENT_TYPE_IDS_CACHE.clear()


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
        dest_key: str = "",
        lines: list[dict],
        note: str = "",
        reference: str | None = None,
        dry_run: bool = False,
        ignore_feature_flag: bool = False,  # canary protocol only
        dest_odoo_location_id: int | None = None,  # center destinations
        dest_label: str = "",
    ) -> WriteResult:
        """Create a DRAFT internal transfer (stock.picking) from one of the
        app's known locations to either another known location (`dest_key`)
        or an explicit Odoo location id (`dest_odoo_location_id`, e.g. a
        center's III/CityCenter/… location discovered by the stock sync).
        `lines` = [{"product_id": <app id>, "qty": n}]."""
        started = time.monotonic()
        operation = "create_internal_transfer"

        # ---- validate inputs (before any gate, so dry-runs are honest)
        if not lines:
            raise WriterValidationError("A transfer needs at least one line.")
        if bool(dest_key) == (dest_odoo_location_id is not None):
            raise WriterValidationError(
                "Give exactly one destination: a location key or an Odoo location id."
            )
        source = self._resolve_location(source_key)
        if dest_key:
            if source_key == dest_key:
                raise WriterValidationError("Source and destination are the same location.")
            dest_id = self._resolve_location(dest_key).odoo_id
        else:
            dest_id = int(dest_odoo_location_id or 0)
            if dest_id <= 0:
                raise WriterValidationError(
                    f"'{dest_label or 'destination'}' has no Odoo location mapped yet — "
                    "run a stock sync, or fix the center's location name in Odoo."
                )
        if dest_id == source.odoo_id:
            raise WriterValidationError("Source and destination are the same location.")
        transfer_lines: list[TransferLine] = []
        for line in lines:
            qty = float(line.get("qty", 0))
            # NaN fails EVERY comparison, so `qty <= 0` let it through and it
            # reached the move payload; inf would too. Check finiteness first.
            if not math.isfinite(qty) or not qty > 0:
                raise WriterValidationError(f"Quantity must be a positive number (got {qty}).")
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
            dest_location_id=dest_id,
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
                    record_name=str(rec.get("name") or ""),
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
            record_name=str(rec.get("name") or ""),
            deep_link=deep_link,
            payload=payload,
            message=f"Draft transfer {rec.get('name', picking_id)} created — review it in Odoo.",
            audit_id=audit_id,
        )

    def _adjustment_env(
        self, wanted: str, need: str
    ) -> tuple[int | None, int | None, list[str]]:
        """(picking_type_id, the type's default src/dest location id, warnings)
        for an inventory-adjustment operation type matched by configured name
        (ilike; % wildcards allowed). `need` is which default the operation
        requires: 'dest' for reductions, 'src' for additions. Failures fall
        back to None so dry-runs still render honestly.

        Cached per process, because approving a whole inventory count makes one
        adjustment per item and this lookup ran on every one of them: on
        2026-08-22 a 65-item review put Odoo's proxy over its limit and 16
        approvals came back "could not resolve the adjustment picking type
        (HTTP 429)" — approvals with no Odoo record at all. The operation types
        are configuration; they do not change between two clicks."""
        cache_key = (self.settings.odoo_base_url, wanted, need)
        cached = _ADJUSTMENT_ENV_CACHE.get(cache_key)
        if cached is not None:
            return cached
        warnings: list[str] = []
        type_id: int | None = None
        loc_id: int | None = None
        field = "default_location_dest_id" if need == "dest" else "default_location_src_id"
        try:
            types = self.conn.search_read(
                "stock.picking.type",
                [["name", "ilike", wanted]],
                ["id", "name", field],
                order="id asc",
            )
            if types:
                type_id = types[0]["id"]
                loc = types[0].get(field)
                loc_id = loc[0] if isinstance(loc, list) else (loc or None)
                if not loc_id:
                    side = "destination" if need == "dest" else "source"
                    warnings.append(
                        f"picking type '{types[0].get('name')}' has no default {side} "
                        "location — set one in Odoo"
                    )
            else:
                warnings.append(f"no picking type matching '{wanted}' on the instance")
        except OdooError as e:
            # Not cached: a transient failure must not poison every later call.
            warnings.append(f"could not resolve the adjustment picking type ({e})")
            return type_id, loc_id, warnings
        if type_id is not None:
            _ADJUSTMENT_ENV_CACHE[cache_key] = (type_id, loc_id, warnings)
        return type_id, loc_id, warnings

    def create_inventory_reduction(
        self,
        *,
        product_id: int,
        qty: float,
        note: str = "",
        reference: str | None = None,
        dry_run: bool = False,
        ignore_feature_flag: bool = False,
        location_odoo_id: int | None = None,
    ) -> WriteResult:
        """Create a DRAFT picking on the inventory-reduction operation type
        ("USA-III: Inventory Adj Reduction") removing `qty` of one product
        from the floor — the floor team's 'this shelf is actually empty' data
        cleanup. Draft only; a human confirms it in Odoo."""
        return self._create_adjustment_draft(
            operation="create_inventory_reduction",
            label="reduction",
            type_name=self.settings.odoo_reduction_picking_type,
            need="dest",  # floor → the type's default destination (loss)
            product_id=product_id,
            qty=qty,
            note=note,
            reference=reference,
            dry_run=dry_run,
            ignore_feature_flag=ignore_feature_flag,
            location_odoo_id=location_odoo_id,
        )

    def create_inventory_addition(
        self,
        *,
        product_id: int,
        qty: float,
        note: str = "",
        reference: str | None = None,
        dry_run: bool = False,
        ignore_feature_flag: bool = False,
        location_odoo_id: int | None = None,
    ) -> WriteResult:
        """Create a DRAFT picking on the inventory-addition operation type
        ("USA-III: Inventory Adj  Adding Qty") putting `qty` of one product
        ONTO the floor — the back-in-stock counterpart of the reduction.
        Draft only; a human confirms it in Odoo."""
        return self._create_adjustment_draft(
            operation="create_inventory_addition",
            label="addition",
            type_name=self.settings.odoo_addition_picking_type,
            need="src",  # the type's default source (loss) → floor
            product_id=product_id,
            qty=qty,
            note=note,
            reference=reference,
            dry_run=dry_run,
            ignore_feature_flag=ignore_feature_flag,
            location_odoo_id=location_odoo_id,
        )

    def _create_adjustment_draft(
        self,
        *,
        operation: str,
        label: str,
        type_name: str,
        need: str,
        product_id: int,
        qty: float,
        note: str,
        reference: str | None,
        dry_run: bool,
        ignore_feature_flag: bool,
        location_odoo_id: int | None = None,
    ) -> WriteResult:
        """The shared core of both inventory adjustments — identical
        discipline, opposite directions.

        `location_odoo_id` is the shelf being corrected. It defaults to the
        FLOOR, which is what the OOS board and the floor-count edit mean; the
        inventory-counting flow passes the counted location explicitly, because
        a count can be taken in the warehouse or at SHIP (which has no synced
        location row of its own — see counting/locations.py)."""
        started = time.monotonic()

        # NaN fails EVERY comparison, so `qty <= 0` let it through onto a real
        # adjustment draft; inf would too. Check finiteness first.
        if not math.isfinite(qty) or not qty > 0:
            raise WriterValidationError(f"Quantity must be a positive number (got {qty:g}).")
        product = self.db.get(Product, int(product_id))
        if product is None:
            raise WriterValidationError(f"Unknown product id {product_id}.")
        if not product.is_stock_tracked or not product.odoo_product_id:
            raise WriterValidationError(
                f"'{product.name}' is not stock-tracked in Odoo — nothing to adjust."
            )
        place_id = location_odoo_id or self._resolve_location("floor").odoo_id
        line = TransferLine(
            product_odoo_id=product.odoo_product_id,
            description=f"{product.global_sku} {product.name}"[:120],
            qty=qty,
        )

        reference = reference or new_reference("OOS")
        reason = self._forced_dry_run_reason(operation, dry_run, ignore_feature_flag)
        type_id, loc_id, env_warnings = self._adjustment_env(type_name, need)
        src_id: int | None
        dest_id: int | None
        if need == "dest":
            src_id, dest_id = place_id, loc_id
        else:
            src_id, dest_id = loc_id, place_id
        payload = build_inventory_adjustment_payload(
            picking_type_id=type_id,
            source_location_id=src_id,
            dest_location_id=dest_id,
            reference=reference,
            line=line,
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
        if type_id is None or not src_id or not dest_id:
            raise WriterValidationError(
                f"The inventory-{label} operation type isn't usable: "
                + "; ".join(env_warnings)
            )
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
                    record_name=str(rec.get("name") or ""),
                    deep_link=odoo_record_url(self.settings, "stock.picking", rec["id"]),
                    payload=payload,
                    message=f"The {label} already exists as {rec.get('name')} (idempotent retry).",
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
            raise OdooWriteError(f"Odoo rejected the {label}: {e}") from e

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
            record_name=str(rec.get("name") or ""),
            deep_link=odoo_record_url(self.settings, "stock.picking", picking_id),
            payload=payload,
            message=f"Draft {label} {rec.get('name', picking_id)} created — review it in Odoo.",
            audit_id=audit_id,
        )

    def validate_adjustment(
        self,
        *,
        picking_odoo_id: int,
        reference: str = "",
        dry_run: bool = False,
        ignore_feature_flag: bool = False,
    ) -> WriteResult:
        """Post an inventory-adjustment picking the app created — the ONE
        operation that moves stock instead of proposing it.

        Everything else in this class stops at a draft for a human to
        validate. Counting is the deliberate exception (Noah, 2026-08-22): a
        reviewer has already compared the counted number against Odoo's and
        approved it, so a second human clicking Validate on 49 pickings adds
        no judgement — it only adds a queue. See DECISIONS.md.

        Because this one really does change stock, its guards are the tightest
        in the file, and BOTH of them are load-bearing:

          * the origin must be app-prefixed — never post a human's picking;
          * the picking TYPE must be one of the two inventory-adjustment
            types. `ILAPP-CNT-` is shared with the floor's STAGING→FLOOR count
            transfers (transfers/service.prepare_count_transfer uses the same
            prefix), so a reference-only check would post pallets nobody has
            counted yet. Type is what tells the two apart.

        Backorders are refused rather than confirmed: a quantity below demand
        means the shelf couldn't give what the count asked for, which is a
        question for a person, not something to answer with a wizard."""
        started = time.monotonic()
        operation = "validate_adjustment"

        if picking_odoo_id <= 0:
            raise WriterValidationError("No picking to validate.")
        payload = {"picking_id": picking_odoo_id, "method": "button_validate"}
        reason = self._forced_dry_run_reason(operation, dry_run, ignore_feature_flag)
        if reason:
            audit_id = self._audit(
                operation=operation,
                reference=reference,
                dry_run=True,
                dry_run_reason=reason,
                success=True,
                odoo_model="stock.picking",
                record_ids=[picking_odoo_id],
                payload=payload,
                response={},
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
                record_ids=[picking_odoo_id],
                payload=payload,
                message=_dry_run_message(reason),
                audit_id=audit_id,
            )

        try:
            state, name = self._check_validatable(picking_odoo_id)
            if state == "done":
                steps = ["already validated (idempotent retry)"]
            else:
                steps = self._post_adjustment(picking_odoo_id, state)
                state = str(
                    (
                        self.conn.call_kw("stock.picking", "read", [[picking_odoo_id], ["state"]])
                        or [{}]
                    )[0].get("state", "")
                )
                if state != "done":
                    raise OdooWriteError(
                        f"{name} is still '{state}' after validating — check it in Odoo."
                    )
        except (OdooError, OdooWriteError, WriterValidationError) as e:
            self._audit(
                operation=operation,
                reference=reference,
                dry_run=False,
                dry_run_reason="",
                success=False,
                odoo_model="stock.picking",
                record_ids=[picking_odoo_id],
                payload=payload,
                response={},
                error=str(e),
                started=started,
            )
            if isinstance(e, WriterValidationError):
                raise
            raise OdooWriteError(str(e)) from e

        audit_id = self._audit(
            operation=operation,
            reference=reference,
            dry_run=False,
            dry_run_reason="",
            success=True,
            odoo_model="stock.picking",
            record_ids=[picking_odoo_id],
            payload=payload,
            response={"steps": steps, "state": state},
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
            record_ids=[picking_odoo_id],
            record_name=name,
            deep_link=odoo_record_url(self.settings, "stock.picking", picking_odoo_id),
            payload=payload,
            message=f"{name} validated in Odoo ({', '.join(steps)}).",
            audit_id=audit_id,
        )

    def _check_validatable(self, picking_odoo_id: int) -> tuple[str, str]:
        """(state, name) — or a refusal. See validate_adjustment for why both
        the reference AND the picking type are checked."""
        rows = self.conn.call_kw(
            "stock.picking",
            "read",
            [[picking_odoo_id], ["name", "origin", "state", "picking_type_id"]],
        )
        if not rows:
            raise WriterValidationError(f"Picking #{picking_odoo_id} not found in Odoo.")
        rec = rows[0]
        name = str(rec.get("name") or f"#{picking_odoo_id}")
        if not is_app_reference(str(rec.get("origin") or "")):
            raise WriterValidationError(
                f"Refusing to validate {name}: its reference isn't app-prefixed, so the "
                "app didn't create it."
            )
        type_id = rec.get("picking_type_id")
        type_id = type_id[0] if isinstance(type_id, list) else type_id
        if type_id not in self._adjustment_type_ids():
            type_label = (
                rec["picking_type_id"][1]
                if isinstance(rec.get("picking_type_id"), list)
                else "unknown"
            )
            raise WriterValidationError(
                f"Refusing to validate {name}: '{type_label}' is not an inventory-adjustment "
                "operation type. Only adjustments are posted by the app; transfers and "
                "counts are validated by the person doing them."
            )
        state = str(rec.get("state") or "")
        if state == "cancel":
            raise WriterValidationError(f"{name} is cancelled in Odoo — nothing to validate.")
        return state, name

    def _adjustment_type_ids(self) -> set[int]:
        """The ids of the two configured inventory-adjustment operation types.
        Empty would make the type guard vacuous, so an empty result raises.

        Cached like _adjustment_env, and for the same reason: posting a whole
        count is one call per item, and re-reading configuration on each one is
        what got the app rate-limited."""
        cache_key = self.settings.odoo_base_url
        cached = _ADJUSTMENT_TYPE_IDS_CACHE.get(cache_key)
        if cached:
            return cached
        ids: set[int] = set()
        for wanted in (
            self.settings.odoo_reduction_picking_type,
            self.settings.odoo_addition_picking_type,
        ):
            for row in self.conn.search_read(
                "stock.picking.type", [["name", "ilike", wanted]], ["id"], order="id asc"
            ):
                ids.add(int(row["id"]))
        if not ids:
            raise WriterValidationError(
                "Neither inventory-adjustment operation type could be found in Odoo — "
                "refusing to validate anything until the type guard works."
            )
        _ADJUSTMENT_TYPE_IDS_CACHE[cache_key] = ids
        return ids

    def _post_adjustment(self, picking_odoo_id: int, state: str) -> list[str]:
        """Confirm → reserve → mark the lines picked → button_validate."""
        steps: list[str] = []
        if state == "draft":
            self.conn.call_kw("stock.picking", "action_confirm", [[picking_odoo_id]])
            steps.append("confirmed")
            state = "confirmed"
        if state in ("confirmed", "waiting"):
            self.conn.call_kw("stock.picking", "action_assign", [[picking_odoo_id]])
            steps.append("reserved")

        moves = self.conn.search_read(
            "stock.move",
            [["picking_id", "=", picking_odoo_id]],
            ["id", "product_uom_qty", "quantity", "picked"],
        )
        if not moves:
            raise WriterValidationError("That picking has no lines — nothing to post.")
        # Odoo 17+ posts `quantity` on lines flagged `picked`. Set both, so an
        # adjustment can't validate as a zero and quietly do nothing.
        for m in moves:
            vals: dict = {}
            if float(m.get("quantity") or 0) != float(m["product_uom_qty"]):
                vals["quantity"] = m["product_uom_qty"]
            if not m.get("picked"):
                vals["picked"] = True
            if vals:
                self.conn.call_kw("stock.move", "write", [[m["id"]], vals])
        steps.append(f"{len(moves)} line(s) picked")

        res = self.conn.call_kw("stock.picking", "button_validate", [[picking_odoo_id]])
        if isinstance(res, dict) and res.get("res_model"):
            # A wizard came back — most often the backorder confirmation, which
            # means Odoo could not give the full quantity. Answering it blind
            # would post a number nobody counted.
            raise OdooWriteError(
                f"Odoo asked for confirmation ({res.get('res_model')}) instead of validating — "
                "the quantity on hand can't satisfy this adjustment. Handle it in Odoo."
            )
        steps.append("validated")
        return steps

    def prepare_count_transfer(
        self,
        *,
        source_picking_odoo_id: int,
        reference: str | None = None,
        dry_run: bool = False,
        ignore_feature_flag: bool = False,
        allow_foreign_source: bool = False,
    ) -> WriteResult:
        """Duplicate a picking as the STAGING→FLOOR count transfer: copy,
        retarget the locations, mark To Do (action_confirm), check
        availability (action_assign). The result is a ready-to-scan transfer
        for Odoo's barcode app — a human validates it there; the app only
        watches for that validation.

        `allow_foreign_source` permits copying a picking the app did NOT
        create. Since 2026-08-17 the pallet that carries stock to the floor
        is normally made by the warehouse in Odoo and declared on the app's
        delivery form, so its origin is not app-prefixed — and its contents
        are exactly what the floor has to count. This does not weaken the
        blast-radius rules: Odoo's `copy` writes nothing to the source, the
        COPY still carries our own ILAPP-CNT- reference (so unlink safety is
        unchanged), and the caller has to ask for it explicitly."""
        started = time.monotonic()
        operation = "prepare_count_transfer"

        if source_picking_odoo_id <= 0:
            raise WriterValidationError("No source picking to duplicate.")
        staging = self._resolve_location("staging")
        floor = self._resolve_location("floor")

        reference = reference or new_reference("CNT")
        reason = self._forced_dry_run_reason(operation, dry_run, ignore_feature_flag)
        payload = build_count_transfer_payload(
            source_picking_odoo_id=source_picking_odoo_id,
            staging_location_id=staging.odoo_id,
            floor_location_id=floor.odoo_id,
            reference=reference,
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
                response={},
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
            src = self.conn.call_kw(
                "stock.picking", "read", [[source_picking_odoo_id], ["origin", "name", "state"]]
            )
            if not src:
                raise WriterValidationError(
                    f"Picking #{source_picking_odoo_id} not found in Odoo."
                )
            if not allow_foreign_source and not is_app_reference(
                str(src[0].get("origin") or "")
            ):
                raise WriterValidationError(
                    f"Refusing to duplicate {src[0].get('name')}: its reference isn't "
                    "app-prefixed, so the app didn't create it."
                )

            # idempotent: an earlier attempt may have made the copy already
            existing = self.conn.search_read(
                "stock.picking", [["origin", "=", reference]], ["id", "name", "state"]
            )
            if existing:
                new_id = existing[0]["id"]
                steps = ["found existing copy (idempotent retry)"]
            else:
                copied = self.conn.call_kw(
                    "stock.picking",
                    "copy",
                    [[source_picking_odoo_id]],
                    {"default": dict(payload["copy_defaults"])},
                )
                new_id = copied[0] if isinstance(copied, list) else int(copied)
                steps = ["copied"]

            # the copied moves keep their old endpoints — retarget them
            move_ids = [
                m["id"]
                for m in self.conn.search_read(
                    "stock.move", [["picking_id", "=", new_id]], ["id"]
                )
            ]
            if move_ids:
                self.conn.call_kw(
                    "stock.move", "write", [move_ids, dict(payload["then_update_moves"])]
                )
                steps.append(f"retargeted {len(move_ids)} move(s)")

            state = str(
                (self.conn.call_kw("stock.picking", "read", [[new_id], ["state"]]) or [{}])[0].get(
                    "state", ""
                )
            )
            if state == "draft":
                self.conn.call_kw("stock.picking", "action_confirm", [[new_id]])
                steps.append("marked To Do")
            self.conn.call_kw("stock.picking", "action_assign", [[new_id]])
            steps.append("availability checked")

            readback = self.conn.call_kw(
                "stock.picking", "read", [[new_id], ["name", "state", "origin"]]
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
            raise OdooWriteError(f"Odoo rejected the count transfer: {e}") from e

        audit_id = self._audit(
            operation=operation,
            reference=reference,
            dry_run=False,
            dry_run_reason="",
            success=True,
            odoo_model="stock.picking",
            record_ids=[new_id],
            payload=payload,
            response={"record": rec, "steps": steps},
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
            record_ids=[new_id],
            record_name=str(rec.get("name") or ""),
            deep_link=odoo_record_url(self.settings, "stock.picking", new_id),
            payload=payload,
            message=f"Count transfer {rec.get('name', new_id)} ready to scan "
            f"({rec.get('state', '?')}).",
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
