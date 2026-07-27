"""Shared product-search semantics — tokenized and separator-insensitive.

"Yoga mat" must find "Yoga-Mat-Cotton-Brown": the query splits into
alphanumeric tokens, and a product matches when ANY ONE searched field
(name, SKU, barcode, category) contains ALL the tokens — word order and the
separators between words (spaces, hyphens, underscores, slashes) don't
matter. Tokens deliberately don't mix across fields: "copper 00" should not
return every product whose SKU happens to contain "00". Any query that
matched as a contiguous substring before still matches, so this is strictly
more forgiving than the old `%whole query%` ILIKE, never less.

Two twins, one semantics: `product_search_clause` for SQL list endpoints,
`matches_search` for the in-memory filters (availability, time machine, the
bot API). The frontend mirrors this in `src/search.ts` for its client-side
filtered lists — change one, change all three.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.sql.elements import ColumnElement

# split on runs of anything that isn't a letter or digit (underscore included:
# it's a separator in SKUs, not part of a word)
_SEPARATORS = re.compile(r"[\W_]+", re.UNICODE)


def search_tokens(query: str | None) -> list[str]:
    return [t for t in _SEPARATORS.split(query or "") if t]


def product_search_clause(query: str, columns: tuple[Any, ...]) -> ColumnElement | None:
    """OR over columns, each column required to contain every token; None
    when the query holds no tokens (only punctuation/whitespace) — callers
    skip the filter. Tokens are alphanumeric by construction, so no
    LIKE-wildcard escaping is needed."""
    tokens = search_tokens(query)
    if not tokens:
        return None
    return or_(*(and_(*(col.ilike(f"%{t}%") for t in tokens)) for col in columns))


def matches_search(query: str | None, *fields: str | None) -> bool:
    """The Python-side twin of `product_search_clause` for already-loaded
    rows. Empty/punctuation-only queries match everything."""
    tokens = [t.lower() for t in search_tokens(query)]
    if not tokens:
        return True
    return any(
        all(t in low for t in tokens) for low in ((f or "").lower() for f in fields) if low
    )
