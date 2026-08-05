"""Live Odoo 19 client over the web-session JSON endpoints.

This instance has no External API, so we authenticate exactly like the web
client (POST /web/session/authenticate -> session cookie) and call
/web/dataset/call_kw — the approach proven by the `ops` and `skubot` projects.

Politeness: paged reads, a throttle between calls, generous timeouts.
Credentials live in memory only — never logged, never in URLs.
Read-only by default; the sole holder of a writable client is OdooWriter.
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from ..config import Settings
from .errors import (
    OdooAuthError,
    OdooError,
    OdooUnavailable,
    OdooWriteNotPermitted,
    extract_error_message,
    is_auth_failure,
    is_session_expired,
)
from .protocol import READ_METHODS, WRITE_METHODS


class OdooClient:
    mode = "live"

    def __init__(self, settings: Settings, read_only: bool = True):
        if not settings.odoo_configured:
            raise OdooError("Odoo credentials are not configured (ODOO_* env vars).")
        self.base_url = settings.odoo_base_url.rstrip("/")
        self._db = settings.odoo_db
        self._login = settings.odoo_login
        self._password = settings.odoo_password.get_secret_value()  # memory only
        self.read_only = read_only
        self.page_size = settings.odoo_page_size
        self.throttle = settings.odoo_throttle_seconds
        self._http = httpx.Client(timeout=settings.odoo_timeout_seconds)
        self._uid: int | None = None

    # ------------------------------------------------------------- plumbing
    def _post(self, path: str, params: dict) -> Any:
        payload = {"jsonrpc": "2.0", "method": "call", "params": params}
        try:
            resp = self._http.post(f"{self.base_url}{path}", json=payload)
        except httpx.HTTPError as e:
            raise OdooUnavailable(f"Odoo unreachable: {e.__class__.__name__}") from e
        try:
            data = resp.json()
        except ValueError as e:
            raise OdooUnavailable(
                f"Non-JSON response from Odoo (HTTP {resp.status_code}); "
                "a proxy/WAF may be interfering."
            ) from e
        if data.get("error"):
            err = data["error"]
            if is_session_expired(err):
                raise _SessionExpired()
            msg = extract_error_message(err)
            if is_auth_failure(err):
                raise OdooAuthError(msg)
            raise OdooError(msg)
        return data.get("result")

    def authenticate(self) -> int:
        result = self._post(
            "/web/session/authenticate",
            {"db": self._db, "login": self._login, "password": self._password},
        )
        uid = (result or {}).get("uid")
        if not uid:
            raise OdooAuthError(
                "Odoo auth returned no uid — check ODOO_DB/login/password, and note "
                "that 2FA must be disabled on the app's account."
            )
        self._uid = uid
        return uid

    def call_kw(
        self, model: str, method: str, args: list | None = None, kwargs: dict | None = None
    ) -> Any:
        if method in WRITE_METHODS and self.read_only:
            raise OdooWriteNotPermitted(
                f"{model}.{method} attempted on a read-only connection. "
                "All writes must go through OdooWriter."
            )
        if method not in READ_METHODS | WRITE_METHODS:
            raise OdooWriteNotPermitted(f"Method '{method}' is not on the app's allow-list.")
        if self._uid is None:
            self.authenticate()
        params = {"model": model, "method": method, "args": args or [], "kwargs": kwargs or {}}
        time.sleep(self.throttle)
        try:
            return self._post(f"/web/dataset/call_kw/{model}/{method}", params)
        except _SessionExpired:
            # one silent re-auth, then retry once
            self.authenticate()
            return self._post(f"/web/dataset/call_kw/{model}/{method}", params)

    # ------------------------------------------------------------- reads
    def search_read(
        self, model: str, domain: list, fields: list[str], order: str | None = None
    ) -> list[dict]:
        out: list[dict] = []
        offset = 0
        while True:
            kwargs: dict = {"fields": fields, "limit": self.page_size, "offset": offset}
            if order:
                kwargs["order"] = order
            chunk = self.call_kw(model, "search_read", [domain], kwargs) or []
            out.extend(chunk)
            if len(chunk) < self.page_size:
                return out
            offset += self.page_size

    def fields_get(self, model: str) -> dict[str, dict]:
        return self.call_kw(model, "fields_get", [], {"attributes": ["type", "string"]}) or {}

    def search_count(self, model: str, domain: list) -> int:
        return self.call_kw(model, "search_count", [domain]) or 0


class _SessionExpired(Exception):
    pass
