from __future__ import annotations

import pytest
from app.models import OdooWriteAudit, Product
from app.odoo.operations import TransferLine, build_internal_transfer_payload
from app.odoo.simulator import OdooSimulator
from app.odoo.writer import OdooWriter, WriterValidationError
from app.sync.runner import run_domain
from sqlalchemy import select

from .util import set_flag

FLAG = "write_create_internal_transfer"


def _prepared(db, settings):
    """Products + locations synced; returns (writable simulator, a product)."""
    sim = OdooSimulator(settings.fixtures_path, read_only=False)
    run_domain(db, settings, "products", conn=sim, trigger="manual")
    run_domain(db, settings, "stock", conn=sim, trigger="manual")
    product = db.scalar(select(Product).where(Product.global_sku == "CA0023000009"))
    return sim, product


# ---------------------------------------------------------- layer A: payloads
def test_payload_is_exact():
    payload = build_internal_transfer_payload(
        picking_type_id=5,
        source_location_id=12,
        dest_location_id=14,
        reference="ILAPP-XFER-AAAA",
        lines=[TransferLine(product_odoo_id=201, description="CA0023000009 Copper Bottle", qty=5)],
        move_field="move_ids",
        note="hello",
    )
    assert payload == {
        "picking_type_id": 5,
        "location_id": 12,
        "location_dest_id": 14,
        "origin": "ILAPP-XFER-AAAA",
        "note": "hello",
        "move_ids": [
            (
                0,
                0,
                {
                    "description_picking": "CA0023000009 Copper Bottle",
                    "product_id": 201,
                    "product_uom_qty": 5,
                    "location_id": 12,
                    "location_dest_id": 14,
                },
            )
        ],
    }


# ---------------------------------------------------------- gates
def test_kill_switch_forces_dry_run(db, live_env, monkeypatch):
    monkeypatch.setenv("ODOO_WRITES_ENABLED", "false")
    from app.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    sim, product = _prepared(db, settings)
    set_flag(db, FLAG, True)

    writer = OdooWriter(db, settings, conn=sim)
    baseline = sim.search_count("stock.picking", [])  # fixture's native pickings
    result = writer.create_internal_transfer(
        source_key="bwhse", dest_key="floor", lines=[{"product_id": product.id, "qty": 3}]
    )
    assert result.dry_run and result.dry_run_reason == "kill_switch"
    assert sim.search_count("stock.picking", []) == baseline  # nothing written
    audit = db.scalars(select(OdooWriteAudit)).all()[-1]
    assert audit.dry_run and audit.dry_run_reason == "kill_switch" and audit.success
    assert audit.request_payload["origin"].startswith("ILAPP-XFER-")


def test_feature_flag_off_forces_dry_run(db, live_env):
    sim, product = _prepared(db, live_env)
    set_flag(db, FLAG, False)
    writer = OdooWriter(db, live_env, conn=sim)
    result = writer.create_internal_transfer(
        source_key="bwhse", dest_key="floor", lines=[{"product_id": product.id, "qty": 3}]
    )
    assert result.dry_run and result.dry_run_reason == "feature_flag"


def test_fixture_mode_forces_dry_run(db, settings_env):
    sim, product = _prepared(db, settings_env)
    set_flag(db, FLAG, True)
    writer = OdooWriter(db, settings_env, conn=sim)
    result = writer.create_internal_transfer(
        source_key="bwhse", dest_key="floor", lines=[{"product_id": product.id, "qty": 3}]
    )
    # kill switch (off by default) outranks fixture mode as the reported reason
    assert result.dry_run and result.dry_run_reason == "kill_switch"
    assert result.payload["location_id"] == 12 and result.payload["location_dest_id"] == 14


