"""The connection contract shared by the live client and the simulator, plus
the write-method allow-list both enforce.

Everything above this layer (sync, writer, contract check) talks to an
`OdooConnection` and cannot tell live from fixture — which is exactly how the
app runs demos, tests, and CI without an Odoo instance.
"""
from __future__ import annotations

from typing import Any, Protocol

READ_METHODS = {"search_read", "read", "read_group", "search", "search_count", "fields_get"}
# The only write methods the app may EVER use, and only via OdooWriter.
# copy/action_confirm/action_assign exist for ONE operation: preparing the
# STAGING→FLOOR count transfer (duplicate, mark To Do, check availability).
# They reserve stock but move none — validation stays human, always.
WRITE_METHODS = {"create", "write", "unlink", "copy", "action_confirm", "action_assign"}


class OdooConnection(Protocol):
    mode: str  # "live" | "fixture"
    read_only: bool

    def call_kw(
        self, model: str, method: str, args: list | None = None, kwargs: dict | None = None
    ) -> Any: ...

    def search_read(
        self,
        model: str,
        domain: list,
        fields: list[str],
        order: str | None = None,
    ) -> list[dict]: ...

    def fields_get(self, model: str) -> dict[str, dict]: ...

    def search_count(self, model: str, domain: list) -> int: ...


def safe_fields(conn: OdooConnection, model: str, wanted: list[str]) -> list[str]:
    """Keep only fields the instance actually exposes (Odoo installs are
    customized; never assume)."""
    try:
        have = set(conn.fields_get(model).keys())
    except Exception:
        return wanted
    keep = [f for f in wanted if f in have]
    return keep or ["name"]


def parse_code(display: str | None) -> str:
    """'[CA0023000009] Copper Bottle' -> 'CA0023000009'; plain codes pass through."""
    s = (display or "").strip()
    if s.startswith("[") and "]" in s:
        return s[1 : s.index("]")].strip()
    return s
