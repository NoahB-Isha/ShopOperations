"""The skubot-facing API — key-authenticated, read-only, lean JSON.

skubot (the WhatsApp lookup bot) is a machine client, not a person: it
authenticates with `X-API-Key` matching SKUBOT_API_KEY instead of a user
session. Blank key = the whole surface is off (503, so the bot's error
message says why). Phase 6 will grow this router; keep it read-only until
then — anything that changes state must go through user-authenticated flows.
"""
from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_db
from ..models import utcnow
from .service import OOS_SCOPES, coming_soon_items, oos_items, snapshot_freshness


def require_bot_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    settings: Settings = Depends(get_settings),
) -> None:
    if not settings.skubot_api_key:
        raise HTTPException(503, "bot API not configured (set SKUBOT_API_KEY)")
    if not x_api_key or not hmac.compare_digest(x_api_key, settings.skubot_api_key):
        raise HTTPException(401, "invalid API key")


router = APIRouter(prefix="/bot", tags=["bot"], dependencies=[Depends(require_bot_key)])


def _payload(items: list, freshness: dict) -> dict:
    return {
        "generated_at": utcnow().isoformat(),
        "snapshot_freshness": freshness,
        "count": len(items),
        "items": [i.as_dict() for i in items],
    }


@router.get("/oos")
def bot_oos(
    scope: str = Query("org"),
    category: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    if scope not in OOS_SCOPES:
        raise HTTPException(422, f"scope must be one of {', '.join(OOS_SCOPES)}")
    items = oos_items(db, settings, scope=scope, category=category, q=q)
    return _payload(items, snapshot_freshness(db))


@router.get("/coming-soon")
def bot_coming_soon(
    category: str | None = None,
    q: str | None = None,
    within_days: int | None = Query(None, ge=1, le=365),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    items = coming_soon_items(db, settings, category=category, q=q, within_days=within_days)
    return _payload(items, snapshot_freshness(db))


@router.get("/health")
def bot_health() -> dict:
    """Connectivity check for the bot — reaching this means the key works."""
    return {"ok": True, "time": utcnow().isoformat()}
