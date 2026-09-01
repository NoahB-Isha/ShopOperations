"""Admin: sync status & triggers, feature flags, audit log, canary, imports."""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth.deps import AuthedUser, require_roles
from ..centers.importer import run_import
from ..config import Settings, get_settings
from ..db import get_db
from ..ingestion.sources import registry_status
from ..models import (
    SYNC_DOMAINS,
    FeatureFlag,
    OdooWriteAudit,
    Role,
    SyncState,
)
from ..notify import service as notify_service
from ..odoo.canary import run_canary_create_internal_transfer
from ..odoo.connection import get_connection
from ..odoo.contract import check_contract
from ..ratelimit import rate_limit
from ..sync.runner import run_all, run_domain
from ..sync.status import domain_statuses, health_payload, recent_runs
from ..transfers.delivery import DeliveryError, release_stale_counts
from ..transfers.reset import ResetError, reset_delivery_flow

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_roles(Role.ADMIN))],
)


@router.get("/status")
def status(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    flags = db.scalars(select(FeatureFlag)).all()
    return {
        **health_payload(db, settings),
        "auth_mode": settings.auth_mode,
        "odoo_base_url": settings.odoo_base_url or None,  # never the credentials
        "recent_runs": recent_runs(db),
        "notifications": notify_service.channels_payload(db, settings),
        "flags": [
            {"key": f.key, "enabled": f.enabled, "description": f.description} for f in flags
        ],
        "ingestion_sources": registry_status(),
    }


@router.get("/notifications")
def list_notifications(
    limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db)
) -> list[dict]:
    """The outbox, newest first — who was (or would have been) told what."""
    return notify_service.recent_notifications(db, limit)


