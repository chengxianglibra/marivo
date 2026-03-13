from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.bindings import BindingService
from app.engines import EngineService
from app.execution.capabilities import (
    EngineCapabilityProfile,
    describe_routing_fit,
    score_capability_profile,
)
from app.storage.analytics import AnalyticsEngine
from app.storage.metadata import MetadataStore


@dataclass
class RoutingIntent:
    """Semantic and policy signals that influence engine selection."""

    step_type: str | None = None
    metric_names: list[str] = field(default_factory=list)
    requested_dimensions: list[str] = field(default_factory=list)
    compatible_dimensions: list[str] = field(default_factory=list)
    legal_grains: list[str] = field(default_factory=list)
    policy_hints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ResolvedRoute:
    """Result of resolve_tables(): the chosen engine plus qualified table names."""

    engine: AnalyticsEngine
    engine_id: str
    qualified_names: dict[str, str] = field(default_factory=dict)  # {native_name: qualified_name}
    capability_profile: EngineCapabilityProfile | None = None
    capability_score: int = 0
    selection_reason: str | None = None
    routing_detail: dict[str, Any] = field(default_factory=dict)


class QueryRouter:
    """Resolves table names to the appropriate analytics engine via
    source-engine bindings.

    Resolution path: table_name → source_objects → source_id → bindings → engine
    """

    def __init__(self, metadata: MetadataStore, engine_service: EngineService) -> None:
        self.metadata = metadata
        self.engine_service = engine_service
        self.binding_service = BindingService(metadata)

    def resolve_engine_for_tables(
        self,
        table_names: list[str],
        *,
        routing_intent: RoutingIntent | None = None,
    ) -> AnalyticsEngine:
        """Given table names, find a common engine that can query all of them.

        Raises KeyError if a table is not found in source_objects.
        Raises ValueError if no single engine covers all tables.
        """
        route = self.resolve_tables(table_names, routing_intent=routing_intent)
        return route.engine

    def resolve_tables(
        self,
        table_names: list[str],
        *,
        routing_intent: RoutingIntent | None = None,
    ) -> ResolvedRoute:
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

        capability_profiles = {
            engine_id: self.engine_service.get_capability_profile(engine_id)
            for engine_id in common_engines
        }

        # Step 4: score candidates with binding, capability, and optional semantic intent.
        candidate_scores: list[dict[str, Any]] = []
        for engine_id in common_engines:
            capability_profile = capability_profiles[engine_id]
            priority_score = sum(engine_priorities.get(engine_id, {}).values())
            capability_score = score_capability_profile(
                capability_profile,
                table_count=len(table_names),
            )
            fit_detail = (
                describe_routing_fit(
                    capability_profile,
                    table_count=len(table_names),
                    step_type=routing_intent.step_type,
                    metric_names=routing_intent.metric_names,
                    requested_dimensions=routing_intent.requested_dimensions,
                    compatible_dimensions=routing_intent.compatible_dimensions,
                    policy_hints=routing_intent.policy_hints,
                )
                if routing_intent is not None
                else {
                    "step_type_supported": True,
                    "satisfied_policy_support": [],
                    "missing_policy_support": [],
                    "requested_dimension_count": 0,
                    "compatible_dimension_count": 0,
                    "unresolved_dimension_count": 0,
                    "metric_count": 0,
                    "step_score": 0,
                    "policy_score": 0,
                    "semantic_score": 0,
                    "cost_score": 0,
                    "reasons": [],
                }
            )
            total_score = (
                priority_score
                + capability_score
                + int(fit_detail["step_score"])
                + int(fit_detail["policy_score"])
                + int(fit_detail["semantic_score"])
                + int(fit_detail["cost_score"])
            )
            candidate_scores.append(
                {
                    "engine_id": engine_id,
                    "priority_score": priority_score,
                    "capability_score": capability_score,
                    "total_score": total_score,
                    "performance_class": capability_profile.performance_class,
                    "federation_support": capability_profile.federation_support,
                    "step_type_supported": fit_detail["step_type_supported"],
                    "satisfied_policy_support": fit_detail["satisfied_policy_support"],
                    "missing_policy_support": fit_detail["missing_policy_support"],
                    "step_score": fit_detail["step_score"],
                    "policy_score": fit_detail["policy_score"],
                    "semantic_score": fit_detail["semantic_score"],
                    "cost_score": fit_detail["cost_score"],
                    "reasons": list(fit_detail["reasons"]),
                }
            )

        candidate_scores.sort(
            key=lambda candidate: (
                candidate["total_score"],
                candidate["priority_score"],
                candidate["capability_score"],
            ),
            reverse=True,
        )
        selected_candidate = candidate_scores[0]
        best_engine_id = str(selected_candidate["engine_id"])

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
        capability_profile = capability_profiles[best_engine_id]
        return ResolvedRoute(
            engine=engine,
            engine_id=best_engine_id,
            qualified_names=qualified_names,
            capability_profile=capability_profile,
            capability_score=score_capability_profile(
                capability_profile,
                table_count=len(table_names),
            ),
            selection_reason=(
                selected_candidate["reasons"][0]
                if selected_candidate["reasons"]
                else "highest combined routing score"
            ),
            routing_detail={
                "strategy": (
                    "semantic_intent_and_capability"
                    if routing_intent is not None
                    else "binding_priority_and_capability"
                ),
                "intent": routing_intent.to_dict() if routing_intent is not None else None,
                "candidates": candidate_scores,
            },
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
