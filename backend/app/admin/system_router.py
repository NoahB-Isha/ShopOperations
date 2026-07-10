"""Admin: sync status & triggers, feature flags, audit log, canary, imports."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth.deps import AuthedUser, require_roles
from ..centers.importer import run_import
from ..config import Settings, get_settings
from ..db import get_db
from ..models import SYNC_DOMAINS, FeatureFlag, OdooWriteAudit, Role
from ..odoo.canary import run_canary_create_internal_transfer
from ..odoo.connection import get_connection
from ..odoo.contract import check_contract
from ..sync.runner import run_all, run_domain
from ..sync.status import domain_statuses, health_payload, recent_runs

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
        "flags": [
            {"key": f.key, "enabled": f.enabled, "description": f.description} for f in flags
        ],
    }


@router.post("/sync/{domain}")
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
def audit_log(limit: int = 100, db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(
        select(OdooWriteAudit).order_by(OdooWriteAudit.id.desc()).limit(min(limit, 500))
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
    path = settings.coordinator_xlsx_path
    try:
        report = run_import(db, path, apply=body.apply, create_users=body.create_users)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    return report.to_dict()
