"""Legacy semantic_runtime.catalog stubs — preserved for import compatibility."""

from __future__ import annotations

from typing import Any

from app.storage.metadata import MetadataStore


class CatalogRuntimeService:
    """Stub — returns empty results during OSI v2 migration.  See Task 7."""

    def __init__(self, metadata: MetadataStore, **_kwargs: Any) -> None:
        self.metadata = metadata

    def search(self, q: str, **_kwargs: Any) -> list[dict[str, Any]]:
        return []

    def resolve(self, name: str) -> dict[str, Any]:
        raise KeyError(f"Cannot resolve: {name}")

    def get_catalog_object_detail(self, object_kind: str, object_id: str) -> dict[str, Any]:
        raise KeyError(f"Object not found: {object_kind}/{object_id}")

    def planner_context(self, session_id: str) -> dict[str, Any]:
        return {}

    def graph(self, root: str, depth: int = 2) -> dict[str, Any]:
        return {}
