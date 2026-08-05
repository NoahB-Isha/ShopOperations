"""Generated dashboard copy: the narrative summary card and the Q&A box.

Same LLM posture as everywhere else in the app: the model reads a compact
facts JSON built from the snapshot and writes prose — it changes no data, its
output is clearly labeled with its source (model id or 'heuristic'), and a
deterministic fallback runs when no ANTHROPIC_API_KEY is configured so dev,
tests, and the seed demo all work offline.

Narratives are cached per (period, facts-hash) in the `reports_narrative_cache`
AppSetting — the dashboard stays fast and the LLM is only consulted when the
underlying numbers actually changed (or on an explicit refresh).
"""
from __future__ import annotations

import hashlib
import json
import logging

from sqlalchemy.orm import Session

from ..config import Settings
from ..models import utcnow
from ..ordering.service import get_app_setting, set_app_setting
from .queries import Period, breakdown, sales_overview

log = logging.getLogger("reporting")

CACHE_SETTING_KEY = "reports_narrative_cache"
HEURISTIC_SOURCE = "heuristic"

_SUMMARY_SYSTEM = (
    "You write the summary card for Isha Life USA's internal retail sales "
    "dashboard (a nonprofit's shop operations — Shoppe on campus, online "
    "store, city-center pop-ups). Use ONLY the facts JSON provided. Never "
    "invent or extrapolate numbers; round naturally. Plain, warm, specific "
    "language — no corporate filler. Revenue figures marked estimated should "
    "be called estimates. 3-5 bullets, 1-3 suggested actions phrased as "
    "suggestions for a human to consider, never commands."
)

_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "One sentence, the single most important takeaway"},
        "bullets": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "actions": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
    },
    "required": ["headline", "bullets", "actions"],
    "additionalProperties": False,
}

_QA_SYSTEM = (
    "You answer questions about Isha Life USA's retail sales using ONLY the "
    "facts JSON provided. If the facts can't answer the question, say exactly "
    "what's missing instead of guessing. Never invent numbers. Keep answers "
    "to a few sentences; use the period label when talking about time."
)

_QA_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


# ------------------------------------------------------------------- facts
def build_facts(db: Session, period: Period) -> dict:
    """The compact, LLM-ready (and heuristic-ready) view of the period."""
    overview = sales_overview(db, period)
    products = overview["top_products"][:10]
    # fastest growers, with a small revenue floor so a $3 product tripling
    # doesn't headline the report
    movers = [
        r
        for r in breakdown(db, period, dim="product", limit=400)
        if r["prior_revenue"] >= 50 and r["delta_pct"] is not None and r["delta_pct"] > 0
    ]
    movers.sort(key=lambda r: -r["delta_pct"])
    movers = movers[:5]
    return {
        "period": overview["period"]["label"],
        "totals": overview["totals"],
        "orders": overview["orders"]["totals"],
        "orders_caveat": overview["orders"]["caveat"],
        "channels": overview["channels"],
        "top_categories": overview["top_categories"][:8],
        "top_products": [
            {k: p[k] for k in ("label", "sku", "units", "revenue", "delta_pct")} for p in products
        ],
        "fastest_growing_products": [
            {k: p[k] for k in ("label", "sku", "revenue", "prior_revenue", "delta_pct")}
            for p in movers
        ],
        "centers": overview["centers"][:10],
    }


def _facts_hash(facts: dict) -> str:
    return hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()[:16]


# --------------------------------------------------------------- heuristic
def _pct(x: float | None) -> str:
    if x is None:
        return "n/a (no prior-period data)"
    return f"{x * 100:+.0f}%"


