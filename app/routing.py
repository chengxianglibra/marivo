from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.bindings import BindingService
from app.engines import EngineService
from app.storage.analytics import AnalyticsEngine
from app.storage.metadata import MetadataStore


@dataclass
class ResolvedRoute:
    """Result of resolve_tables(): the chosen engine plus qualified table names."""

    engine: AnalyticsEngine
    engine_id: str
    qualified_names: dict[str, str] = field(default_factory=dict)  # {native_name: qualified_name}


class QueryRouter:
    """Resolves table names to the appropriate analytics engine via
    source-engine bindings.

    Resolution path: table_name → source_objects → source_id → bindings → engine
    """

    def __init__(self, metadata: MetadataStore, engine_service: EngineService) -> None:
        self.metadata = metadata
        self.engine_service = engine_service
        self.binding_service = BindingService(metadata)

    def resolve_engine_for_tables(self, table_names: list[str]) -> AnalyticsEngine:
        """Given table names, find a common engine that can query all of them.

        Raises KeyError if a table is not found in source_objects.
        Raises ValueError if no single engine covers all tables.
        """
        route = self.resolve_tables(table_names)
        return route.engine

    def resolve_tables(self, table_names: list[str]) -> ResolvedRoute:
        """Given table names, find a common engine and return qualified names.

        Returns a ResolvedRoute with the engine, engine_id, and a mapping
        from native table names to engine-qualified names.

        Raises KeyError if a table is not found in source_objects.
        Raises ValueError if no single engine covers all tables.
        """
        if not table_names:
            raise ValueError("No table names provided")

        # Step 1: resolve each table to its source_id
        source_ids_per_table: dict[str, str] = {}
        for table_name in table_names:
            row = self.metadata.query_one(
                "SELECT source_id FROM source_objects WHERE object_type = 'table' AND native_name = ?",
                [table_name],
            )
            if row is None:
                raise KeyError(f"Table not found in source_objects: {table_name}")
            source_ids_per_table[table_name] = row["source_id"]

        # Step 2: for each unique source, get candidate engine_ids and binding info
        unique_sources = set(source_ids_per_table.values())
        engine_sets: dict[str, set[str]] = {}
        engine_priorities: dict[str, dict[str, int]] = {}  # engine_id -> source_id -> priority
        # Track binding details: (source_id, engine_id) -> binding dict
        binding_details: dict[tuple[str, str], dict[str, Any]] = {}

        for source_id in unique_sources:
            bindings = self.metadata.query_rows(
                """
                SELECT engine_id, priority, namespace_json
                FROM source_engine_bindings
                WHERE source_id = ? AND status = 'active'
                """,
                [source_id],
            )
            if not bindings:
                raise ValueError(
                    f"Source '{source_id}' has no active engine bindings"
                )
            engine_ids = set()
            for b in bindings:
                engine_ids.add(b["engine_id"])
                engine_priorities.setdefault(b["engine_id"], {})[source_id] = b["priority"]
                import json
                binding_details[(source_id, b["engine_id"])] = {
                    "namespace": json.loads(b["namespace_json"]),
                }
            engine_sets[source_id] = engine_ids

        # Step 3: intersect engine sets across all sources
        common_engines = engine_sets[next(iter(engine_sets))]
        for source_id, engines in engine_sets.items():
            common_engines = common_engines & engines

        if not common_engines:
            detail_parts = []
            for source_id, engines in engine_sets.items():
                detail_parts.append(f"source '{source_id}' → engines {engines}")
            raise ValueError(
                f"No common engine for tables {table_names}. "
                f"Bindings: {'; '.join(detail_parts)}"
            )

        # Step 4: pick the engine with highest total priority
        best_engine_id = max(
            common_engines,
            key=lambda eid: sum(engine_priorities.get(eid, {}).values()),
        )

        # Step 5: build qualified names using binding namespace
        qualified_names: dict[str, str] = {}
        for table_name in table_names:
            source_id = source_ids_per_table[table_name]
            binding = binding_details.get((source_id, best_engine_id), {})
            qualified_names[table_name] = self.qualify_table_name(
                table_name, source_id, binding,
            )

        # Step 6: build the analytics engine
        engine = self.engine_service.build_analytics_engine(best_engine_id)
        return ResolvedRoute(
            engine=engine,
            engine_id=best_engine_id,
            qualified_names=qualified_names,
        )

    def resolve_engine_for_source(self, source_id: str) -> AnalyticsEngine:
        """Return the highest-priority engine bound to a source.

        Raises ValueError if no bindings exist for the source.
        """
        engines = self.binding_service.get_engines_for_source(source_id)
        if not engines:
            raise ValueError(f"Source '{source_id}' has no active engine bindings")
        # Already ordered by priority DESC
        return self.engine_service.build_analytics_engine(engines[0]["engine_id"])

    def get_engine_info_for_source(self, source_id: str) -> dict[str, Any] | None:
        """Return the highest-priority engine dict (not instance) for a source,
        or None if no bindings exist."""
        engines = self.binding_service.get_engines_for_source(source_id)
        if not engines:
            return None
        best = engines[0]
        return {
            "engine_id": best["engine_id"],
            "engine_type": best["engine_type"],
            "display_name": best["display_name"],
            "priority": best["priority"],
            "namespace": best.get("namespace", {}),
        }

    def qualify_table_name(
        self, table_native_name: str, source_id: str, binding: dict[str, Any],
    ) -> str:
        """Build an engine-qualified table reference using binding namespace."""
        ns = binding.get("namespace", {})
        parts: list[str] = []

        if catalog := ns.get("catalog"):
            parts.append(catalog)

        # Resolve schema: explicit override from namespace, or look up from source_objects hierarchy
        schema = ns.get("schema")  # explicit override
        if schema is None:
            schema = self._get_table_schema(table_native_name, source_id)
        if schema is not None:
            parts.append(schema)

        parts.append(table_native_name)
        return ".".join(parts)

    def _get_table_schema(self, table_native_name: str, source_id: str) -> str | None:
        """Find the parent schema name for a table in source_objects."""
        row = self.metadata.query_one(
            """SELECT so_parent.native_name
               FROM source_objects so
               JOIN source_objects so_parent ON so.parent_id = so_parent.object_id
               WHERE so.source_id = ? AND so.native_name = ? AND so.object_type = 'table'
            """,
            [source_id, table_native_name],
        )
        return row["native_name"] if row else None
