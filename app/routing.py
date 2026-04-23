from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from app.engines import EngineService
from app.execution.capabilities import (
    EngineCapabilityProfile,
    RoutingFitDetail,
    describe_routing_fit,
    score_capability_profile,
)
from app.mappings import MappingService
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
    """Resolve table names to an execution engine via source execution mappings."""

    def __init__(self, metadata: MetadataStore, engine_service: EngineService) -> None:
        self.metadata = metadata
        self.engine_service = engine_service
        self.mapping_service = MappingService(metadata)

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

        # Step 1: resolve each table to its source_id and authority locator
        resolved_tables: dict[str, dict[str, Any]] = {}
        for table_name in table_names:
            resolved_tables[table_name] = self._resolve_table_source_object(table_name)

        # Step 2: for each unique source, get candidate engine_ids and mapping info
        unique_sources = {str(table["source_id"]) for table in resolved_tables.values()}
        engine_sets: dict[str, set[str]] = {}
        engine_priorities: dict[str, dict[str, int]] = {}  # engine_id -> source_id -> priority
        mapping_details: dict[tuple[str, str], dict[str, Any]] = {}
        source_detail: dict[str, dict[str, Any]] = {}

        for source_id in unique_sources:
            mappings = self.mapping_service.list_mappings(source_id=source_id, status="active")
            if not mappings:
                raise ValueError(f"Source '{source_id}' has no active execution mappings")
            engine_ids = set()
            failed_mappings: list[dict[str, Any]] = []
            for mapping in mappings:
                if mapping["readiness_status"] != "ready":
                    failed_mappings.append(
                        {
                            "mapping_id": mapping["mapping_id"],
                            "engine_id": mapping["engine_id"],
                            "failure_code": mapping.get("failure_code"),
                        }
                    )
                    continue
                engine_id = str(mapping["engine_id"])
                engine_info = self.engine_service.get_engine(engine_id)
                if engine_info["readiness_status"] != "ready":
                    failed_mappings.append(
                        {
                            "mapping_id": mapping["mapping_id"],
                            "engine_id": mapping["engine_id"],
                            "failure_code": engine_info.get("failure_code") or "engine_not_ready",
                        }
                    )
                    continue
                engine_ids.add(engine_id)
                engine_priorities.setdefault(engine_id, {})[source_id] = int(mapping["priority"])
                mapping_details[(source_id, engine_id)] = mapping
            if not engine_ids:
                detail = ", ".join(
                    f"{entry['mapping_id']}:{entry['failure_code'] or 'not_ready'}"
                    for entry in failed_mappings
                )
                raise ValueError(
                    f"Source '{source_id}' has no ready execution mappings"
                    + (f" ({detail})" if detail else "")
                )
            engine_sets[source_id] = engine_ids
            source_detail[source_id] = {
                "candidate_engine_ids": sorted(engine_ids),
                "failed_mappings": failed_mappings,
            }

        # Step 3: intersect engine sets across all sources
        common_engines = engine_sets[next(iter(engine_sets))]
        for _, engines in engine_sets.items():
            common_engines = common_engines & engines

        if not common_engines:
            detail_parts = []
            for source_id, engines in engine_sets.items():
                detail_parts.append(f"source '{source_id}' → engines {sorted(engines)}")
            raise ValueError(
                f"No common engine for tables {table_names}. Mappings: {'; '.join(detail_parts)}"
            )

        capability_profiles = {
            engine_id: self.engine_service.get_capability_profile(engine_id)
            for engine_id in common_engines
        }

        # Step 4: score candidates with mapping, capability, and optional semantic intent.
        candidate_scores: list[dict[str, Any]] = []
        for engine_id in common_engines:
            capability_profile = capability_profiles[engine_id]
            priority_score = sum(engine_priorities.get(engine_id, {}).values())
            capability_score = score_capability_profile(
                capability_profile,
                table_count=len(table_names),
            )
            fit_detail: RoutingFitDetail = (
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
                    "mapping_ids": [
                        str(mapping_details[(source_id, engine_id)]["mapping_id"])
                        for source_id in sorted(unique_sources)
                    ],
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

        # Step 5: build execution-qualified names using mapping resolution
        qualified_names: dict[str, str] = {}
        resolved_execution_locators: dict[str, dict[str, Any]] = {}
        for table_name in table_names:
            resolved_table = resolved_tables[table_name]
            source_id = str(resolved_table["source_id"])
            mapping = mapping_details[(source_id, best_engine_id)]
            execution_locator = self.resolve_execution_locator(
                resolved_table,
                mapping,
            )
            qualified_names[table_name] = self.qualify_table_name(execution_locator)
            resolved_execution_locators[table_name] = execution_locator

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
                    else "mapping_priority_and_capability"
                ),
                "intent": routing_intent.to_dict() if routing_intent is not None else None,
                "sources": source_detail,
                "candidates": candidate_scores,
                "resolution_status": "resolved",
                "execution_locators": resolved_execution_locators,
            },
        )

    def resolve_engine_for_source(self, source_id: str) -> AnalyticsEngine:
        """Return the highest-priority ready engine mapped to a source.

        Raises ValueError if no ready mappings exist for the source.
        """
        mappings, detail = self._ready_mappings_for_source(source_id)
        if not mappings:
            raise ValueError(
                f"Source '{source_id}' has no ready execution mappings"
                + (f" ({detail})" if detail else "")
            )
        return self.engine_service.build_analytics_engine(str(mappings[0]["engine_id"]))

    def get_engine_info_for_source(self, source_id: str) -> dict[str, Any] | None:
        """Return the highest-priority ready engine dict (not instance) for a source."""
        mappings, _ = self._ready_mappings_for_source(source_id)
        if not mappings:
            return None
        best = mappings[0]
        engine = self.engine_service.get_engine(str(best["engine_id"]))
        return {
            "engine_id": engine["engine_id"],
            "engine_type": engine["engine_type"],
            "display_name": engine["display_name"],
            "priority": best["priority"],
            "mapping_id": best["mapping_id"],
        }

    def _ready_mappings_for_source(self, source_id: str) -> tuple[list[dict[str, Any]], str | None]:
        ready_mappings: list[dict[str, Any]] = []
        failed_entries: list[str] = []
        for mapping in self.mapping_service.list_mappings(source_id=source_id, status="active"):
            if mapping["readiness_status"] != "ready":
                failed_entries.append(
                    f"{mapping['mapping_id']}:{mapping.get('failure_code') or 'not_ready'}"
                )
                continue
            engine = self.engine_service.get_engine(str(mapping["engine_id"]))
            if engine["readiness_status"] != "ready":
                failed_entries.append(
                    f"{mapping['mapping_id']}:{engine.get('failure_code') or 'engine_not_ready'}"
                )
                continue
            ready_mappings.append(mapping)

        detail = ", ".join(failed_entries) if failed_entries else None
        return ready_mappings, detail

    def resolve_execution_locator(
        self,
        table_source_object: dict[str, Any],
        mapping: dict[str, Any],
    ) -> dict[str, Any]:
        authority_locator = dict(table_source_object.get("authority_locator") or {})
        authority_catalog = authority_locator.get("catalog")
        if not isinstance(authority_catalog, str) or not authority_catalog:
            raise ValueError(
                "mapping_invalid_namespace: source object is missing authority catalog"
            )

        source_catalogs = self._source_authority_catalogs(str(table_source_object["source_id"]))
        mapped_catalogs = {
            str(item["authority_catalog"]) for item in mapping.get("catalog_mappings", [])
        }
        if source_catalogs and not source_catalogs.issubset(mapped_catalogs):
            raise ValueError(
                f"mapping_incomplete: mapping '{mapping['mapping_id']}' does not cover "
                f"source '{table_source_object['source_id']}' authority catalogs"
            )

        matched = next(
            (
                item
                for item in mapping.get("catalog_mappings", [])
                if item.get("authority_catalog") == authority_catalog
            ),
            None,
        )
        if matched is None:
            raise ValueError(
                f"mapping_missing: mapping '{mapping['mapping_id']}' has no entry for "
                f"authority catalog '{authority_catalog}'"
            )

        execution_catalog = matched.get("execution_catalog")
        if not isinstance(execution_catalog, str) or not execution_catalog:
            raise ValueError(
                f"mapping_invalid_namespace: mapping '{mapping['mapping_id']}' is missing "
                f"execution_catalog for authority catalog '{authority_catalog}'"
            )

        authority_schema = authority_locator.get("schema")
        default_schema = matched.get("default_schema")
        if authority_schema is not None:
            if default_schema is not None:
                raise ValueError(
                    f"mapping_invalid_namespace: mapping '{mapping['mapping_id']}' cannot "
                    "override an existing authority schema with default_schema"
                )
            schema = authority_schema
        else:
            schema = default_schema
            if schema is None:
                raise ValueError(
                    f"mapping_invalid_namespace: mapping '{mapping['mapping_id']}' needs "
                    "default_schema when authority schema is missing"
                )

        return {
            "catalog": execution_catalog,
            "schema": schema,
            "table": authority_locator.get("table") or table_source_object.get("native_name"),
            "mapping_id": mapping["mapping_id"],
            "authority_locator": authority_locator,
        }

    def qualify_table_name(self, execution_locator: dict[str, Any]) -> str:
        """Build an engine-qualified table reference from the resolved execution locator."""
        parts = [
            str(value)
            for key in ("catalog", "schema", "table")
            for value in [execution_locator.get(key)]
            if isinstance(value, str) and value
        ]
        return ".".join(parts)

    def _resolve_table_source_object(self, table_name: str) -> dict[str, Any]:
        short_name = table_name.split(".")[-1]
        rows = self.metadata.query_rows(
            """
            SELECT object_id, source_id, parent_id, native_name, fqn, authority_locator_json, updated_at
            FROM source_objects
            WHERE object_type = 'table' AND (fqn = ? OR native_name = ?)
            ORDER BY CASE WHEN fqn = ? THEN 0 ELSE 1 END, updated_at DESC, object_id
            """,
            [table_name, short_name, table_name],
        )
        if not rows:
            raise KeyError(f"Table not found in source_objects: {table_name}")

        matched_fqn_rows = [dict(row) for row in rows if str(row["fqn"]) == table_name]
        if matched_fqn_rows:
            return self._row_to_source_object(matched_fqn_rows[0])

        if len(rows) > 1:
            if len({str(row["fqn"]) for row in rows}) == 1:
                return self._row_to_source_object(dict(rows[0]))
            matching_fqns = ", ".join(str(row["fqn"]) for row in rows)
            raise ValueError(
                "Ambiguous table name in source_objects; use full FQN: "
                f"{table_name} -> {matching_fqns}"
            )

        return self._row_to_source_object(dict(rows[0]))

    def _row_to_source_object(self, row: dict[str, Any]) -> dict[str, Any]:
        source_object = dict(row)
        source_object["authority_locator"] = json.loads(str(row["authority_locator_json"]))
        return source_object

    def _source_authority_catalogs(self, source_id: str) -> set[str]:
        rows = self.metadata.query_rows(
            """
            SELECT authority_locator_json
            FROM source_objects
            WHERE source_id = ? AND object_type = 'table'
            """,
            [source_id],
        )
        catalogs: set[str] = set()
        for row in rows:
            locator = json.loads(str(row["authority_locator_json"]))
            catalog = locator.get("catalog")
            if isinstance(catalog, str) and catalog:
                catalogs.add(catalog)
        return catalogs