def heuristic_summary(facts: dict) -> dict:
    t = facts["totals"]
    channels = facts["channels"]
    headline = (
        f"{facts['period']}: ${t['revenue']:,.0f} revenue on {t['units']:,.0f} units "
        f"({_pct(t['revenue_delta_pct'])} vs the prior period)."
    )
    bullets = []
    for ch in channels[:4]:
        bullets.append(
            f"{ch['label']}: ${ch['revenue']:,.0f} ({ch['share'] * 100:.0f}% of revenue, "
            f"{_pct(ch['delta_pct'])})"
        )
    o = facts.get("orders") or {}
    if o.get("orders"):
        aov = f"${o['aov']:,.2f}" if o.get("aov") is not None else "n/a"
        bullets.append(
            f"{o['orders']:,} orders at {aov} average ({_pct(o.get('aov_delta_pct'))} order size, "
            f"{_pct(o.get('orders_delta_pct'))} order count); {o.get('new_customers', 0):,} new customers."
        )
    if facts["top_products"]:
        p = facts["top_products"][0]
        bullets.append(f"Top product: {p['label']} — ${p['revenue']:,.0f} on {p['units']:g} units.")
    if facts["centers"]:
        c = max(
            facts["centers"],
            key=lambda c: c["delta_pct"] if c["delta_pct"] is not None else -9,
        )
        if c["delta_pct"] is not None:
            bullets.append(f"Fastest-growing center: {c['label']} ({_pct(c['delta_pct'])}).")
    actions = []
    if t["estimated_share"] > 0.5:
        actions.append(
            "Most revenue here is estimated at current retail — run the sales history rebuild "
            "(admin → status) to capture real amounts."
        )
    shrinking = [c for c in channels if c["delta_pct"] is not None and c["delta_pct"] < -0.15]
    for ch in shrinking[:2]:
        actions.append(f"Look into {ch['label']}: revenue is down {_pct(ch['delta_pct'])}.")
    if not actions:
        actions.append("Numbers look steady — nothing urgent suggested by the rules.")
    return {"headline": headline, "bullets": bullets, "actions": actions}


def heuristic_answer(facts: dict, question: str) -> str:
    ql = question.lower()
    t = facts["totals"]
    if "center" in ql:
        ranked = [c for c in facts["centers"] if c["delta_pct"] is not None]
        ranked.sort(key=lambda c: -c["delta_pct"])
        if ranked:
            top = ", ".join(f"{c['label']} ({_pct(c['delta_pct'])})" for c in ranked[:3])
            return f"Fastest-growing centers in {facts['period']}: {top}."
        top = ", ".join(f"{c['label']} (${c['revenue']:,.0f})" for c in facts["centers"][:3])
        return f"Top centers by revenue in {facts['period']}: {top}." if top else (
            "No city-center sales are recorded for this period yet."
        )
    if "categor" in ql:
        top = ", ".join(
            f"{c['label']} (${c['revenue']:,.0f})" for c in facts["top_categories"][:3]
        )
        return f"Top categories in {facts['period']}: {top}."
    if "product" in ql or "seller" in ql or "top" in ql:
        top = ", ".join(f"{p['label']} (${p['revenue']:,.0f})" for p in facts["top_products"][:3])
        return f"Top products in {facts['period']}: {top}."
    if "online" in ql or "channel" in ql or "shoppe" in ql:
        parts = ", ".join(
            f"{c['label']} ${c['revenue']:,.0f} ({c['share'] * 100:.0f}%)" for c in facts["channels"]
        )
        return f"Channel split for {facts['period']}: {parts}."
    o = facts.get("orders") or {}
    if any(k in ql for k in ("order size", "aov", "average order", "basket")) and o.get("aov"):
        return (
            f"{facts['period']}: average order ${o['aov']:,.2f} ({_pct(o.get('aov_delta_pct'))} vs "
            f"prior) across {o['orders']:,} orders ({_pct(o.get('orders_delta_pct'))})."
        )
    if any(k in ql for k in ("customer", "loyal", "repeat", "returning")) and o.get("orders"):
        share = o.get("returning_share_last_month")
        share_txt = (
            f"{share * 100:.0f}% of active customers in {o.get('returning_share_month')} were returning"
            if share is not None
            else "returning share not available yet"
        )
        return (
            f"{facts['period']}: {o.get('new_customers', 0):,} new customers; {share_txt}. "
            f"{facts.get('orders_caveat', '')}"
        )
    if "grow" in ql or "trend" in ql or "compare" in ql:
        return (
            f"{facts['period']} revenue is ${t['revenue']:,.0f}, {_pct(t['revenue_delta_pct'])} vs "
            f"the prior period; units {_pct(t['units_delta_pct'])}."
        )
    return (
        f"{facts['period']}: ${t['revenue']:,.0f} revenue, {t['units']:,.0f} units. "
        "I can break that down by channel, category, product, or center — "
        "for free-form questions, configure ANTHROPIC_API_KEY."
    )


