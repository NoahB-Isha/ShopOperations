"""Reply-parser unit tests — the acceptance email must yield two correctly
parsed proposals with verbatim quotes, and matching must resolve hints to the
right order lines."""

from __future__ import annotations

from app.models import OrderEventKind, PurchaseOrder, PurchaseOrderLine
from app.ordering.parser import (
    Extraction,
    _clean_hint,
    heuristic_extract,
    llm_extract,
    match_line,
    resolve_proposals,
)

ACCEPTANCE_REPLY = (
    "Namaskaram,\n\n"
    "Thank you for the order. We checked with the warehouse — we can only send "
    "200 of the 500 lamps, and dhoop sticks are discontinued.\n\n"
    "Everything else will ship as requested.\n"
)


def _order_with_lines() -> PurchaseOrder:
    order = PurchaseOrder(id=1, name="Q3 2026", reference="ILAPP-PO-TEST")
    order.lines = [
        PurchaseOrderLine(
            id=11,
            order_id=1,
            global_sku="HO0000500400",
            final_sea_qty=500,
            final_air_qty=0,
            suggestion_json={"name": "Brass Guru Puja Lamp Large"},
        ),
        PurchaseOrderLine(
            id=12,
            order_id=1,
            global_sku="IN0001200500",
            final_sea_qty=400,
            final_air_qty=0,
            suggestion_json={"name": "Sambrani Dhoop Sticks"},
        ),
        PurchaseOrderLine(
            id=13,
            order_id=1,
            global_sku="BC0001005100",
            final_sea_qty=100,
            final_air_qty=50,
            suggestion_json={"name": "Solid Perfume Jasmine Orient"},
        ),
    ]
    return order


def test_acceptance_email_yields_two_extractions_with_quotes():
    extractions = heuristic_extract(ACCEPTANCE_REPLY)
    kinds = {e.kind for e in extractions}
    assert OrderEventKind.QTY_CHANGE.value in kinds
    assert OrderEventKind.DISCONTINUED.value in kinds
    qty = next(e for e in extractions if e.kind == "qty_change")
    disc = next(e for e in extractions if e.kind == "discontinued")
    # verbatim supporting quotes
    assert qty.quote in ACCEPTANCE_REPLY
    assert "200 of the 500 lamps" in qty.quote
    assert disc.quote in ACCEPTANCE_REPLY
    assert "dhoop sticks are discontinued" in disc.quote
    # payload carries both quantities; hints are clean product names
    assert qty.payload["to"] == 200 and qty.payload["from"] == 500
    assert qty.product_hint == "lamps"
    assert disc.product_hint == "dhoop sticks"


def test_acceptance_email_resolves_to_the_right_lines():
    order = _order_with_lines()
    proposals = resolve_proposals(order, heuristic_extract(ACCEPTANCE_REPLY), "heuristic")
    assert len(proposals) == 2
    qty = next(p for p in proposals if p.kind == "qty_change")
    disc = next(p for p in proposals if p.kind == "discontinued")
    assert qty.line_id == 11  # the lamp
    assert disc.line_id == 12  # the dhoop sticks
    # qty payload shaped for the sea leg, with the matching 'from' boosting confidence
    assert qty.payload["sea"] == {"from": 500, "to": 200}
    assert qty.confidence > 0.8
    assert 0 < disc.confidence < 1


def test_clean_hint_strips_leading_conjunctions():
    assert _clean_hint("and the dhoop sticks") == "dhoop sticks"
    assert _clean_hint("but also our copper bottles") == "copper bottles"
    assert _clean_hint("lamps") == "lamps"


def test_more_phrasings():
    extractions = heuristic_extract(
        "We will replace the rose incense with sandalwood incense. "
        "Copper bottles will be available by mid-August. "
        "Please note we are shipping the balms by air."
    )
    kinds = [e.kind for e in extractions]
    assert "substitution" in kinds
    assert "availability" in kinds
    assert "method_change" in kinds
    sub = next(e for e in extractions if e.kind == "substitution")
    assert sub.product_hint == "rose incense"
    assert sub.payload["substitute_hint"] == "sandalwood incense"
    avail = next(e for e in extractions if e.kind == "availability")
    assert avail.payload["eta_text"].lower().startswith("mid-august")
    method = next(e for e in extractions if e.kind == "method_change")
    assert method.payload["to"] == "air"


def test_ambiguous_hint_leaves_line_unset():
    order = _order_with_lines()
    # "incense" matches nothing in this order's names -> no line, lower confidence
    proposals = resolve_proposals(
        order,
        [Extraction(kind="discontinued", quote="incense is discontinued",
                    confidence=0.8, product_hint="incense")],
        "heuristic",
    )
    assert proposals[0].line_id is None
    assert proposals[0].confidence < 0.8


def test_sku_hint_matches_exactly():
    order = _order_with_lines()
    line, boost = match_line(order, "bc0001005100")
    assert line is not None and line.id == 13
    assert boost > 0


def test_no_noise_extractions_from_pleasantries():
    assert heuristic_extract(
        "Namaskaram, thank you for the order. We will confirm shortly. Pranam."
    ) == []


def test_llm_extract_without_key_returns_none(settings_env):
    order = _order_with_lines()
    assert llm_extract(settings_env, order, ACCEPTANCE_REPLY) is None
