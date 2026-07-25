"""In-process Odoo simulator backed by recorded/generated fixture files.

Implements just enough of the handful of models the app touches that the
whole stack — sync, writer, canary, integration tests — runs identically
with zero credentials: `create` returns an id, subsequent reads reflect the
write, drafts behave like drafts, `unlink` removes.

Fixture layout: one JSON file per model in a directory, named exactly like
the model (`product.product.json` = a list of record dicts, many2one values
as `[id, display_name]`). An optional `_schema.json` maps model -> field
names so `fields_get` stays meaningful for empty tables.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .errors import OdooError, OdooWriteNotPermitted
from .protocol import READ_METHODS, WRITE_METHODS

# many2one fields the app filters through with dotted domains
RELATIONS: dict[tuple[str, str], str] = {
    ("pos.order.line", "order_id"): "pos.order",
    ("sale.order.line", "order_id"): "sale.order",
    ("stock.move", "picking_id"): "stock.picking",
    ("stock.quant", "location_id"): "stock.location",
}

# one2many creation commands the app uses ((parent, field) -> (child, backref))
ONE2MANY: dict[tuple[str, str], tuple[str, str]] = {
    ("stock.picking", "move_ids"): ("stock.move", "picking_id"),
}


class OdooSimulator:
    mode = "fixture"

    def __init__(self, fixtures_dir: Path, read_only: bool = True):
        self.read_only = read_only
        self.fixtures_dir = Path(fixtures_dir)
        self.tables: dict[str, list[dict]] = {}
        self.schema: dict[str, list[str]] = {}
        self._load()

    # ------------------------------------------------------------- loading
    def _load(self) -> None:
        if not self.fixtures_dir.is_dir():
            raise OdooError(
                f"Odoo fixtures not found at {self.fixtures_dir}. "
                "Run `make fixtures` (or `make seed`) to generate the demo set."
            )
        for f in sorted(self.fixtures_dir.glob("*.json")):
            if f.name == "_schema.json":
                self.schema = json.loads(f.read_text())
                continue
            self.tables[f.stem] = json.loads(f.read_text())

    def authenticate(self) -> int:
        return 999  # simulator session

    # ------------------------------------------------------------- dispatch
    def call_kw(
        self, model: str, method: str, args: list | None = None, kwargs: dict | None = None
    ) -> Any:
        args, kwargs = args or [], kwargs or {}
        if method in WRITE_METHODS and self.read_only:
            raise OdooWriteNotPermitted(
                f"{model}.{method} attempted on a read-only connection. "
                "All writes must go through OdooWriter."
            )
        if method not in READ_METHODS | WRITE_METHODS:
            raise OdooWriteNotPermitted(f"Method '{method}' is not on the app's allow-list.")
        handler = getattr(self, f"_{method}", None)
        if handler is None:
            raise OdooError(f"Simulator does not implement '{method}'.")
        return handler(model, *args, **kwargs)

    # ------------------------------------------------------------- reads
    def search_read(
        self, model: str, domain: list, fields: list[str], order: str | None = None
    ) -> list[dict]:
        return self._search_read(model, domain, fields=fields, order=order)

    def fields_get(self, model: str) -> dict[str, dict]:
        return self._fields_get(model)

    def search_count(self, model: str, domain: list) -> int:
        return self._search_count(model, domain)

    def _rows(self, model: str) -> list[dict]:
        return self.tables.setdefault(model, [])

    def _search_read(
        self,
        model: str,
        domain: list | None = None,
        fields: list[str] | None = None,
        limit: int | None = None,
        offset: int = 0,
        order: str | None = None,
        context: dict | None = None,
    ) -> list[dict]:
        # qty_available reads (the time-machine backfill's as-of query) are
        # computed from the quant table; the simulator has no move ledger, so
        # any `to_date` serves CURRENT state — documented, good enough for
        # demos and tests.
        if model == "product.product" and any(
            isinstance(t, list | tuple) and t and t[0] == "qty_available" for t in (domain or [])
        ):
            rows = self._qty_available_rows(context)
            rows = [r for r in rows if self._match(model, r, domain or [])]
        else:
            rows = [r for r in self._rows(model) if self._match(model, r, domain or [])]
        rows = self._order(rows, order)
        rows = rows[offset : offset + limit if limit else None]
        if not fields:
            return [dict(r) for r in rows]
        keep = set(fields) | {"id"}
        return [{k: r.get(k, False) for k in keep} for r in rows]

    def _qty_available_rows(self, context: dict | None) -> list[dict]:
        """Per-product on-hand from stock.quant, optionally scoped to the
        `location` context (subtree, like Odoo)."""
        loc_id = (context or {}).get("location")
        loc_names = {
            r.get("id"): str(r.get("complete_name") or "") for r in self._rows("stock.location")
        }
        root_name = loc_names.get(loc_id, "")
        totals: dict[int, float] = {}
        for q in self._rows("stock.quant"):
            if loc_id:
                q_loc = q.get("location_id")
                q_id = self._m2o_id(q_loc)
                q_name = loc_names.get(q_id) or (
                    str(q_loc[1]) if isinstance(q_loc, list | tuple) and len(q_loc) == 2 else ""
                )
                if not (
                    q_id == loc_id
                    or (root_name and (q_name == root_name or q_name.startswith(root_name + "/")))
                ):
                    continue
            pid = self._m2o_id(q.get("product_id"))
            if isinstance(pid, int):
                totals[pid] = totals.get(pid, 0.0) + float(q.get("quantity") or 0.0)
        return [{"id": pid, "qty_available": qty} for pid, qty in totals.items()]

    def _read(self, model: str, ids: list[int], fields: list[str] | None = None) -> list[dict]:
        ids = ids if isinstance(ids, list) else [ids]
        rows = [r for r in self._rows(model) if r.get("id") in ids]
        if not fields:
            return [dict(r) for r in rows]
        keep = set(fields) | {"id"}
        return [{k: r.get(k, False) for k in keep} for r in rows]

    def _search(self, model: str, domain: list | None = None, **kw) -> list[int]:
        return [r["id"] for r in self._search_read(model, domain, fields=["id"], **kw)]

    def _search_count(
        self, model: str, domain: list | None = None, context: dict | None = None
    ) -> int:
        return len([r for r in self._rows(model) if self._match(model, r, domain or [])])

    def _fields_get(self, model: str, **kw) -> dict[str, dict]:
        names: set[str] = set(self.schema.get(model, []))
        for r in self._rows(model):
            names.update(r.keys())
        return {n: {"type": "char", "string": n} for n in sorted(names)}

    # ------------------------------------------------------------- writes
    def _next_id(self, model: str) -> int:
        return max((r.get("id", 0) for r in self._rows(model)), default=0) + 1

    def _create(self, model: str, vals: dict | list[dict]) -> int | list[int]:
        if isinstance(vals, list):
            return [self._create_one(model, v) for v in vals]
        return self._create_one(model, vals)

    def _create_one(self, model: str, vals: dict) -> int:
        rec = dict(vals)
        rec["id"] = self._next_id(model)
        children: list[tuple[str, str, list]] = []
        for key, value in list(rec.items()):
            child = ONE2MANY.get((model, key))
            if child and isinstance(value, list):
                children.append((*child, value))
                del rec[key]
        if model == "stock.picking":
            rec.setdefault("name", f"III/INT/{rec['id']:05d}")
            rec.setdefault("state", "draft")
        if model == "stock.move":
            rec.setdefault("state", "draft")
        self._rows(model).append(rec)
        for child_model, backref, commands in children:
            for cmd in commands:
                if not (isinstance(cmd, list | tuple) and len(cmd) == 3 and cmd[0] == 0):
                    raise OdooError(f"Simulator only supports (0,0,vals) commands, got {cmd!r}")
                child_vals = dict(cmd[2])
                child_vals[backref] = [rec["id"], rec.get("name", str(rec["id"]))]
                self._create_one(child_model, child_vals)
        return int(rec["id"])

    def _write(self, model: str, ids: list[int], vals: dict) -> bool:
        for r in self._rows(model):
            if r.get("id") in ids:
                r.update(vals)
        return True

    def _copy(self, model: str, ids: list[int] | int, default: dict | None = None) -> list[int]:
        """Duplicate records like Odoo's copy(): fresh ids, defaults applied,
        pickings reset to draft with their moves duplicated along."""
        ids = ids if isinstance(ids, list) else [ids]
        new_ids: list[int] = []
        for rid in ids:
            src = next((r for r in self._rows(model) if r.get("id") == rid), None)
            if src is None:
                raise OdooError(f"{model} #{rid} not found (copy).")
            rec = dict(src)
            rec["id"] = self._next_id(model)
            rec.update(default or {})
            if model == "stock.picking":
                rec["name"] = f"III/INT/{rec['id']:05d}"
                rec["state"] = "draft"
            self._rows(model).append(rec)
            new_ids.append(int(rec["id"]))
            for (parent, _field), (child, backref) in ONE2MANY.items():
                if parent != model:
                    continue
                for child_row in [
                    c for c in self._rows(child) if self._m2o_id(c.get(backref)) == rid
                ]:
                    dup = dict(child_row)
                    dup["id"] = self._next_id(child)
                    dup[backref] = [rec["id"], rec.get("name", str(rec["id"]))]
                    if child == "stock.move":
                        dup["state"] = "draft"
                    self._rows(child).append(dup)
        return new_ids

    def _action_confirm(self, model: str, ids: list[int]) -> bool:
        """Mark To Do — like Odoo, draft pickings become confirmed."""
        if model != "stock.picking":
            raise OdooError(f"Simulator only confirms stock.picking, not {model}.")
        for r in self._rows(model):
            if r.get("id") in ids and r.get("state") in (None, False, "draft"):
                r["state"] = "confirmed"
        return True

    def _action_assign(self, model: str, ids: list[int]) -> bool:
        """Check availability — confirmed pickings become assigned (ready)."""
        if model != "stock.picking":
            raise OdooError(f"Simulator only assigns stock.picking, not {model}.")
        for r in self._rows(model):
            if r.get("id") in ids and r.get("state") in ("draft", "confirmed", "waiting"):
                r["state"] = "assigned"
        return True

    def _unlink(self, model: str, ids: list[int]) -> bool:
        ids = ids if isinstance(ids, list) else [ids]
        self.tables[model] = [r for r in self._rows(model) if r.get("id") not in ids]
        # cascade one2many children like Odoo does (picking -> moves)
        for (parent, _field), (child, backref) in ONE2MANY.items():
            if parent == model:
                self.tables[child] = [
                    r
                    for r in self._rows(child)
                    if self._m2o_id(r.get(backref)) not in ids
                ]
        return True

    # ------------------------------------------------------------- matching
    @staticmethod
    def _m2o_id(value: Any) -> Any:
        """[id, display] -> id; scalars pass through."""
        if isinstance(value, list | tuple) and len(value) == 2:
            return value[0]
        return value

    def _resolve(self, model: str, rec: dict, field: str) -> Any:
        """Resolve possibly-dotted field paths through the relation registry."""
        if "." not in field:
            return rec.get(field)
        head, rest = field.split(".", 1)
        related_model = RELATIONS.get((model, head))
        if related_model is None:
            raise OdooError(
                f"Simulator has no relation registered for {model}.{head} — add it to RELATIONS."
            )
        rid = self._m2o_id(rec.get(head))
        for r in self._rows(related_model):
            if r.get("id") == rid:
                return self._resolve(related_model, r, rest)
        return None

    def _match(self, model: str, rec: dict, domain: list) -> bool:
        for term in domain:
            if isinstance(term, str):
                raise OdooError(
                    f"Simulator supports AND-only domains; got operator {term!r}. "
                    "Restructure the query or extend the simulator."
                )
            field, op, expected = term
            if op == "child_of":
                if not self._child_of(model, rec, field, expected):
                    return False
                continue
            actual = self._resolve(model, rec, field)
            scalar = self._m2o_id(actual)
            if not self._compare(scalar, op, expected):
                return False
        return True

    def _child_of(self, model: str, rec: dict, field: str, expected) -> bool:
        """Subtree membership like Odoo's child_of, resolved by complete_name
        prefix (fixture locations carry their full path as the display name)."""
        roots = list(expected) if isinstance(expected, list | tuple) else [expected]
        value = rec.get(field)
        value_id = self._m2o_id(value)
        if value_id in roots:
            return True
        related = RELATIONS.get((model, field))

        def name_of(rid) -> str:
            if related is None:
                return ""
            for r in self._rows(related):
                if r.get("id") == rid:
                    return str(r.get("complete_name") or r.get("name") or "")
            return ""

        value_name = name_of(value_id)
        if not value_name and isinstance(value, list | tuple) and len(value) == 2:
            value_name = str(value[1] or "")
        for rid in roots:
            root_name = name_of(rid)
            if root_name and (value_name == root_name or value_name.startswith(root_name + "/")):
                return True
        return False

    @staticmethod
    def _compare(actual: Any, op: str, expected: Any) -> bool:
        if op == "=":
            return actual == expected
        if op == "!=":
            return actual != expected
        if op == "in":
            return actual in expected
        if op == "not in":
            return actual not in expected
        if op in (">=", "<=", ">", "<"):
            if actual is None or actual is False:
                return False
            return {
                ">=": actual >= expected,
                "<=": actual <= expected,
                ">": actual > expected,
                "<": actual < expected,
            }[op]
        if op in ("ilike", "like"):
            # Odoo's (i)like supports % wildcards inside the pattern
            hay = str(actual or "").lower()
            pattern = str(expected).lower()
            if "%" in pattern:
                regex = ".*".join(re.escape(part) for part in pattern.split("%"))
                return re.search(regex, hay) is not None
            return pattern in hay
        raise OdooError(f"Simulator does not support domain operator {op!r}.")

    @staticmethod
    def _order(rows: list[dict], order: str | None) -> list[dict]:
        if not order:
            return rows
        parts = order.split()
        field = parts[0]
        reverse = len(parts) > 1 and parts[1].lower() == "desc"
        return sorted(rows, key=lambda r: (r.get(field) is None, r.get(field)), reverse=reverse)