# --------------------------------------------------------------------- llm
def _llm_call(settings: Settings, system: str, schema: dict, payload: str, max_tokens: int):
    """One structured call, or None (no key / refusal / any failure)."""
    if not settings.anthropic_api_key.get_secret_value():
        return None
    try:
        import anthropic

        client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key.get_secret_value(),
            timeout=settings.reports_llm_timeout_seconds,
            max_retries=0,
        )
        response = client.messages.create(
            model=settings.reports_llm_model,
            max_tokens=max_tokens,
            system=system,
            output_config={"format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": payload}],
        )
        if getattr(response, "stop_reason", "") == "refusal":
            return None
        text = next((b.text for b in response.content if b.type == "text"), "")
        return json.loads(text)
    except Exception as e:  # noqa: BLE001 — LLM polish must never take the page down
        log.warning("reports LLM call failed: %s", e)
        return None


def llm_summary(settings: Settings, facts: dict) -> dict | None:
    result = _llm_call(
        settings,
        _SUMMARY_SYSTEM,
        _SUMMARY_SCHEMA,
        json.dumps(facts, sort_keys=True),
        max_tokens=700,
    )
    if not result or not result.get("headline"):
        return None
    return result


# ------------------------------------------------------------------ public
def narrative(db: Session, settings: Settings, period: Period, force: bool = False) -> dict:
    """The summary card payload, cached per (period, facts-hash)."""
    facts = build_facts(db, period)
    fhash = _facts_hash(facts)
    cache = get_app_setting(db, CACHE_SETTING_KEY)
    cached = cache.get(period.key)
    if not force and cached and cached.get("facts_hash") == fhash:
        return cached["result"]

    result = llm_summary(settings, facts)
    source = settings.reports_llm_model if result else HEURISTIC_SOURCE
    result = result or heuristic_summary(facts)
    payload = {
        **result,
        "source": source,
        "generated": True,  # UI labels this card as generated content
        "generated_at": utcnow().isoformat(),
        "period": period.key,
    }
    cache[period.key] = {"facts_hash": fhash, "result": payload}
    set_app_setting(db, CACHE_SETTING_KEY, cache)
    db.commit()
    return payload


def answer_question(db: Session, settings: Settings, period: Period, question: str) -> dict:
    """The Q&A box: one question in, one labeled answer out. Read-only."""
    question = (question or "").strip()[:500]
    if not question:
        return {"answer": "Ask a question about the period's sales.", "source": HEURISTIC_SOURCE,
                "generated": True, "generated_at": utcnow().isoformat()}
    facts = build_facts(db, period)
    payload = json.dumps({"facts": facts, "question": question}, sort_keys=True)
    result = _llm_call(settings, _QA_SYSTEM, _QA_SCHEMA, payload, max_tokens=400)
    if result and result.get("answer"):
        answer, source = result["answer"], settings.reports_llm_model
    else:
        answer, source = heuristic_answer(facts, question), HEURISTIC_SOURCE
    return {
        "answer": answer,
        "source": source,
        "generated": True,
        "generated_at": utcnow().isoformat(),
    }