# ---------------------------------------------------------- live path (layer B)
def test_live_write_creates_draft_with_reference_and_link(db, live_env):
    sim, product = _prepared(db, live_env)
    set_flag(db, FLAG, True)
    writer = OdooWriter(db, live_env, conn=sim, actor_user_id=None)
    result = writer.create_internal_transfer(
        source_key="bwhse",
        dest_key="floor",
        lines=[{"product_id": product.id, "qty": 4}],
        note="restock",
    )
    assert not result.dry_run and result.success
    assert result.reference.startswith("ILAPP-XFER-")
    assert result.record_ids
    pid = result.record_ids[0]
    rec = sim.call_kw("stock.picking", "read", [[pid], ["state", "origin"]])[0]
    assert rec["state"] == "draft"  # never validated by the app
    assert rec["origin"] == result.reference
    assert f"id={pid}" in result.deep_link and "stock.picking" in result.deep_link

    audit = db.scalars(select(OdooWriteAudit).order_by(OdooWriteAudit.id.desc())).first()
    assert audit.success and not audit.dry_run and audit.odoo_record_ids == [pid]


def test_live_write_is_idempotent_on_same_reference(db, live_env):
    sim, product = _prepared(db, live_env)
    set_flag(db, FLAG, True)
    writer = OdooWriter(db, live_env, conn=sim)
    first = writer.create_internal_transfer(
        source_key="bwhse", dest_key="floor",
        lines=[{"product_id": product.id, "qty": 4}], reference="ILAPP-XFER-RETRY1",
    )
    second = writer.create_internal_transfer(
        source_key="bwhse", dest_key="floor",
        lines=[{"product_id": product.id, "qty": 4}], reference="ILAPP-XFER-RETRY1",
    )
    assert first.record_ids == second.record_ids
    assert "idempotent" in second.message
    assert sim.search_count("stock.picking", [["origin", "=", "ILAPP-XFER-RETRY1"]]) == 1


# ---------------------------------------------------------- validation
def test_validation_rejects_bad_input(db, live_env):
    sim, product = _prepared(db, live_env)
    writer = OdooWriter(db, live_env, conn=sim)
    with pytest.raises(WriterValidationError, match="at least one line"):
        writer.create_internal_transfer(source_key="bwhse", dest_key="floor", lines=[])
    with pytest.raises(WriterValidationError, match="positive"):
        writer.create_internal_transfer(
            source_key="bwhse", dest_key="floor", lines=[{"product_id": product.id, "qty": 0}]
        )
    with pytest.raises(WriterValidationError, match="same location"):
        writer.create_internal_transfer(
            source_key="floor", dest_key="floor", lines=[{"product_id": product.id, "qty": 1}]
        )
    with pytest.raises(WriterValidationError, match="Unknown product"):
        writer.create_internal_transfer(
            source_key="bwhse", dest_key="floor", lines=[{"product_id": 424242, "qty": 1}]
        )


def test_manual_products_cannot_be_transferred(db, live_env):
    sim, _ = _prepared(db, live_env)
    from .util import mk_product

    water = mk_product(db, "MAN-WATER", "Spring Water", source="manual", stock_tracked=False)
    writer = OdooWriter(db, live_env, conn=sim)
    with pytest.raises(WriterValidationError, match="not stock-tracked"):
        writer.create_internal_transfer(
            source_key="bwhse", dest_key="floor", lines=[{"product_id": water.id, "qty": 1}]
        )


# ---------------------------------------------------------- unlink guard
def test_unlink_refuses_non_app_records(db, live_env):
    sim, product = _prepared(db, live_env)
    human_id = sim.call_kw(
        "stock.picking", "create", [{"origin": "WH/MANUAL/007", "picking_type_id": 5}]
    )
    writer = OdooWriter(db, live_env, conn=sim)
    with pytest.raises(WriterValidationError, match="not\\s+app-prefixed"):
        writer.unlink_app_record("stock.picking", human_id)
    assert sim.search_count("stock.picking", [["id", "=", human_id]]) == 1


def test_unlink_allows_app_records(db, live_env):
    sim, product = _prepared(db, live_env)
    set_flag(db, FLAG, True)
    writer = OdooWriter(db, live_env, conn=sim)
    created = writer.create_internal_transfer(
        source_key="bwhse", dest_key="floor", lines=[{"product_id": product.id, "qty": 1}]
    )
    result = writer.unlink_app_record("stock.picking", created.record_ids[0])
    assert result.success and not result.dry_run
    assert sim.search_count("stock.picking", [["id", "=", created.record_ids[0]]]) == 0


# ------------------------------------------- posting an approved adjustment
ADJ_FLAGS = ("write_create_inventory_addition", "write_validate_inventory_adjustment")


