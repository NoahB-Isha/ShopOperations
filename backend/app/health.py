from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .db import get_db
from .sync.status import health_payload

router = APIRouter(tags=["health"])


@router.get("/health")
def health(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    """Unauthenticated app + sync health: reports snapshot age and staleness
    honestly (no credentials, no PII)."""
    return health_payload(db, settings)
