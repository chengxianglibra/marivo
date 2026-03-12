from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.semantic_runtime import CatalogRuntimeService
from app.storage.metadata import MetadataStore

if TYPE_CHECKING:
    from app.bindings import BindingService


class CatalogQueryService:
    """Search, resolve, planner-context, and graph traversal over the
    semantic and physical catalog."""

    def __init__(
        self,
        metadata: MetadataStore,
        binding_service: BindingService | None = None,
    ) -> None:
        self.metadata = metadata
        self.runtime = CatalogRuntimeService(metadata, binding_service)

    # ── Search ───────────────────────────────────────────────────

    def search(self, query: str, object_type: str | None = None) -> list[dict[str, Any]]:
        return self.runtime.search(query, object_type=object_type)

    # ── Resolve ──────────────────────────────────────────────────

    def resolve(self, name: str) -> dict[str, Any]:
        return self.runtime.resolve(name)

    # ── Planner context ──────────────────────────────────────────

    def planner_context(self, session_id: str, service: object) -> dict[str, Any]:
        del service
        return self.runtime.planner_context(session_id)

    # ── Graph traversal ──────────────────────────────────────────

    def graph(self, root: str, depth: int = 2) -> dict[str, Any]:
        return self.runtime.graph(root, depth)