def _ready_adjustment(db, settings, sim, product):
    """An addition draft, as an approved count produces one."""
    writer = OdooWriter(db, settings, conn=sim)
    return writer.create_inventory_addition(
        product_id=product.id, qty=4, note="counted 4 more", reference="ILAPP-CNT-TEST01"
    )


def test_validate_adjustment_posts_the_stock(db, live_env, monkeypatch):
    sim, product = _prepared(db, live_env)
    for flag in ADJ_FLAGS:
        set_flag(db, flag, True)
    draft = _ready_adjustment(db, live_env, sim, product)
    picking_id = draft.record_ids[0]

    writer = OdooWriter(db, live_env, conn=sim)
    result = writer.validate_adjustment(
        picking_odoo_id=picking_id, reference="ILAPP-CNT-TEST01"
    )
    assert result.success and not result.dry_run
    state = sim.call_kw("stock.picking", "read", [[picking_id], ["state"]])[0]["state"]
    assert state == "done"  # posted, not merely confirmed

    # and it really moved: the floor gained the 4 units the counter found
    floor = [
        q
        for q in sim.search_read("stock.quant", [], ["product_id", "location_id", "quantity"])
        if q["location_id"][0] == 14 and q["product_id"][0] == product.odoo_product_id
    ]
    assert floor and sum(q["quantity"] for q in floor) > 0

    # a second call is a no-op success, not a double post
    again = writer.validate_adjustment(picking_odoo_id=picking_id)
    assert again.success and "already validated" in again.message


def test_validate_adjustment_refuses_a_picking_the_app_did_not_create(db, live_env):
    sim, _ = _prepared(db, live_env)
    for flag in ADJ_FLAGS:
        set_flag(db, flag, True)
    native = sim.search_read("stock.picking", [["name", "=", "WH/INT/NATIVE1"]], ["id", "origin"])
    assert native, "fixture should ship a picking the app didn't write"

    writer = OdooWriter(db, live_env, conn=sim)
    with pytest.raises(WriterValidationError, match="isn't app-prefixed"):
        writer.validate_adjustment(picking_odoo_id=native[0]["id"])


def test_validate_adjustment_refuses_anything_that_is_not_an_adjustment(db, live_env):
    """The load-bearing guard: ILAPP-CNT- is ALSO the floor's STAGING→FLOOR
    count transfer, which a person validates by scanning the pallet. A
    reference-only check would post stock nobody has counted yet."""
    sim, product = _prepared(db, live_env)
    for flag in (*ADJ_FLAGS, "write_create_internal_transfer"):
        set_flag(db, flag, True)
    transfer = OdooWriter(db, live_env, conn=sim).create_internal_transfer(
        source_key="bwhse",
        dest_odoo_location_id=14,
        dest_label="floor",
        lines=[{"product_id": product.id, "qty": 3}],
        note="pallet",
        reference="ILAPP-CNT-NOTANADJ",  # same prefix as a count adjustment
    )

    writer = OdooWriter(db, live_env, conn=sim)
    with pytest.raises(WriterValidationError, match="not an inventory-adjustment"):
        writer.validate_adjustment(picking_odoo_id=transfer.record_ids[0])
    state = sim.call_kw("stock.picking", "read", [transfer.record_ids, ["state"]])[0]["state"]
    assert state != "done"  # untouched


def test_validate_adjustment_is_gated_by_its_own_flag(db, live_env):
    sim, product = _prepared(db, live_env)
    set_flag(db, "write_create_inventory_addition", True)
    set_flag(db, "write_validate_inventory_adjustment", False)
    draft = _ready_adjustment(db, live_env, sim, product)

    writer = OdooWriter(db, live_env, conn=sim)
    result = writer.validate_adjustment(picking_odoo_id=draft.record_ids[0])
    assert result.dry_run and result.dry_run_reason == "feature_flag"
    state = sim.call_kw("stock.picking", "read", [draft.record_ids, ["state"]])[0]["state"]
    assert state != "done"  # the draft stands, exactly as before this feature
    assert db.scalar(
        select(OdooWriteAudit).where(OdooWriteAudit.operation == "validate_adjustment")
    ), "a gated write still leaves an audit row"
