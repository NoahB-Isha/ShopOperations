"""Freshness/staleness reporting for the health endpoint and admin status page."""
from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import SYNC_DOMAINS, SyncRun, SyncState, utcnow


def _age_seconds(dt) -> float | None:
    if dt is None:
        return None
    now = utcnow()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=now.tzinfo)
    return (now - dt).total_seconds()


def domain_statuses(db: Session, settings: Settings) -> dict[str, dict]:
    states = {s.domain: s for s in db.scalars(select(SyncState))}
    out: dict[str, dict] = {}
    for domain in SYNC_DOMAINS:
        state = states.get(domain)
        interval_s = settings.sync_interval_minutes(domain) * 60
        age = _age_seconds(state.last_success_at) if state else None
        stale = age is None or age > interval_s * settings.sync_stale_factor
        out[domain] = {
            "last_success_at": state.last_success_at.isoformat() if state and state.last_success_at else None,
            "last_attempt_at": state.last_attempt_at.isoformat() if state and state.last_attempt_at else None,
            "age_seconds": round(age) if age is not None else None,
            "stale": stale,
            "interval_minutes": settings.sync_interval_minutes(domain),
            "last_error": state.last_error if state else "never synced",
            "auth_failed": bool(state.auth_failed) if state else False,
            "extra": (state.extra or {}) if state else {},
        }
    return out


def health_payload(db: Session, settings: Settings) -> dict:
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    domains = domain_statuses(db, settings) if db_ok else {}
    any_stale = any(d["stale"] for d in domains.values()) if domains else True
    auth_failed = any(d["auth_failed"] for d in domains.values()) if domains else False
    status = "ok"
    if not db_ok:
        status = "down"
    elif auth_failed or any_stale:
        status = "degraded"
    return {
        "status": status,
        "db": db_ok,
        "odoo_mode": settings.odoo_mode,
        "writes_enabled": settings.odoo_writes_enabled,
        "odoo_auth_failed": auth_failed,
        "sync": domains,
    }


def recent_runs(db: Session, limit: int = 40) -> list[dict]:
    runs = db.scalars(select(SyncRun).order_by(SyncRun.id.desc()).limit(limit)).all()
    return [
        {
            "id": r.id,
            "domain": r.domain,
            "trigger": r.trigger,
            "status": r.status,
            "rows": r.rows,
            "source": r.source,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "error": r.error,
        }
        for r in runs
    ]
