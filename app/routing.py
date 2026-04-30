from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.datasources import DatasourceService
from app.source_object_locator import (
    normalize_source_object_authority_locator,
    qualify_execution_locator,
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
    """Result of route resolution for runtime or inspection callers."""

    datasource_id: str
    engine: AnalyticsEngine | None = None
    qualified_names: dict[str, str] = field(default_factory=dict)  # {native_name: qualified_name}
    selection_reason: str | None = None
    routing_detail: dict[str, Any] = field(default_factory=dict)

    def require_engine(self) -> AnalyticsEngine:
        if self.engine is None:
            raise ValueError("resolved route does not include a runtime engine")
        return self.engine


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
    """Resolve table names to a datasource for query execution."""

    def __init__(self, metadata: MetadataStore, datasource_service: DatasourceService) -> None:
        self.metadata = metadata
        self.datasource_service = datasource_service

    def resolve_engine_for_tables(
        self,
        table_names: list[str],
        *,
        routing_intent: RoutingIntent | None = None,
        session_id: str | None = None,
    ) -> AnalyticsEngine:
        """Given table names, find a common engine that can query all of them.

        Raises KeyError if a table is not found in source_objects.
        Raises ValueError if no single engine covers all tables.
        """
        route = self.resolve_tables(
            table_names,
            routing_intent=routing_intent,
            session_id=session_id,
        )
        return route.require_engine()

    def resolve_tables(
        self,
        table_names: list[str],
        *,
        routing_intent: RoutingIntent | None = None,
        session_id: str | None = None,
    ) -> ResolvedRoute:
        """Given table names, find a common datasource and return qualified names.

        Returns a ResolvedRoute with the engine, datasource_id, and a mapping
        from native table names to engine-qualified names.

        Raises KeyError if a table is not found in source_objects.
        Raises ValueError if tables belong to different datasources.
        """
        return self.resolve_route(
            table_names,
            routing_intent=routing_intent,
            session_id=session_id,
        ).require_route()

    def resolve_route(
        self,
        table_names: list[str],
        *,
        routing_intent: RoutingIntent | None = None,
        session_id: str | None = None,
        include_runtime_engine: bool = True,
    ) -> RouteResolution:
        if not table_names:
            return self._failure(
                code="routing_no_tables",
                message="No table names provided",
                table_names=table_names,
                resolution_status="no_tables",
            )

        resolved_tables: dict[str, dict[str, Any]] = {}
        datasource_tables: dict[str, list[str]] = {}
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
            datasource_tables.setdefault(str(resolved_table["datasource_id"]), []).append(
                table_name
            )

        unique_datasources = sorted(datasource_tables)

        # All tables must belong to the same datasource for a single-engine route.
        if len(unique_datasources) > 1:
            return self._failure(
                code="routing_no_common_engine",
                message=(
                    f"Tables belong to different datasources: "
                    f"{', '.join(f'{ds}={datasource_tables[ds]}' for ds in unique_datasources)}"
                ),
                table_names=table_names,
                resolution_status="multiple_datasources",
                routing_intent=routing_intent,
            )

        datasource_id = unique_datasources[0]

        # Check datasource readiness.
        try:
            datasource = self.datasource_service.get_datasource(datasource_id)
        except KeyError as error:
            return self._failure(
                code="routing_source_unmapped",
                message=str(error),
                table_names=table_names,
                resolution_status="datasource_not_found",
                routing_intent=routing_intent,
            )

        if datasource.get("readiness_status") != "ready":
            failure_code = str(datasource.get("failure_code") or "datasource_not_ready")
            return self._failure(
                code="routing_source_unavailable",
                message=(
                    f"Datasource '{datasource_id}' is not ready"
                    + (f" (failure_code={failure_code})" if failure_code else "")
                ),
                table_names=table_names,
                resolution_status="datasource_not_ready",
                routing_intent=routing_intent,
            )

        qualified_names: dict[str, str] = {}
        resolved_execution_locators: dict[str, dict[str, Any]] = {}
        for table_name in table_names:
            resolved_table = resolved_tables[table_name]
            execution_locator = self.resolve_execution_locator(resolved_table)
            qualified_names[table_name] = self.qualify_table_name_for_engine(
                datasource_id,
                execution_locator,
            )
            resolved_execution_locators[table_name] = execution_locator

        routing_detail = self._build_routing_detail(
            table_names=table_names,
            resolution_status="resolved",
            datasource_id=datasource_id,
            execution_locators=resolved_execution_locators,
            routing_intent=routing_intent,
        )
        engine = (
            self.datasource_service.build_analytics_engine(datasource_id, session_id=session_id)
            if include_runtime_engine
            else None
        )
        return RouteResolution(
            resolved=True,
            route=ResolvedRoute(
                datasource_id=datasource_id,
                engine=engine,
                qualified_names=qualified_names,
                selection_reason=f"resolved via datasource '{datasource_id}'",
                routing_detail=routing_detail,
            ),
        )

    def resolve_datasource_for_source(
        self,
        datasource_id: str,
        *,
        session_id: str | None = None,
    ) -> AnalyticsEngine:
        """Return an analytics engine for the given datasource.

        Raises ValueError if the datasource is not ready.
        """
        datasource = self.datasource_service.get_datasource(datasource_id)
        if datasource.get("readiness_status") != "ready":
            raise ValueError(
                f"Datasource '{datasource_id}' is not ready"
                + (
                    f" (failure_code={datasource.get('failure_code')})"
                    if datasource.get("failure_code")
                    else ""
                )
            )
        return self.datasource_service.build_analytics_engine(
            datasource_id,
            session_id=session_id,
        )

    def get_datasource_info_for_source(self, datasource_id: str) -> dict[str, Any] | None:
        """Return the datasource dict for the given datasource_id."""
        try:
            datasource = self.datasource_service.get_datasource(datasource_id)
        except KeyError:
            return None
        return {
            "datasource_id": datasource["datasource_id"],
            "datasource_type": datasource["datasource_type"],
            "display_name": datasource["display_name"],
        }

    def resolve_execution_locator(
        self,
        table_source_object: dict[str, Any],
    ) -> dict[str, Any]:
        authority_locator = dict(table_source_object.get("authority_locator") or {})
        return {
            "catalog": authority_locator.get("catalog"),
            "schema": authority_locator.get("schema"),
            "table": authority_locator.get("table") or table_source_object.get("native_name"),
            "datasource_id": table_source_object["datasource_id"],
            "readiness_blockers": [],
            "authority_locator": authority_locator,
        }

    def qualify_table_name(self, execution_locator: dict[str, Any]) -> str:
        """Build an engine-qualified table reference from the resolved execution locator."""
        return qualify_execution_locator(execution_locator)

    def qualify_table_name_for_engine(
        self,
        datasource_id: str,
        execution_locator: dict[str, Any],
    ) -> str:
        datasource = self.datasource_service.get_datasource(datasource_id)
        return qualify_execution_locator(
            execution_locator,
            engine_type=str(datasource.get("datasource_type") or ""),
        )

    def _resolve_table_source_object(self, table_name: str) -> dict[str, Any]:
        locator_rows = self._lookup_table_rows_by_authority_locator(table_name)
        if locator_rows:
            return self._select_table_row_for_locator_lookup(table_name, locator_rows)

        short_name = table_name.split(".")[-1]
        rows = self.metadata.query_rows(
            """
            SELECT object_id, datasource_id, parent_id, native_name, fqn, authority_locator_json, updated_at
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
                SELECT object_id, datasource_id, parent_id, native_name, fqn, authority_locator_json, updated_at
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
                SELECT object_id, datasource_id, parent_id, native_name, fqn, authority_locator_json, updated_at
                FROM source_objects
                WHERE object_type = 'table'
                  AND json_extract(authority_locator_json, '$.schema') = ?
                  AND json_extract(authority_locator_json, '$.table') = ?
                ORDER BY updated_at DESC, object_id
            """
            params = parts
        elif len(parts) == 1:
            sql = """
                SELECT object_id, datasource_id, parent_id, native_name, fqn, authority_locator_json, updated_at
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
            matching_datasources = ", ".join(sorted({str(row["datasource_id"]) for row in rows}))
            raise ValueError(
                "Ambiguous table name in source_objects; full authority locator matches multiple "
                f"datasources: {table_name} -> {matching_datasources}"
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
        source_object["authority_locator"] = normalize_source_object_authority_locator(
            self.metadata,
            source_object,
        )
        return source_object

    def _failure(
        self,
        *,
        code: str,
        message: str,
        table_names: list[str],
        resolution_status: str,
        execution_locators: dict[str, dict[str, Any]] | None = None,
        unresolved_tables: list[str] | None = None,
        routing_intent: RoutingIntent | None = None,
    ) -> RouteResolution:
        routing_detail = self._build_routing_detail(
            table_names=table_names,
            resolution_status=resolution_status,
            execution_locators=execution_locators or {},
            unresolved_tables=unresolved_tables or [],
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
        datasource_id: str | None = None,
        execution_locators: dict[str, dict[str, Any]],
        unresolved_tables: list[str] | None = None,
        routing_intent: RoutingIntent | None = None,
    ) -> dict[str, Any]:
        return {
            "strategy": ("semantic_intent" if routing_intent is not None else "datasource_direct"),
            "intent": routing_intent.to_dict() if routing_intent is not None else None,
            "table_names": list(table_names),
            "datasource_id": datasource_id,
            "resolution_status": resolution_status,
            "unresolved_tables": list(unresolved_tables or []),
            "execution_locators": execution_locators,
        }
