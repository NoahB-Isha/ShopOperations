from __future__ import annotations

import pytest
from app.odoo.errors import OdooError, OdooWriteNotPermitted
from app.odoo.simulator import OdooSimulator


@pytest.fixture()
def sim(settings_env):
    return OdooSimulator(settings_env.fixtures_path, read_only=False)


def test_search_read_filters(sim):
    rows = sim.search_read("product.product", [["default_code", "=", "CA0023000009"]], ["name"])
    assert len(rows) == 2  # duplicate variant fixture included
    rows = sim.search_read("product.product", [["name", "ilike", "toothpaste"]], ["name"])
    assert rows[0]["name"] == "Neem Toothpaste"
    # bwhse, floor, staging + the bwhse bin (subtree-matching fixture)
    assert sim.search_count("stock.location", [["usage", "=", "internal"]]) == 4


def test_dotted_domain_via_relation(sim):
    rows = sim.search_read(
        "pos.order.line", [["order_id.state", "in", ["paid", "done", "invoiced"]]], ["qty"]
    )
    assert {r["id"] for r in rows} == {1, 2, 3, 5}  # draft order's line excluded


def test_or_domains_rejected_loudly(sim):
    with pytest.raises(OdooError, match="AND-only"):
        sim.search_read("product.product", ["|", ["name", "=", "x"], ["name", "=", "y"]], ["name"])


def test_create_read_write_unlink_cycle(sim):
    pid = sim.call_kw(
        "stock.picking",
        "create",
        [{
            "origin": "ILAPP-XFER-TEST01",
            "location_id": 12,
            "location_dest_id": 14,
            "picking_type_id": 5,
            "move_ids": [(0, 0, {"name": "line", "product_id": 201, "product_uom_qty": 5,
                                 "location_id": 12, "location_dest_id": 14})],
        }],
    )
    rec = sim.call_kw("stock.picking", "read", [[pid], ["state", "origin", "name"]])[0]
    assert rec["state"] == "draft"  # drafts behave like Odoo drafts
    assert rec["origin"] == "ILAPP-XFER-TEST01"
    moves = sim.search_read("stock.move", [["picking_id", "=", pid]], ["product_uom_qty"])
    assert len(moves) == 1 and moves[0]["product_uom_qty"] == 5

    sim.call_kw("stock.picking", "unlink", [[pid]])
    assert sim.search_count("stock.picking", [["id", "=", pid]]) == 0
    assert sim.search_count("stock.move", [["picking_id", "=", pid]]) == 0  # cascaded


def test_read_only_simulator_refuses_writes(settings_env):
    sim = OdooSimulator(settings_env.fixtures_path)  # read_only=True default
    with pytest.raises(OdooWriteNotPermitted, match="OdooWriter"):
        sim.call_kw("stock.picking", "create", [{}])
