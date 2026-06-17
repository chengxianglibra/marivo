"""Analysis-owned connection runtime and query-capture bookkeeping."""

from __future__ import annotations

from typing import Any

from marivo.datasource.runtime import DatasourceConnectionService
from marivo.datasource.timezone import DatasourceEngineTimezone


class AnalysisConnectionRuntime:
    """Wrap datasource connections with analysis-only execution bookkeeping."""

    def __init__(self, service: DatasourceConnectionService) -> None:
        self.service = service
        self._validated: set[str] = set()
        self._capture_buffer: list[Any] | None = None

    def session_backend(self, datasource_name: str) -> Any:
        return self.service.session_backend(datasource_name)

    def engine_timezone(self, datasource_name: str) -> DatasourceEngineTimezone:
        return self.service.engine_timezone(datasource_name)

    def get_or_create(self, datasource_name: str) -> Any:
        try:
            return self.session_backend(datasource_name)
        except Exception as exc:
            from marivo.analysis.errors import NoBackendFactoryError
            from marivo.datasource.errors import DatasourceMissingError

            if not isinstance(exc, DatasourceMissingError):
                raise
            service = self.service
            has_overrides = bool(getattr(service, "_backend_overrides", {}))
            has_factory = getattr(service, "_backend_factory", None) is not None
            uses_datasources = bool(getattr(service, "_use_datasources", False))
            if has_overrides or has_factory or uses_datasources:
                raise
            raise NoBackendFactoryError(
                message="session has no backend_factory; data-materializing intents need one",
                details={"datasource": datasource_name},
                hint=(
                    "Register a project datasource and call mv.session.get_or_create(name=...), "
                    "or pass backends={...}/backend_factory=... only for explicit overrides."
                ),
            ) from exc

    def should_mark_validated(self, datasource_name: str) -> bool:
        return datasource_name not in self._validated

    def mark_validated(self, datasource_name: str) -> None:
        self._validated.add(datasource_name)

    def begin_query_capture(self) -> None:
        self._capture_buffer = []

    def record_query(self, query: Any) -> None:
        if self._capture_buffer is not None:
            self._capture_buffer.append(query)

    def take_captured_queries(self) -> list[Any]:
        if self._capture_buffer is None:
            return []
        queries = self._capture_buffer
        self._capture_buffer = None
        return queries

    def close_all(self) -> None:
        self.service.close_all()
        self._validated.clear()
        self._capture_buffer = None
