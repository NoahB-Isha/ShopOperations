"""Tiny in-process rate limiter.

Deliberately not distributed: one dict per process, fixed sliding windows. The
API runs as a single uvicorn process behind a tunnel, so this is as accurate as
any in-memory library and adds no dependency. If the API ever runs multiple
processes, move the counters to Postgres/Redis — the call sites don't change.

Two keying strategies, because only one of them is trustworthy everywhere:
  * authenticated endpoints key on the USER ID (`rate_limit`), which is exact.
  * unauthenticated endpoints key on the client IP (`client_key`) AND on the
    submitted identifier. Behind a reverse proxy the IP is only real when
    uvicorn runs with --proxy-headers --forwarded-allow-ips=<proxy>; the
    identifier key keeps the limit meaningful either way.
"""
from __future__ import annotations

import threading
import time
from collections import deque

from fastapi import Depends, HTTPException, Request

from .auth.deps import AuthedUser, get_current_user
from .config import Settings, get_settings

_LOCK = threading.Lock()
_HITS: dict[str, deque[float]] = {}
# Cap on distinct keys so a spray of unique identifiers can't grow the dict
# without bound; idle keys are dropped first.
_MAX_KEYS = 20_000


def _hit(key: str, limit: int, per_seconds: int) -> int | None:
    """Record a call. None when allowed, else seconds until the window frees up."""
    now = time.monotonic()
    with _LOCK:
        window = _HITS.setdefault(key, deque())
        while window and now - window[0] >= per_seconds:
            window.popleft()
        if len(window) >= limit:
            return max(1, int(per_seconds - (now - window[0])))
        window.append(now)
        if len(_HITS) > _MAX_KEYS:
            for stale in [k for k, w in _HITS.items() if not w or now - w[-1] > per_seconds]:
                _HITS.pop(stale, None)
    return None


def client_key(request: Request) -> str:
    """Caller address. See the module docstring on proxy headers."""
    return request.client.host if request.client else "unknown"


def enforce(
    settings: Settings, bucket: str, subject: str, *, limit: int, per_seconds: int
) -> None:
    """Raise 429 when `subject` has exceeded `limit` calls to `bucket`."""
    if not settings.rate_limit_enabled:
        return
    retry_after = _hit(f"{bucket}|{subject}", limit, per_seconds)
    if retry_after is not None:
        raise HTTPException(
            429,
            "Too many requests. Wait a moment and try again.",
            headers={"Retry-After": str(retry_after)},
        )


def rate_limit(bucket: str, *, limit: int, per_seconds: int):
    """Dependency for AUTHENTICATED endpoints; keyed on the user id, so it holds
    whether or not proxy headers are configured. get_current_user is already a
    dependency on these routes, so FastAPI reuses the cached lookup."""

    def guard(
        authed: AuthedUser = Depends(get_current_user),
        settings: Settings = Depends(get_settings),
    ) -> None:
        enforce(settings, bucket, str(authed.id), limit=limit, per_seconds=per_seconds)

    return guard


def enforce_login_limits(settings: Settings, request: Request, identifier: str) -> None:
    """Ceilings shared by the unauthenticated auth endpoints: per-IP to blunt
    sweeps, per-identifier so one NAT'd city center can't lock out its
    neighbours and one account can't be flooded with codes.

    Dev-auth servers are exempt: they deliver nothing (the code renders on
    screen), compose binds them to loopback only, and the e2e suite re-logs the
    same demo users in tight loops."""
    if settings.dev_auth:
        return
    enforce(settings, "auth:ip", client_key(request), limit=30, per_seconds=300)
    enforce(settings, "auth:identifier", identifier.strip().lower(), limit=5, per_seconds=600)


def reset_for_tests() -> None:
    """Drop all counters (the test client shares one process across tests)."""
    with _LOCK:
        _HITS.clear()
