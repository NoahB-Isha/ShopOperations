"""External sales-data ingestion — INTERFACES ONLY for now.

Amazon and Canada are "someday" data sources (feature list rows 25-26): the
dashboard should eventually fold them in, but nothing is built yet — by
design (Prompt 5: "stub the ingestion interfaces, don't build"). This module
pins down the seam so wiring a real source later is additive:

  * implement `ExternalSalesSource` for the new source,
  * translate its records into `ExternalSaleRecord`s (one per product/day/
    channel — the same grain as `sales_daily`),
  * register it in `REGISTRY`.

A future sync domain would then upsert records into the sales snapshot under
its own channel value (e.g. 'amazon'), which the dashboard already renders
generically. Nothing else in the app should import source-specific code.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

STATUS_STUB = "stub"  # interface pinned, nothing implemented
STATUS_LIVE = "live"


@dataclass(frozen=True)
class ExternalSaleRecord:
    """One product×day×channel sales fact from an external source, matching
    sales_daily's grain. `external_sku` is the source's own product id; the
    ingestion sync (future) is responsible for matching it to a Product
    (the catalog matcher in app/catalog/matching.py is the intended tool)."""

    source: str  # registry key, e.g. "amazon"
    external_sku: str
    name: str
    day: date
    units: float
    amount: float | None  # gross revenue, source currency
    currency: str = "USD"


@dataclass(frozen=True)
class ExternalInventoryRecord:
    """On-hand at the external source (e.g. FBA warehouse stock)."""

    source: str
    external_sku: str
    name: str
    qty: float


class ExternalSalesSource(Protocol):
    """The seam a real integration must fill. Implementations must be polite
    clients (paged, throttled) and must never write to the source."""

    key: str
    label: str
    status: str  # STATUS_STUB | STATUS_LIVE

    def fetch_sales(self, since: date, until: date) -> list[ExternalSaleRecord]:
        """Sales facts for the window, one record per product/day."""
        ...

    def fetch_inventory(self) -> list[ExternalInventoryRecord]:
        """Current on-hand at the source."""
        ...


class _StubSource:
    """Shared stub behavior: honest, loud, and useless — exactly as specced."""

    key = ""
    label = ""
    status = STATUS_STUB
    planned = ""

    def fetch_sales(self, since: date, until: date) -> list[ExternalSaleRecord]:
        raise NotImplementedError(
            f"{self.label} ingestion is not built yet — interface only. Planned: {self.planned}"
        )

    def fetch_inventory(self) -> list[ExternalInventoryRecord]:
        raise NotImplementedError(
            f"{self.label} ingestion is not built yet — interface only. Planned: {self.planned}"
        )


class AmazonSource(_StubSource):
    """Amazon US marketplace sales (Seller Central).

    Planned wiring: SP-API `GET_SALES_AND_TRAFFIC_REPORT` (daily, per-ASIN)
    with an ASIN↔SKU mapping table; FBA inventory from
    `GET_FBA_MYI_UNSUPPRESSED_INVENTORY_DATA`. Amounts arrive in USD tax-in.
    """

    key = "amazon"
    label = "Amazon (Seller Central)"
    planned = "SP-API sales & traffic report + FBA inventory report"


class CanadaSource(_StubSource):
    """Isha Life Canada sales (separate operation, currently outside this
    Odoo instance).

    Planned wiring: whichever export Canada can produce reliably — a separate
    Odoo/POS export or a monthly spreadsheet — normalized to
    `ExternalSaleRecord`s in CAD with a currency conversion at ingest.
    """

    key = "canada"
    label = "Isha Life Canada"
    planned = "periodic spreadsheet/Odoo export, CAD normalized at ingest"


REGISTRY: dict[str, ExternalSalesSource] = {
    s.key: s for s in (AmazonSource(), CanadaSource())
}


def registry_status() -> list[dict]:
    """For the admin status page: what exists, what's stubbed."""
    return [
        {
            "key": s.key,
            "label": s.label,
            "status": s.status,
            # stubs state their intended wiring; a live source has nothing to plan
            "planned": getattr(s, "planned", ""),
        }
        for s in REGISTRY.values()
    ]
