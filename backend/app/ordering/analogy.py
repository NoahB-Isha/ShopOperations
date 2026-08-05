"""Analog suggestions for new products — "this will probably sell like X".

The suggestion is a PROPOSAL: the admin sees the candidate, the rationale,
and confirms (creating the ForecastAnalogy) or picks another product / a
manual estimate instead. LLM output never becomes an analogy by itself.

With ANTHROPIC_API_KEY configured the pick comes from the model (given the
new product and the candidate list); otherwise — and whenever the API call
fails — a deterministic name/category token-overlap heuristic answers, so the
feature works offline and in tests.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import Product, SalesMonthly, not_clothing

log = logging.getLogger("ordering.analogy")

HEURISTIC_SOURCE = "heuristic"


def _tokens(text: str) -> set[str]:
    return {w.rstrip("s") for w in re.findall(r"[a-z0-9']+", (text or "").lower()) if len(w) > 2}


def candidate_products(db: Session, product: Product, limit: int = 200) -> list[Product]:
    """Products a new item could plausibly sell like: active, with real sales
    history, same-category first."""
    with_history = {
        pid
        for (pid,) in db.execute(
            select(SalesMonthly.product_id)
            .group_by(SalesMonthly.product_id)
            .having(func.count() >= 3)
        )
    }
    rows = (
        db.execute(
            select(Product)
            .where(
                Product.is_active.is_(True),
                Product.id != product.id,
                not_clothing(),
            )
            .order_by(
                (Product.category == product.category).desc(),  # same category first
                Product.name,
            )
        )
        .scalars()
        .all()
    )
    return [p for p in rows if p.id in with_history][:limit]


def heuristic_suggest(product: Product, candidates: list[Product]) -> tuple[Product, str] | None:
    """Best name-token overlap, category as tiebreak."""
    target = _tokens(product.name)
    if not target:
        return None
    best: tuple[float, Product] | None = None
    for candidate in candidates:
        overlap = len(target & _tokens(candidate.name))
        if not overlap:
            continue
        score = overlap + (0.5 if candidate.category == product.category else 0.0)
        if best is None or score > best[0]:
            best = (score, candidate)
    if best is None:
        # nothing shares a name token — fall back to the first same-category item
        same_cat = [c for c in candidates if c.category == product.category]
        if not same_cat:
            return None
        pick = same_cat[0]
        return pick, f"same category ({pick.category}) — no closer name match found"
    pick = best[1]
    shared = ", ".join(sorted(target & _tokens(pick.name)))
    return pick, f"closest name match (shared terms: {shared})"


_LLM_SYSTEM = """You match a NEW retail product to the ONE existing product whose
sales pattern it will most likely follow. Same product family, size, price
band and buyer intent beat superficial word overlap. Answer with the
candidate's sku exactly as given and one sentence of rationale. If nothing
fits, use an empty sku."""

_LLM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "analog_sku": {"type": "string"},
        "rationale": {"type": "string"},
    },
    "required": ["analog_sku", "rationale"],
}


def llm_suggest(
    settings: Settings, product: Product, candidates: list[Product]
) -> tuple[Product, str] | None:
    if not settings.anthropic_api_key.get_secret_value() or not candidates:
        return None
    try:
        import anthropic

        client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key.get_secret_value(),
            timeout=settings.ordering_parser_llm_timeout_seconds,
            max_retries=0,
        )
        payload = {
            "new_product": {
                "sku": product.global_sku,
                "name": product.name,
                "category": product.category,
                "retail_price": float(product.retail_price or 0),
            },
            "candidates": [
                {
                    "sku": c.global_sku,
                    "name": c.name,
                    "category": c.category,
                    "retail_price": float(c.retail_price or 0),
                }
                for c in candidates
            ],
        }
        response = client.messages.create(
            model=settings.ordering_parser_llm_model,
            max_tokens=300,
            system=_LLM_SYSTEM,
            output_config={"format": {"type": "json_schema", "schema": _LLM_SCHEMA}},
            messages=[{"role": "user", "content": json.dumps(payload)}],
        )
        text = next((b.text for b in response.content if b.type == "text"), "")
        data = json.loads(text)
        sku = str(data.get("analog_sku") or "").strip()
        by_sku = {c.global_sku: c for c in candidates}
        if sku in by_sku:
            return by_sku[sku], str(data.get("rationale") or "suggested by the model")
        return None
    except Exception as e:  # advisory: fall back to the heuristic
        log.warning("LLM analog suggestion failed (%s) — using heuristic", e)
        return None


def suggest_analog(
    db: Session, settings: Settings, product: Product
) -> tuple[Product, str, str] | None:
    """(analog, rationale, source) or None when no candidate exists."""
    candidates = candidate_products(db, product)
    if not candidates:
        return None
    picked = llm_suggest(settings, product, candidates)
    if picked is not None:
        return picked[0], picked[1], settings.ordering_parser_llm_model
    fallback = heuristic_suggest(product, candidates)
    if fallback is None:
        return None
    return fallback[0], fallback[1], HEURISTIC_SOURCE
