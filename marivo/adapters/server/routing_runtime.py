from __future__ import annotations

from dataclasses import dataclass

from marivo.routing import ResolvedRoute
from marivo.runtime.semantic.feedback import routing_feedback_from_error
from marivo.runtime_contracts import ExecutionFeedback
from marivo.storage.analytics import AnalyticsEngine

if False:  # pragma: no cover
    from marivo.routing import QueryRouter


@dataclass
class RoutingResolutionResult:
    engine: AnalyticsEngine
    datasource_type: str
    route: ResolvedRoute | None = None
    feedback: ExecutionFeedback | None = None
    fallback_used: bool = False


class RoutingRuntime:
    def __init__(
        self,
        query_router: QueryRouter | None,
        default_engine: AnalyticsEngine,
        default_datasource_type: str = "duckdb",
    ) -> None:
        self.query_router = query_router
        self.default_engine = default_engine
        self.default_datasource_type = default_datasource_type

    def resolve_tables(
        self,
        table_names: list[str],
        *,
        session_id: str | None = None,
    ) -> RoutingResolutionResult:
        if self.query_router is None:
            return RoutingResolutionResult(
                engine=self.default_engine,
                datasource_type=self.default_datasource_type,
            )

        try:
            if session_id is None:
                route = self.query_router.resolve_tables(table_names)
            else:
                route = self.query_router.resolve_tables(table_names, session_id=session_id)
            datasource = self.query_router.datasource_service.get_datasource(route.datasource_id)
            return RoutingResolutionResult(
                engine=route.require_engine(),
                datasource_type=str(datasource["datasource_type"]),
                route=route,
            )
        except (KeyError, ValueError) as error:
            feedback = routing_feedback_from_error(error, table_names=table_names)
            return RoutingResolutionResult(
                engine=self.default_engine,
                datasource_type=self.default_datasource_type,
                feedback=feedback,
                fallback_used=True,
            )
