"""Amazon/Canada ingestion: interfaces pinned, nothing built — and the stubs
say so loudly instead of pretending."""
from __future__ import annotations

from datetime import date

import pytest
from app.ingestion.sources import REGISTRY, STATUS_STUB, registry_status


def test_registry_lists_both_sources_as_stubs():
    assert set(REGISTRY) == {"amazon", "canada"}
    status = {s["key"]: s for s in registry_status()}
    assert all(s["status"] == STATUS_STUB for s in status.values())
    assert all(s["planned"] for s in status.values())  # each stub states its plan


@pytest.mark.parametrize("key", ["amazon", "canada"])
def test_stub_sources_refuse_loudly(key):
    source = REGISTRY[key]
    with pytest.raises(NotImplementedError, match="not built yet"):
        source.fetch_sales(date(2026, 1, 1), date(2026, 2, 1))
    with pytest.raises(NotImplementedError, match="not built yet"):
        source.fetch_inventory()
