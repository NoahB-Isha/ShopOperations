"""The gated canary protocol (test layer C).

Before a write operation's feature flag may be enabled, an admin runs its
canary against production: create one clearly-marked APP-TEST- draft record
with a minimal line, read it back, verify the deep link, then unlink it.
Draft records are inert in Odoo (they move no stock), so the worst case is a
stray draft a human can delete.

Canaries run ONLY from an explicit admin action — never automatically, never
in CI. The operation's feature flag stays off; the canary bypasses that one
gate but still honours the global kill switch. Every step is audit-logged
like any other write.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import Product, utcnow
from .connection import get_connection
from .errors import OdooError
from .operations import CANARY_REF_PREFIX
from .protocol import OdooConnection
from .writer import OdooWriter, WriterValidationError


def run_canary_create_internal_transfer(
    db: Session,
    settings: Settings,
    actor_user_id: int | None,
    dry_run: bool = False,
    conn: OdooConnection | None = None,  # tests inject the simulator here
) -> dict:
    steps: list[dict] = []

    def step(name: str, ok: bool, detail: str = "") -> bool:
        steps.append({"name": name, "ok": ok, "detail": detail})
        return ok

    result: dict = {
        "operation": "create_internal_transfer",
        "mode": settings.odoo_mode,
        "dry_run": dry_run,
        "steps": steps,
        "ok": False,
        "reference": "",
        "deep_link": "",
    }

    # --- preconditions
    if settings.odoo_mode == "fixture":
        step(
            "preconditions",
            True,
            "Fixture mode — this exercises the canary flow against the simulator, "
            "not production. Point ODOO_* at the real instance for the true canary.",
        )
    elif not dry_run and not settings.odoo_writes_enabled:
        step(
            "preconditions",
            False,
            "The global kill switch is off (ODOO_WRITES_ENABLED=false). Enable it "
            "for the canary run, or run the dry-run variant.",
        )
        return result
    else:
        step("preconditions", True, "Live instance, writes enabled." if not dry_run else "Dry run.")

    product = db.scalar(
        select(Product)
        .where(
            Product.source == "odoo",
            Product.is_stock_tracked.is_(True),
            Product.odoo_product_id.is_not(None),
            Product.is_active.is_(True),
        )
        .order_by(Product.id)
    )
    if product is None:
        step("pick product", False, "No synced, stock-tracked product available — run a product sync first.")
        return result
    step("pick product", True, f"{product.global_sku} — {product.name} (qty 1)")

    reference = f"{CANARY_REF_PREFIX}{utcnow():%Y%m%d-%H%M%S}"
    writer = OdooWriter(
        db,
        settings,
        conn=conn or get_connection(settings, read_only=False),
        actor_user_id=actor_user_id,
    )

    # --- create
    try:
        created = writer.create_internal_transfer(
            source_key="bwhse",
            dest_key="floor",
            lines=[{"product_id": product.id, "qty": 1}],
            note="Canary test record created by the ops app — safe to delete.",
            reference=reference,
            dry_run=dry_run,
            ignore_feature_flag=True,  # the whole point: flag stays off until this passes
        )
    except (WriterValidationError, OdooError) as e:
        step("create draft", False, str(e))
        return result
    result["reference"] = created.reference
    if created.dry_run:
        step(
            "create draft",
            True,
            f"[dry run — {created.dry_run_reason}] payload rendered for {len(created.payload.get('move_ids', []))} line(s)",
        )
        result["payload"] = created.payload
        result["ok"] = True
        return result
    step("create draft", True, f"created stock.picking {created.record_ids} ({created.message})")

    # --- read back and verify draft state
    picking_id = created.record_ids[0]
    try:
        rows = writer.conn.call_kw("stock.picking", "read", [[picking_id], ["state", "origin", "name"]])
        rec = rows[0] if rows else {}
        ok = rec.get("state") == "draft" and rec.get("origin") == reference
        step(
            "read back",
            ok,
            f"state={rec.get('state')!r} origin={rec.get('origin')!r} name={rec.get('name')!r}",
        )
        if not ok:
            return result
    except OdooError as e:
        step("read back", False, str(e))
        return result

    # --- deep link
    result["deep_link"] = created.deep_link
    step("deep link", bool(created.deep_link), created.deep_link)

    # --- cleanup
    try:
        removed = writer.unlink_app_record("stock.picking", picking_id)
        step("unlink", removed.success and not removed.dry_run, removed.message)
        result["ok"] = removed.success and not removed.dry_run
    except (WriterValidationError, OdooError) as e:
        step("unlink", False, f"{e} — the APP-TEST- draft is inert; delete it in Odoo.")
        return result

    return result
