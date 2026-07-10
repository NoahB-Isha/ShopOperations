from __future__ import annotations


class OdooError(RuntimeError):
    """Base for anything the Odoo boundary raises."""


class OdooAuthError(OdooError):
    """Bad credentials, expired password, or 2FA tripped on the shared account.
    Sync surfaces this loudly on the admin status page — it never fails silently."""


class OdooUnavailable(OdooError):
    """Network / server trouble. Sync keeps the last good snapshot."""


class OdooWriteNotPermitted(OdooError):
    """A write-style method reached a read-only connection. This is a bug:
    all writes must go through OdooWriter."""


class OdooWriteError(OdooError):
    """A write operation failed inside Odoo (validation error, missing record…)."""


def extract_error_message(err: dict) -> str:
    """Odoo's top-level message is often the generic 'Odoo Server Error'; the
    useful detail (AccessDenied, wrong db…) hides in error.data."""
    data = err.get("data") or {}
    parts = [err.get("message") or "", data.get("name") or "", data.get("message") or ""]
    seen: set[str] = set()
    out = []
    for p in parts:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return " | ".join(out) or str(err)


def is_session_expired(err: dict) -> bool:
    if err.get("code") == 100:
        return True
    name = str((err.get("data") or {}).get("name") or "")
    return "SessionExpired" in name


def is_auth_failure(err: dict) -> bool:
    msg = extract_error_message(err).lower()
    return any(s in msg for s in ("access denied", "accessdenied", "wrong login", "password"))
