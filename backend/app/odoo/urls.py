from __future__ import annotations

from ..config import Settings


def odoo_record_url(settings: Settings, model: str, record_id: int) -> str:
    """Deep link to a record in the Odoo web client. Every record the app
    creates must be presented with one — the human handoff is part of the
    feature. In fixture mode there is no real instance; we return a clearly
    fake URL so UIs can still render the affordance."""
    base = settings.odoo_base_url.rstrip("/") or "https://odoo.fixture.invalid"
    return settings.odoo_record_url_template.format(base=base, model=model, id=record_id)
