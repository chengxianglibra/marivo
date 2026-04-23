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


@dataclass(frozen=True)
class RoutingFailure:
    code: str
    message: str
    routing_detail: dict[str, Any] = field(default_factory=dict)


class RoutingResolutionError(ValueError):
    """Structured routing failure for callers that still rely on exceptions."""

    def __init__(self, failure: RoutingFailure) -> None:
        super().__init__(failure.message)
        self.code = failure.code
        self.routing_detail = failure.routing_detail


@dataclass
class RouteResolution:
    resolved: bool
    route: ResolvedRoute | None = None
    failure: RoutingFailure | None = None

    def require_route(self) -> ResolvedRoute:
        if self.resolved and self.route is not None:
            return self.route
        if self.failure is None:
            raise RoutingResolutionError(
                RoutingFailure(
                    code="routing_resolution_failed",
                    message="Routing did not produce a resolved route",
                    routing_detail={},
                )
            )
        raise RoutingResolutionError(self.failure)


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
        return self.resolve_route(table_names, routing_intent=routing_intent).require_route()

    def resolve_route(
        self,
        table_names: list[str],
        *,
        routing_intent: RoutingIntent | None = None,
    ) -> RouteResolution:
        if not table_names:
            return self._failure(
                code="routing_no_tables",
                message="No table names provided",
                table_names=table_names,
                resolution_status="no_tables",
            )

        resolved_tables: dict[str, dict[str, Any]] = {}
        source_tables: dict[str, list[str]] = {}
        for table_name in table_names:
            try:
                resolved_table = self._resolve_table_source_object(table_name)
            except KeyError as error:
                return self._failure(
                    code="routing_table_not_found",
                    message=str(error),
                    table_names=table_names,
                    unresolved_tables=[table_name],
                    resolution_status="table_lookup_failed",
                    routing_intent=routing_intent,
                )
            except ValueError as error:
                return self._failure(
                    code="routing_table_ambiguous",
                    message=str(error),
                    table_names=table_names,
                    unresolved_tables=[table_name],
                    resolution_status="table_lookup_failed",
                    routing_intent=routing_intent,
                )
            resolved_tables[table_name] = resolved_table
            source_tables.setdefault(str(resolved_table["source_id"]), []).append(table_name)

        unique_sources = sorted(source_tables)
        engine_sets: dict[str, set[str]] = {}
        engine_priorities: dict[str, dict[str, int]] = {}
        mapping_details: dict[tuple[str, str], dict[str, Any]] = {}
        source_detail: dict[str, dict[str, Any]] = {}

        for source_id in unique_sources:
            source_result = self._collect_source_candidates(source_id)
            engine_sets[source_id] = source_result["engine_ids"]
            source_detail[source_id] = source_result["detail"]
            for engine_id, priority in source_result["engine_priorities"].items():
                engine_priorities.setdefault(engine_id, {})[source_id] = priority
            for engine_id, mapping in source_result["ready_mappings"].items():
                mapping_details[(source_id, engine_id)] = mapping
            if source_result["failure_code"] is not None:
                return self._failure(
                    code=source_result["failure_code"],
                    message=source_result["failure_message"],
                    table_names=table_names,
                    source_detail=source_detail,
                    unresolved_tables=list(source_tables[source_id]),
                    resolution_status=str(source_result["detail"]["resolution_status"]),
                    routing_intent=routing_intent,
                )

        candidate_scores = self._build_candidate_scores(
            table_count=len(table_names),
            unique_sources=unique_sources,
            engine_sets=engine_sets,
            engine_priorities=engine_priorities,
            mapping_details=mapping_details,
            routing_intent=routing_intent,
        )
        eligible_candidates = [candidate for candidate in candidate_scores if candidate["eligible"]]
        if not eligible_candidates:
            return self._failure(
                code="routing_no_common_engine",
                message=f"No common engine for tables {table_names}",
                table_names=table_names,
                source_detail=source_detail,
                candidate_scores=candidate_scores,
                resolution_status="no_common_engine",
                routing_intent=routing_intent,
            )

        selected_candidate = eligible_candidates[0]
        best_engine_id = str(selected_candidate["engine_id"])
        selected_mapping_ids = list(selected_candidate["mapping_ids"])

        qualified_names: dict[str, str] = {}
        resolved_execution_locators: dict[str, dict[str, Any]] = {}
        for table_name in table_names:
            resolved_table = resolved_tables[table_name]
            source_id = str(resolved_table["source_id"])
            mapping = mapping_details[(source_id, best_engine_id)]
            try:
                execution_locator = self.resolve_execution_locator(resolved_table, mapping)
            except ValueError as error:
                failure_code = self._failure_code_from_message(str(error))
                blocker = self._mapping_projection_blocker(
                    table_name=table_name,
                    source_id=source_id,
                    engine_id=best_engine_id,
                    mapping_id=str(mapping["mapping_id"]),
                    message=str(error),
                )
                return self._failure(
                    code=failure_code,
                    message=str(error),
                    table_names=table_names,
                    source_detail=source_detail,
                    candidate_scores=candidate_scores,
                    execution_locators=resolved_execution_locators,
                    selected_mapping_ids=selected_mapping_ids,
                    unresolved_tables=[table_name],
                    resolution_status="execution_locator_failed",
                    extra_readiness_blockers=[blocker],
                    routing_intent=routing_intent,
                )
            qualified_names[table_name] = self.qualify_table_name(execution_locator)
            resolved_execution_locators[table_name] = execution_locator

        engine = self.engine_service.build_analytics_engine(best_engine_id)
        capability_profile = self.engine_service.get_capability_profile(best_engine_id)
        routing_detail = self._build_routing_detail(
            table_names=table_names,
            resolution_status="resolved",
            source_detail=source_detail,
            candidate_scores=candidate_scores,
            execution_locators=resolved_execution_locators,
            selected_mapping_ids=selected_mapping_ids,
            routing_intent=routing_intent,
        )
        return RouteResolution(
            resolved=True,
            route=ResolvedRoute(
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
                routing_detail=routing_detail,
            ),
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

    def _collect_source_candidates(self, source_id: str) -> dict[str, Any]:
        mappings = self.mapping_service.list_mappings(source_id=source_id, status="active")
        detail: dict[str, Any] = {
            "candidate_engine_ids": [],
            "active_mapping_ids": [str(mapping["mapping_id"]) for mapping in mappings],
            "ready_mapping_ids": [],
            "failed_mappings": [],
            "readiness_blockers": [],
            "resolution_status": "ready_candidates",
        }
        if not mappings:
            blocker = {
                "kind": "mapping_missing",
                "source_id": source_id,
                "failure_code": "mapping_missing",
                "message": f"Source '{source_id}' has no active execution mappings",
            }
            detail["readiness_blockers"] = [blocker]
            detail["resolution_status"] = "no_active_mappings"
            return {
                "engine_ids": set(),
                "engine_priorities": {},
                "ready_mappings": {},
                "detail": detail,
                "failure_code": "routing_source_unmapped",
                "failure_message": blocker["message"],
            }

        engine_ids: set[str] = set()
        engine_priorities: dict[str, int] = {}
        ready_mappings: dict[str, dict[str, Any]] = {}
        failed_mappings: list[dict[str, Any]] = []
        blockers: list[dict[str, Any]] = []
        for mapping in mappings:
            mapping_id = str(mapping["mapping_id"])
            engine_id = str(mapping["engine_id"])
            if mapping["readiness_status"] != "ready":
                failure_code = str(mapping.get("failure_code") or "mapping_inactive_dependency")
                failed_entry = {
                    "mapping_id": mapping_id,
                    "engine_id": engine_id,
                    "failure_code": failure_code,
                    "kind": "mapping_not_ready",
                }
                failed_mappings.append(failed_entry)
                blockers.append(
                    {
                        "kind": "mapping_not_ready",
                        "source_id": source_id,
                        "mapping_id": mapping_id,
                        "engine_id": engine_id,
                        "failure_code": failure_code,
                        "message": f"Mapping '{mapping_id}' is not ready for source '{source_id}'",
                    }
                )
                continue
            engine_info = self.engine_service.get_engine(engine_id)
            if engine_info["readiness_status"] != "ready":
                failure_code = str(engine_info.get("failure_code") or "engine_not_ready")
                failed_entry = {
                    "mapping_id": mapping_id,
                    "engine_id": engine_id,
                    "failure_code": failure_code,
                    "kind": "engine_not_ready",
                }
                failed_mappings.append(failed_entry)
                blockers.append(
                    {
                        "kind": "engine_not_ready",
                        "source_id": source_id,
                        "mapping_id": mapping_id,
                        "engine_id": engine_id,
                        "failure_code": failure_code,
                        "message": f"Engine '{engine_id}' is not ready for mapping '{mapping_id}'",
                    }
                )
                continue
            engine_ids.add(engine_id)
            engine_priorities[engine_id] = int(mapping["priority"])
            ready_mappings[engine_id] = mapping

        detail["candidate_engine_ids"] = sorted(engine_ids)
        detail["ready_mapping_ids"] = [
            str(ready_mappings[engine_id]["mapping_id"]) for engine_id in sorted(engine_ids)
        ]
        detail["failed_mappings"] = failed_mappings
        detail["readiness_blockers"] = blockers
        if engine_ids:
            return {
                "engine_ids": engine_ids,
                "engine_priorities": engine_priorities,
                "ready_mappings": ready_mappings,
                "detail": detail,
                "failure_code": None,
                "failure_message": None,
            }

        detail["resolution_status"] = "no_ready_mappings"
        failure_suffix = ", ".join(
            f"{entry['mapping_id']}:{entry['failure_code']}" for entry in failed_mappings
        )
        return {
            "engine_ids": set(),
            "engine_priorities": {},
            "ready_mappings": {},
            "detail": detail,
            "failure_code": "routing_source_unavailable",
            "failure_message": (
                f"Source '{source_id}' has no ready execution mappings"
                + (f" ({failure_suffix})" if failure_suffix else "")
            ),
        }

    def _build_candidate_scores(
        self,
        *,
        table_count: int,
        unique_sources: list[str],
        engine_sets: dict[str, set[str]],
        engine_priorities: dict[str, dict[str, int]],
        mapping_details: dict[tuple[str, str], dict[str, Any]],
        routing_intent: RoutingIntent | None,
    ) -> list[dict[str, Any]]:
        all_engine_ids = sorted(
            {engine_id for engines in engine_sets.values() for engine_id in engines}
        )
        candidate_scores: list[dict[str, Any]] = []
        for engine_id in all_engine_ids:
            covered_sources = sorted(
                source_id
                for source_id in unique_sources
                if engine_id in engine_sets.get(source_id, set())
            )
            missing_sources = sorted(
                source_id for source_id in unique_sources if source_id not in covered_sources
            )
            capability_profile = self.engine_service.get_capability_profile(engine_id)
            priority_score = sum(engine_priorities.get(engine_id, {}).values())
            capability_score = score_capability_profile(
                capability_profile,
                table_count=table_count,
            )
            fit_detail = self._routing_fit_detail(
                capability_profile=capability_profile,
                table_count=table_count,
                routing_intent=routing_intent,
            )
            total_score = (
                priority_score
                + capability_score
                + int(fit_detail["step_score"])
                + int(fit_detail["policy_score"])
                + int(fit_detail["semantic_score"])
                + int(fit_detail["cost_score"])
            )
            reasons = list(fit_detail["reasons"])
            if missing_sources:
                reasons.append("missing mappings for sources: " + ", ".join(missing_sources))
            candidate_scores.append(
                {
                    "engine_id": engine_id,
                    "eligible": not missing_sources,
                    "covered_sources": covered_sources,
                    "missing_sources": missing_sources,
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
                    "reasons": reasons,
                    "mapping_ids": [
                        str(mapping_details[(source_id, engine_id)]["mapping_id"])
                        for source_id in covered_sources
                    ],
                }
            )
        candidate_scores.sort(
            key=lambda candidate: (
                candidate["eligible"],
                candidate["total_score"],
                candidate["priority_score"],
                candidate["capability_score"],
            ),
            reverse=True,
        )
        return candidate_scores

    def _routing_fit_detail(
        self,
        *,
        capability_profile: EngineCapabilityProfile,
        table_count: int,
        routing_intent: RoutingIntent | None,
    ) -> RoutingFitDetail:
        if routing_intent is None:
            return {
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
        return describe_routing_fit(
            capability_profile,
            table_count=table_count,
            step_type=routing_intent.step_type,
            metric_names=routing_intent.metric_names,
            requested_dimensions=routing_intent.requested_dimensions,
            compatible_dimensions=routing_intent.compatible_dimensions,
            policy_hints=routing_intent.policy_hints,
        )

    def _failure(
        self,
        *,
        code: str,
        message: str,
        table_names: list[str],
        resolution_status: str,
        source_detail: dict[str, dict[str, Any]] | None = None,
        candidate_scores: list[dict[str, Any]] | None = None,
        execution_locators: dict[str, dict[str, Any]] | None = None,
        selected_mapping_ids: list[str] | None = None,
        unresolved_tables: list[str] | None = None,
        extra_readiness_blockers: list[dict[str, Any]] | None = None,
        routing_intent: RoutingIntent | None = None,
    ) -> RouteResolution:
        routing_detail = self._build_routing_detail(
            table_names=table_names,
            resolution_status=resolution_status,
            source_detail=source_detail or {},
            candidate_scores=candidate_scores or [],
            execution_locators=execution_locators or {},
            selected_mapping_ids=selected_mapping_ids or [],
            unresolved_tables=unresolved_tables or [],
            extra_readiness_blockers=extra_readiness_blockers or [],
            routing_intent=routing_intent,
        )
        return RouteResolution(
            resolved=False,
            failure=RoutingFailure(
                code=code,
                message=message,
                routing_detail=routing_detail,
            ),
        )

    def _build_routing_detail(
        self,
        *,
        table_names: list[str],
        resolution_status: str,
        source_detail: dict[str, dict[str, Any]],
        candidate_scores: list[dict[str, Any]],
        execution_locators: dict[str, dict[str, Any]],
        selected_mapping_ids: list[str],
        unresolved_tables: list[str] | None = None,
        extra_readiness_blockers: list[dict[str, Any]] | None = None,
        routing_intent: RoutingIntent | None = None,
    ) -> dict[str, Any]:
        readiness_blockers: list[dict[str, Any]] = []
        for detail in source_detail.values():
            blockers = detail.get("readiness_blockers", [])
            if isinstance(blockers, list):
                readiness_blockers.extend(
                    blocker for blocker in blockers if isinstance(blocker, dict)
                )
        if extra_readiness_blockers:
            readiness_blockers.extend(extra_readiness_blockers)
        return {
            "strategy": (
                "semantic_intent_and_capability"
                if routing_intent is not None
                else "mapping_priority_and_capability"
            ),
            "intent": routing_intent.to_dict() if routing_intent is not None else None,
            "table_names": list(table_names),
            "sources": source_detail,
            "candidates": candidate_scores,
            "resolution_status": resolution_status,
            "unresolved_tables": list(unresolved_tables or []),
            "execution_locators": execution_locators,
            "selected_mapping_ids": list(selected_mapping_ids),
            "readiness_blockers": readiness_blockers,
        }

    def _mapping_projection_blocker(
        self,
        *,
        table_name: str,
        source_id: str,
        engine_id: str,
        mapping_id: str,
        message: str,
    ) -> dict[str, Any]:
        failure_code = self._failure_code_from_message(message)
        return {
            "kind": "execution_locator_invalid",
            "source_id": source_id,
            "engine_id": engine_id,
            "mapping_id": mapping_id,
            "table_name": table_name,
            "failure_code": failure_code,
            "message": message,
        }

    def _failure_code_from_message(self, message: str) -> str:
        code, _, _ = message.partition(":")
        return code.strip() or "routing_resolution_failed"

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
            default_schema_applied = False
        else:
            schema = default_schema
            if schema is None:
                raise ValueError(
                    f"mapping_invalid_namespace: mapping '{mapping['mapping_id']}' needs "
                    "default_schema when authority schema is missing"
                )
            default_schema_applied = True

        return {
            "catalog": execution_catalog,
            "schema": schema,
            "table": authority_locator.get("table") or table_source_object.get("native_name"),
            "mapping_id": mapping["mapping_id"],
            "authority_catalog": authority_catalog,
            "execution_catalog": execution_catalog,
            "default_schema_applied": default_schema_applied,
            "readiness_blockers": [],
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
        locator_rows = self._lookup_table_rows_by_authority_locator(table_name)
        if locator_rows:
            return self._select_table_row_for_locator_lookup(table_name, locator_rows)

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

    def _lookup_table_rows_by_authority_locator(self, table_name: str) -> list[dict[str, Any]]:
        parts = table_name.split(".")
        if len(parts) == 3:
            sql = """
                SELECT object_id, source_id, parent_id, native_name, fqn, authority_locator_json, updated_at
                FROM source_objects
                WHERE object_type = 'table'
                  AND json_extract(authority_locator_json, '$.catalog') = ?
                  AND json_extract(authority_locator_json, '$.schema') = ?
                  AND json_extract(authority_locator_json, '$.table') = ?
                ORDER BY updated_at DESC, object_id
            """
            params: list[Any] = parts
        elif len(parts) == 2:
            sql = """
                SELECT object_id, source_id, parent_id, native_name, fqn, authority_locator_json, updated_at
                FROM source_objects
                WHERE object_type = 'table'
                  AND json_extract(authority_locator_json, '$.schema') = ?
                  AND json_extract(authority_locator_json, '$.table') = ?
                ORDER BY updated_at DESC, object_id
            """
            params = parts
        elif len(parts) == 1:
            sql = """
                SELECT object_id, source_id, parent_id, native_name, fqn, authority_locator_json, updated_at
                FROM source_objects
                WHERE object_type = 'table'
                  AND json_extract(authority_locator_json, '$.table') = ?
                ORDER BY updated_at DESC, object_id
            """
            params = parts
        else:
            return []
        return [dict(row) for row in self.metadata.query_rows(sql, params)]

    def _select_table_row_for_locator_lookup(
        self, table_name: str, rows: list[dict[str, Any]]
    ) -> dict[str, Any]:
        if len(rows) == 1:
            return self._row_to_source_object(rows[0])

        if len(table_name.split(".")) == 3:
            matching_sources = ", ".join(sorted({str(row["source_id"]) for row in rows}))
            raise ValueError(
                "Ambiguous table name in source_objects; full authority locator matches multiple "
                f"sources: {table_name} -> {matching_sources}"
            )

        unique_locators = {
            self.qualify_table_name(self._row_to_source_object(row)["authority_locator"])
            for row in rows
        }
        if len(unique_locators) == 1:
            return self._row_to_source_object(rows[0])

        matching_locators = ", ".join(sorted(unique_locators))
        raise ValueError(
            "Ambiguous table name in source_objects; use full authority locator FQN: "
            f"{table_name} -> {matching_locators}"
        )

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
