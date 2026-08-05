from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from .auth.deps import AuthedUser, get_current_user
from .config import Settings, get_settings
from .db import get_db
from .sync.status import health_payload

router = APIRouter(tags=["health"])


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    """Public liveness probe — deliberately thin. Load balancers, the compose
    healthcheck and Render need "is this process up and is the DB reachable",
    nothing more.

    The full payload used to live here, which told any anonymous caller whether
    this stack is pointed at live Odoo and whether writes are enabled: free
    reconnaissance for anyone deciding whether to keep poking. It moved to
    /health/detail behind a session (see below).
    """
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "down", "db": db_ok}


@router.get("/health/detail")
def health_detail(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _authed: AuthedUser = Depends(get_current_user),
) -> dict:
    """App + sync health for signed-in users (any role): snapshot age and
    staleness reported honestly, plus Odoo mode / write posture. No
    credentials, no PII — but not for anonymous callers either."""
    return health_payload(db, settings)
