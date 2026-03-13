from __future__ import annotations

from typing import Any

from app.adapters.base import CatalogAdapter
from app.registry import SourceRegistry, build_catalog_adapter


class SourceService(SourceRegistry):
    """Compatibility facade over the new registry layer."""


def _build_adapter(source_type: str, connection: dict[str, Any]) -> CatalogAdapter:
    return build_catalog_adapter(source_type, connection)
