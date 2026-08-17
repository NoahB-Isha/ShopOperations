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
from ..models import SYNC_DOMAINS, SyncRun, SyncState, is_due, utcnow
from ..odoo.connection import get_connection
from ..odoo.errors import OdooAuthError
from ..odoo.protocol import OdooConnection
from .incoming import sync_incoming
from .products import sync_products
from .sales import sync_sales
from .stock import sync_stock
from .transfers import sync_transfers

log = logging.getLogger("sync")

SYNCERS = {
    "products": sync_products,
    "stock": sync_stock,
    "sales": sync_sales,
    "incoming": sync_incoming,
    "transfers": sync_transfers,
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


def claim_stale_refresh(db: Session, domain: str, max_age_seconds: int) -> bool:
    """True if `domain` is older than `max_age_seconds` AND this caller just
    claimed the right to refresh it.

    The claim is the point. A page that refreshes its own data on open is the
    only thing keeping the hosted stack current (its worker is switched off),
    but ten phones opening /restock must not fire ten syncs at Odoo. Stamping
    `last_attempt_at` here — before any work starts, committed immediately —
    means the second caller through sees a fresh stamp and declines, the same
    way the transfer pollers claim their throttle.

    Age is measured from the ATTEMPT, not the success: a domain that is
    failing must back off too, not retry on every page open.
    """
    state = get_or_create_state(db, domain)
    now = utcnow()
    if not is_due(state.last_attempt_at, max_age_seconds, now):
        return False
    state.last_attempt_at = now
    db.commit()
    return True


def refresh_domains_in_background(settings: Settings, domains: list[str]) -> None:
    """Run syncs on their own session, for a FastAPI BackgroundTask.

    Deliberately after the response: a stock sync takes seconds against live
    Odoo, and making someone wait for it to see a list they already have is
    worse than showing them the current one and letting the next poll pick up
    the new numbers.
    """
    from ..db import get_sessionmaker

    db = get_sessionmaker()()
    try:
        for domain in domains:
            try:
                run_domain(db, settings, domain, trigger="on-read")
            except Exception as e:  # noqa: BLE001 — a background refresh never breaks a page
                log.warning("on-read refresh of %s failed: %s", domain, e)
    finally:
        db.close()


def run_all(db: Session, settings: Settings, trigger: str = "manual") -> list[SyncRun]:
    conn = get_connection(settings, read_only=True)
    # products first so stock/sales can resolve product ids
    return [run_domain(db, settings, d, conn=conn, trigger=trigger) for d in SYNC_DOMAINS]


def _prune_runs(db: Session) -> None:
    ids = db.scalars(select(SyncRun.id).order_by(SyncRun.id.desc()).offset(KEEP_RUNS)).all()
    if ids:
        db.execute(delete(SyncRun).where(SyncRun.id.in_(ids)))
        db.commit()
