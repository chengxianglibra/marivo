from __future__ import annotations

from dataclasses import dataclass

from app.execution.feedback import routing_feedback_from_error
from app.routing import ResolvedRoute
from app.runtime_contracts import ExecutionFeedback
from app.storage.analytics import AnalyticsEngine

if False:  # pragma: no cover
    from app.routing import QueryRouter


@dataclass
class RoutingResolutionResult:
    engine: AnalyticsEngine
    engine_type: str
    route: ResolvedRoute | None = None
    feedback: ExecutionFeedback | None = None
    fallback_used: bool = False


class RoutingRuntime:
    def __init__(
        self,
        query_router: QueryRouter | None,
        default_engine: AnalyticsEngine,
        default_engine_type: str = "duckdb",
    ) -> None:
        self.query_router = query_router
        self.default_engine = default_engine
        self.default_engine_type = default_engine_type

    def resolve_tables(self, table_names: list[str]) -> RoutingResolutionResult:
        if self.query_router is None:
            return RoutingResolutionResult(
                engine=self.default_engine,
                engine_type=self.default_engine_type,
            )

        try:
            route = self.query_router.resolve_tables(table_names)
            engine_info = self.query_router.engine_service.get_engine(route.engine_id)
            return RoutingResolutionResult(
                engine=route.engine,
                engine_type=str(engine_info["engine_type"]),
                route=route,
            )
        except (KeyError, ValueError) as error:
            feedback = routing_feedback_from_error(error, table_names=table_names)
            return RoutingResolutionResult(
                engine=self.default_engine,
                engine_type=self.default_engine_type,
                feedback=feedback,
                fallback_used=True,
            )
