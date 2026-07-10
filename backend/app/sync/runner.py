"""Sync orchestration with the self-healing guarantee.

Each domain sync stages all its changes on one session; the runner commits on
success and rolls back on ANY failure — so a failed pull can never clobber
the last good snapshot. Failures update only the sync_state bookkeeping
(attempt time, error, auth_failed) and the run row, both committed separately.
"""
from __future__ import annotations

import logging

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import SYNC_DOMAINS, SyncRun, SyncState, utcnow
from ..odoo.connection import get_connection
from ..odoo.errors import OdooAuthError
from ..odoo.protocol import OdooConnection
from .incoming import sync_incoming
from .products import sync_products
from .sales import sync_sales
from .stock import sync_stock

log = logging.getLogger("sync")

SYNCERS = {
    "products": sync_products,
    "stock": sync_stock,
    "sales": sync_sales,
    "incoming": sync_incoming,
}

KEEP_RUNS = 500


def get_or_create_state(db: Session, domain: str) -> SyncState:
    state = db.get(SyncState, domain)
    if state is None:
        state = SyncState(domain=domain)
        db.add(state)
        db.commit()
    return state


def run_domain(
    db: Session,
    settings: Settings,
    domain: str,
    conn: OdooConnection | None = None,
    trigger: str = "scheduled",
) -> SyncRun:
    if domain not in SYNCERS:
        raise ValueError(f"Unknown sync domain '{domain}'.")
    if conn is None:
        conn = get_connection(settings, read_only=True)

    state = get_or_create_state(db, domain)
    state.last_attempt_at = utcnow()
    run = SyncRun(domain=domain, trigger=trigger, source=conn.mode)
    db.add(run)
    db.commit()  # run row + attempt time survive a later rollback
    run_id = run.id

    try:
        rows = SYNCERS[domain](db, settings, conn, state)
        state.last_success_at = utcnow()
        state.last_error = ""
        state.auth_failed = False
        run.status = "success"
        run.rows = rows
        run.finished_at = utcnow()
        db.commit()
        log.info("sync %s ok: %s rows (%s)", domain, rows, conn.mode)
    except Exception as e:  # noqa: BLE001 — every failure must be recorded, none may clobber
        db.rollback()
        state = get_or_create_state(db, domain)
        failed_run = db.get(SyncRun, run_id)
        state.last_error = f"{e.__class__.__name__}: {e}"
        state.auth_failed = isinstance(e, OdooAuthError)
        if failed_run is not None:
            failed_run.status = "failure"
            failed_run.error = state.last_error
            failed_run.finished_at = utcnow()
        db.commit()
        log.error("sync %s FAILED (last good snapshot kept): %s", domain, e)

    _prune_runs(db)
    final = db.get(SyncRun, run_id)
    assert final is not None
    return final


def run_all(db: Session, settings: Settings, trigger: str = "manual") -> list[SyncRun]:
    conn = get_connection(settings, read_only=True)
    # products first so stock/sales can resolve product ids
    return [run_domain(db, settings, d, conn=conn, trigger=trigger) for d in SYNC_DOMAINS]


def _prune_runs(db: Session) -> None:
    ids = db.scalars(select(SyncRun.id).order_by(SyncRun.id.desc()).offset(KEEP_RUNS)).all()
    if ids:
        db.execute(delete(SyncRun).where(SyncRun.id.in_(ids)))
        db.commit()
