"""Spreadsheet → products: take ANY sheet with a list of items and find the
catalog products it means.

People keep their lists in whatever shape WhatsApp/Excel left them — a name
column, a SKU column, barcodes, quantities, prices, all optional, headers
optional. So matching works cell-by-cell, most-reliable signal first:

  1. SKU  — global_sku / us_sku / odoo_internal_ref (case-insensitive)
  2. barcode — digit strings of 8+ (shorter numbers are qty/price noise)
  3. name — normalized exact, then a UNIQUE substring/token match only
            (ambiguity surfaces as unmatched rather than guessing wrong)

Quantities and every other column are ignored by construction: short numbers
match nothing. One product per row, deduped across rows; unmatched rows are
reported so the human can add them by hand — never silently dropped.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Product, not_blacklisted

MAX_ROWS = 5000

_HEADER_WORDS = {
    "name", "product", "products", "item", "items", "description", "title",
    "sku", "skus", "code", "codes", "barcode", "barcodes", "upc", "ean",
    "qty", "quantity", "quantities", "count", "price", "cost", "amount",
    "unit", "units", "notes", "category", "vendor", "supplier", "email",
    "global sku", "us sku", "internal reference", "reference",
}


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


@dataclass
class MatchHit:
    row_index: int  # 0-based data row (after any header)
    product: Product
    matched_by: str  # sku | barcode | name
    cell: str


@dataclass
class MatchReport:
    hits: list[MatchHit] = field(default_factory=list)
    unmatched: list[tuple[int, str]] = field(default_factory=list)  # (row, preview)
    total_rows: int = 0
    had_header: bool = False

    @property
    def products(self) -> list[Product]:
        return [h.product for h in self.hits]


class SpreadsheetError(ValueError):
    """The upload couldn't be read as a table."""


def parse_table(file_bytes: bytes, filename: str = "") -> list[list[str]]:
    """CSV or Excel → rows of trimmed cell strings (empty rows dropped)."""
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm", ".xltx")) or file_bytes[:4] == b"PK\x03\x04":
        return _parse_xlsx(file_bytes)
    return _parse_csv(file_bytes)


def _parse_xlsx(file_bytes: bytes) -> list[list[str]]:
    import warnings

    import openpyxl

    warnings.filterwarnings("ignore", module="openpyxl")
    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
    except Exception as e:
        raise SpreadsheetError(f"couldn't open the Excel file: {e}") from e
    ws = wb.active
    if ws is None:
        raise SpreadsheetError("the Excel file has no sheets")
    rows: list[list[str]] = []
    for raw in ws.iter_rows(values_only=True):
        cells = ["" if v is None else str(v).strip() for v in raw]
        if any(cells):
            rows.append(cells)
        if len(rows) >= MAX_ROWS:
            break
    wb.close()
    return rows


def _parse_csv(file_bytes: bytes) -> list[list[str]]:
    text = file_bytes.decode("utf-8-sig", errors="replace")
    if not text.strip():
        raise SpreadsheetError("the file is empty")
    try:
        dialect: type[csv.Dialect] | csv.Dialect = csv.Sniffer().sniff(
            text[:4096], delimiters=",;\t|"
        )
    except csv.Error:
        dialect = csv.excel
    rows: list[list[str]] = []
    for raw in csv.reader(io.StringIO(text), dialect):
        cells = [(c or "").strip() for c in raw]
        if any(cells):
            rows.append(cells)
        if len(rows) >= MAX_ROWS:
            break
    return rows


def _looks_like_header(row: list[str]) -> bool:
    labels = [_norm(c) for c in row if c.strip()]
    if not labels:
        return False
    known = sum(1 for c in labels if c in _HEADER_WORDS)
    return known >= max(1, len(labels) // 2)


def match_products(db: Session, rows: list[list[str]]) -> MatchReport:
    """Resolve each row to at most one product. See module docstring."""
    report = MatchReport()
    if not rows:
        return report
    rows = [["" if c is None else str(c).strip() for c in row] for row in rows]
    data_rows = rows
    if _looks_like_header(rows[0]):
        report.had_header = True
        data_rows = rows[1:]
    report.total_rows = len(data_rows)
    if not data_rows:
        return report

    products = db.execute(select(Product).where(not_blacklisted())).scalars().all()
    by_sku: dict[str, Product] = {}
    by_barcode: dict[str, Product] = {}
    by_name: dict[str, Product] = {}
    for p in products:
        for code in (p.global_sku, p.us_sku, p.odoo_internal_ref):
            if code:
                by_sku.setdefault(code.strip().lower(), p)
        if p.barcode:
            by_barcode.setdefault(p.barcode.strip(), p)
        normed = _norm(p.name)
        if normed:
            by_name.setdefault(normed, p)

    seen_products: set[int] = set()
    for index, row in enumerate(data_rows):
        hit = _match_row(row, by_sku, by_barcode, by_name)
        if hit is None:
            preview = " · ".join(c for c in row if c)[:120]
            report.unmatched.append((index, preview))
            continue
        product, matched_by, cell = hit
        if product.id in seen_products:
            continue  # the same item twice in the sheet — first one wins
        seen_products.add(product.id)
        report.hits.append(
            MatchHit(row_index=index, product=product, matched_by=matched_by, cell=cell)
        )
    return report


def _match_row(
    row: list[str],
    by_sku: dict[str, Product],
    by_barcode: dict[str, Product],
    by_name: dict[str, Product],
) -> tuple[Product, str, str] | None:
    cells = [c for c in row if c.strip()]
    # 1. SKU anywhere in the row
    for cell in cells:
        product = by_sku.get(cell.strip().lower())
        if product is not None:
            return product, "sku", cell
    # 2. barcode: long digit runs (ignores qty/price numbers by length)
    for cell in cells:
        digits = cell.strip()
        if re.fullmatch(r"\d{8,14}", digits):
            product = by_barcode.get(digits)
            if product is not None:
                return product, "barcode", cell
    # 3. name: normalized exact, then unique containment
    for cell in cells:
        normed = _norm(cell)
        if len(normed) < 4 or normed.replace(" ", "").isdigit():
            continue
        product = by_name.get(normed)
        if product is not None:
            return product, "name", cell
    for cell in cells:
        normed = _norm(cell)
        if len(normed) < 6 or normed.replace(" ", "").isdigit():
            continue
        contains = [p for n, p in by_name.items() if normed in n or n in normed]
        unique = {p.id: p for p in contains}
        if len(unique) == 1:
            return next(iter(unique.values())), "name", cell
    # 4. token subset: every word of the cell appears in exactly one product's
    #    name ("comfrey soap" → "Lavender & Comfrey Soap") — unique or nothing
    for cell in cells:
        normed = _norm(cell)
        tokens = set(normed.split())
        if len(normed) < 6 or len(tokens) < 2 or normed.replace(" ", "").isdigit():
            continue
        subset = [p for n, p in by_name.items() if tokens <= set(n.split())]
        unique = {p.id: p for p in subset}
        if len(unique) == 1:
            return next(iter(unique.values())), "name", cell
    return None