# Heavy Odoo pulls: a slow ceiling that still lets an operator retry.
@router.post(  # BEFORE /sync/{domain} — route order matters
    "/sync/sales/rebuild",
    dependencies=[Depends(rate_limit("admin:sync-rebuild", limit=3, per_seconds=3600))],
)
def rebuild_sales_history(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Clear the backfill marker and re-pull the full sales window NOW —
    the one heavy query, run deliberately. This is how pre-Phase-5 rows get
    their channel split and revenue amounts. Explicit admin action only."""
    state = db.get(SyncState, "sales")
    if state is not None:
        extra = dict(state.extra or {})
        extra.pop("backfill_done_at", None)
        extra.pop("prev_month_synced_on", None)
        state.extra = extra
        db.commit()
    run = run_domain(db, settings, "sales", trigger="manual")
    return {"domain": run.domain, "status": run.status, "rows": run.rows, "error": run.error}




class ReleaseStaleCountsIn(BaseModel):
    apply: bool = False  # preview by default — the preview changes nothing


@router.post("/transfers/release-stale-counts")
def release_stale_counts_endpoint(
    body: ReleaseStaleCountsIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(require_roles(Role.ADMIN)),
) -> dict:
    """One-time: hand the requests stranded mid-old-flow back to the delivery
    form (see delivery.release_stale_counts for what that means and what it
    deliberately won't do). Preview first — `apply: true` is the second call.

    An endpoint rather than a script because the affected requests live on the
    hosted stack, which has no shell; a preview rather than a migration
    because deciding it needs a LIVE Odoo read of each count picking, and a
    migration must never depend on Odoo answering."""
    try:
        report = release_stale_counts(
            db, settings, apply=body.apply, actor_user_id=authed.id
        )
    except DeliveryError as e:
        raise HTTPException(422, str(e)) from e
    return {
        "applied": report.applied,
        "released": report.released,
        "skipped": report.skipped,
        "note": report.note,
        "rows": [vars(r) for r in report.rows],
        "cancel_in_odoo": [
            {
                "picking_name": r.count_picking_name,
                "url": r.count_picking_url,
                "state": r.count_state,
                "request": r.display_name,
            }
            for r in report.cancel_in_odoo
        ],
    }


class ResetFlowIn(BaseModel):
    apply: bool = False  # preview by default
    keep_hours: int = Field(24, ge=0, le=720)  # what counts as "recent"


@router.post(
    "/transfers/reset-flow",
    dependencies=[Depends(rate_limit("admin:reset-flow", limit=6, per_seconds=3600))],
)
def reset_transfer_flow(
    body: ResetFlowIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(require_roles(Role.ADMIN)),
) -> dict:
    """One-time: clear the testing rubble and start the delivery process from
    the next pallet. Preview first — `apply: true` DELETES rows (see
    transfers/reset.py for exactly what it will and won't touch)."""
    try:
        report = reset_delivery_flow(
            db,
            settings,
            keep_hours=body.keep_hours,
            apply=body.apply,
            actor_user_id=authed.id,
        )
    except ResetError as e:
        raise HTTPException(422, str(e)) from e
    return {
        "applied": report.applied,
        "keep_hours": report.keep_hours,
        "cutoff": report.cutoff,
        "requests_cleared": report.requests_cleared,
        "requests_kept": report.requests_kept,
        "pallets_cleared": report.pallets_cleared,
        "events_cleared": report.events_cleared,
        "drafts_removed": report.drafts_removed,
        "already_gone": report.already_gone,
        "leftovers": [vars(x) for x in report.leftovers],
        "kept": report.kept,
        "discover_from": report.discover_from,
        "note": report.note,
    }


@router.post(
    "/sync/{domain}",
    dependencies=[Depends(rate_limit("admin:sync", limit=20, per_seconds=300))],
)
def trigger_sync(
    domain: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    if domain == "all":
        runs = run_all(db, settings, trigger="manual")
        return {"runs": [{"domain": r.domain, "status": r.status, "rows": r.rows, "error": r.error} for r in runs]}
    if domain not in SYNC_DOMAINS:
        raise HTTPException(404, f"Unknown domain '{domain}'. Use one of {SYNC_DOMAINS} or 'all'.")
    run = run_domain(db, settings, domain, trigger="manual")
    return {
        "domain": run.domain,
        "status": run.status,
        "rows": run.rows,
        "error": run.error,
        "sync": domain_statuses(db, settings)[domain],
    }


class FlagIn(BaseModel):
    enabled: bool


@router.get("/flags")
def list_flags(db: Session = Depends(get_db)) -> list[dict]:
    return [
        {"key": f.key, "enabled": f.enabled, "description": f.description,
         "updated_at": f.updated_at.isoformat() if f.updated_at else None}
        for f in db.scalars(select(FeatureFlag).order_by(FeatureFlag.key))
    ]


@router.put("/flags/{key}")
def set_flag(
    key: str,
    body: FlagIn,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(require_roles(Role.ADMIN)),
) -> dict:
    flag = db.get(FeatureFlag, key)
    if flag is None:
        raise HTTPException(404, f"No feature flag '{key}'.")
    flag.enabled = body.enabled
    flag.updated_by_id = authed.id
    db.commit()
    return {"key": flag.key, "enabled": flag.enabled}


@router.get("/audit")
def audit_log(
    limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)
) -> list[dict]:
    rows = db.scalars(
        select(OdooWriteAudit).order_by(OdooWriteAudit.id.desc()).limit(limit)
    ).all()
    return [
        {
            "id": r.id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "actor_user_id": r.actor_user_id,
            "operation": r.operation,
            "reference": r.reference,
            "dry_run": r.dry_run,
            "dry_run_reason": r.dry_run_reason,
            "success": r.success,
            "odoo_model": r.odoo_model,
            "odoo_record_ids": r.odoo_record_ids,
            "request_payload": r.request_payload,
            "response": r.response,
            "error": r.error,
            "duration_ms": r.duration_ms,
        }
        for r in rows
    ]


class CanaryIn(BaseModel):
    dry_run: bool = True


@router.post("/odoo/canary/create-internal-transfer")
def canary_create_internal_transfer(
    body: CanaryIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(require_roles(Role.ADMIN)),
) -> dict:
    """The gated canary protocol — explicit admin action only, never automatic."""
    return run_canary_create_internal_transfer(db, settings, authed.id, dry_run=body.dry_run)


@router.get("/odoo/contract")
def contract_check(settings: Settings = Depends(get_settings)) -> dict:
    conn = get_connection(settings, read_only=True)
    return {"mode": conn.mode, "results": check_contract(conn)}


class ImportIn(BaseModel):
    apply: bool = False
    create_users: bool = True


@router.post("/import/coordinators")
def import_coordinators(
    body: ImportIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Re-read the roster file that ships with the deployment.

    Kept for the seeded/local path; the admin UI uploads a file instead (see
    below), because the roster now lives in this app and a spreadsheet is
    something you bring TO it, not something it reads over your shoulder."""
    path = settings.coordinator_xlsx_path
    try:
        report = run_import(db, path, apply=body.apply, create_users=body.create_users)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    return report.to_dict()


@router.post("/import/coordinators/upload")
async def import_coordinators_upload(
    file: UploadFile = File(...),
    apply: bool = Form(False),
    create_users: bool = Form(True),
    db: Session = Depends(get_db),
) -> dict:
    """Import a roster the admin picked off their own machine (.xlsx or .csv).

    Written to a temp file because the parsers want a path, and deleted in a
    finally — a roster carries every coordinator's phone number and has no
    business lingering in the container's filesystem.
    """
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".xlsx", ".xlsm", ".csv"):
        raise HTTPException(422, "Upload a .xlsx or .csv roster.")
    payload = await file.read()
    if len(payload) > 8 * 1024 * 1024:
        raise HTTPException(413, "That file is larger than 8MB — is it the right one?")
    tmp = Path(tempfile.mkdtemp()) / f"roster{suffix}"
    try:
        tmp.write_bytes(payload)
        report = run_import(db, tmp, apply=apply, create_users=create_users)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    finally:
        tmp.unlink(missing_ok=True)
        tmp.parent.rmdir()
    return report.to_dict()
