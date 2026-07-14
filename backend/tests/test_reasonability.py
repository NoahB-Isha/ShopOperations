"""The reasonability rules — pure, deterministic, and advisory-only.

The LLM pass is exercised only for its failure mode here (no API key → the
rules result must come through untouched); its live behaviour is escalate-
never-suppress, asserted via _worst.
"""
from __future__ import annotations

from app.center_orders.reasonability import (
    Assessment,
    CenterHistory,
    LineFacts,
    _worst,
    assess_rules,
    polish_with_llm,
)
from app.config import get_settings


def settings():
    return get_settings()


def _history(**kw) -> CenterHistory:
    h = CenterHistory()
    for k, v in kw.items():
        setattr(h, k, v)
    return h


def _codes(a: Assessment, product_id: int) -> set[str]:
    return {b.code for b in a.lines.get(product_id, [])}


def test_clean_order_is_ok(settings_env):
    history = _history(
        order_count=3, avg_total_units=18,
        per_product={1: (3, 6.0)}, days_since_last={1: 14.0},
    )
    a = assess_rules(
        [LineFacts(product_id=1, name="Copper Bottle", qty=6, on_hand=40)],
        history, settings(),
    )
    assert a.level == "ok"
    assert a.lines == {}
    assert "reasonable" in a.summary.lower()


def test_volume_spike_needs_two_priors(settings_env):
    line = LineFacts(product_id=1, name="Copper Bottle", qty=20, on_hand=100)
    one_prior = _history(order_count=1, avg_total_units=6, per_product={1: (1, 6.0)})
    assert "volume_spike" not in _codes(assess_rules([line], one_prior, settings()), 1)

    two_priors = _history(order_count=2, avg_total_units=6, per_product={1: (2, 6.0)})
    a = assess_rules([line], two_priors, settings())
    assert "volume_spike" in _codes(a, 1)
    assert a.level == "warn"
    [badge] = [b for b in a.lines[1] if b.code == "volume_spike"]
    assert badge.text == "3.3× usual volume"  # 20 / 6 — chip-length on purpose


def test_exceeds_stock_and_low_stock_caveat(settings_env):
    history = _history(order_count=2, avg_total_units=10, per_product={1: (2, 10.0)})
    a = assess_rules(
        [LineFacts(product_id=1, name="Incense", qty=12, on_hand=5)], history, settings()
    )
    assert "exceeds_stock" in _codes(a, 1) and a.level == "warn"

    a = assess_rules(
        [LineFacts(product_id=1, name="Incense", qty=2, on_hand=3)], history, settings()
    )
    assert "low_stock_data" in _codes(a, 1)
    assert a.level == "info"  # a data-quality note, not an alarm


def test_case_mismatch_and_repeat_recent_are_infos(settings_env):
    history = _history(
        order_count=2, avg_total_units=10,
        per_product={1: (2, 10.0)}, days_since_last={1: 2.0},
    )
    a = assess_rules(
        [LineFacts(product_id=1, name="Ghee Jar", qty=10, on_hand=50, case_size=6)],
        history, settings(),
    )
    assert {"case_mismatch", "repeat_recent"} <= _codes(a, 1)
    assert a.level == "info"


def test_first_time_item_and_first_order(settings_env):
    # brand-new center: one order-level info, no per-line noise
    a = assess_rules(
        [LineFacts(product_id=1, name="Copper Bottle", qty=6, on_hand=40)],
        _history(order_count=0), settings(),
    )
    assert [b.code for b in a.order_badges] == ["first_order"]
    assert a.level == "info"

    # established center, new item
    history = _history(order_count=3, avg_total_units=12, per_product={2: (3, 6.0)})
    a = assess_rules(
        [LineFacts(product_id=1, name="New Mala", qty=2, on_hand=30)], history, settings()
    )
    assert "first_time_item" in _codes(a, 1)


def test_huge_order_relative_and_absolute(settings_env):
    history = _history(order_count=3, avg_total_units=10, per_product={1: (3, 10.0)})
    a = assess_rules(
        [LineFacts(product_id=1, name="Copper Bottle", qty=35, on_hand=500)],
        history, settings(),
    )
    assert any(b.code == "huge_order" for b in a.order_badges)

    a = assess_rules(
        [LineFacts(product_id=1, name="Copper Bottle", qty=600, on_hand=5000)],
        _history(order_count=0), settings(),
    )
    assert any(b.code == "very_large_order" for b in a.order_badges)
    assert a.level == "warn"


def test_summary_counts_warns_and_infos(settings_env):
    history = _history(order_count=2, avg_total_units=10, per_product={1: (2, 4.0)})
    a = assess_rules(
        [
            LineFacts(product_id=1, name="A", qty=20, on_hand=5),  # spike + exceeds
            LineFacts(product_id=2, name="B", qty=5, on_hand=50, case_size=4),  # case info
        ],
        history, settings(),
    )
    assert "worth a second look" in a.summary and "minor note" in a.summary


def test_worst_level_ordering():
    assert _worst("ok", "info") == "info"
    assert _worst("info", "warn", "ok") == "warn"
    assert _worst() == "ok"
    assert _worst("", "bogus") in ("", "bogus", "ok")  # unknowns never crash


def test_llm_polish_without_key_is_a_noop(settings_env):
    a = Assessment(level="warn", summary="rules summary", source="rules")
    out = polish_with_llm(settings(), a, {"center": "Austin"})
    assert out.summary == "rules summary"
    assert out.source == "rules"  # untouched — no key configured
