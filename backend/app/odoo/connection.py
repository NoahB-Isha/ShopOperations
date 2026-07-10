from __future__ import annotations

from ..config import Settings
from .client import OdooClient
from .protocol import OdooConnection
from .simulator import OdooSimulator


def get_connection(settings: Settings, read_only: bool = True) -> OdooConnection:
    """Live client when ODOO_* credentials are set; otherwise the fixture
    simulator (auto-generating the demo fixture set on first use)."""
    if settings.odoo_configured:
        return OdooClient(settings, read_only=read_only)
    if not settings.fixtures_path.is_dir():
        from .fixtures.generate import generate_fixtures

        generate_fixtures(settings.fixtures_path)
    return OdooSimulator(settings.fixtures_path, read_only=read_only)
