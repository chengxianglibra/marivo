"""Analysis-owned connection runtime and query-capture bookkeeping."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from marivo.analysis.event import EventWatermarkReceipt, EventWatermarkRequest
from marivo.datasource.runtime import DatasourceConnectionService
from marivo.datasource.timezone import DatasourceEngineTimezone


class AnalysisConnectionRuntime:
    """Wrap datasource connections with analysis-only execution bookkeeping."""

    def __init__(self, service: DatasourceConnectionService) -> None:
        self.service = service
        self._validated: set[str] = set()
        self._capture_buffer: list[Any] | None = None
        self._metric_artifact_cache: dict[tuple[str, str], str] = {}

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
                context={"datasource": datasource_name},
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

    def source_snapshot_token(self, datasource_name: str) -> str | None:
        """Return an exact provider-owned source revision, when available."""

        backend = self.get_or_create(datasource_name)
        provider = getattr(backend, "marivo_snapshot_token", None)
        if not callable(provider):
            return None
        token = provider()
        return token if isinstance(token, str) and token else None

    def event_watermark(
        self,
        datasource_name: str,
        request: EventWatermarkRequest,
    ) -> EventWatermarkReceipt | None:
        """Return one provider-owned Event completeness receipt, when available."""
        backend = self.get_or_create(datasource_name)
        provider = getattr(backend, "marivo_event_watermark", None)
        if not callable(provider):
            return None
        raw_receipt = cast(
            "Callable[[EventWatermarkRequest], object | None]",
            provider,
        )(request)
        if raw_receipt is None:
            return None
        try:
            return EventWatermarkReceipt.model_validate(raw_receipt)
        except (TypeError, ValueError):
            return None

    def cached_metric_artifact(self, cache_key: str, snapshot_token: str) -> str | None:
        return self._metric_artifact_cache.get((cache_key, snapshot_token))

    def remember_metric_artifact(
        self,
        cache_key: str,
        snapshot_token: str,
        artifact_ref: str,
    ) -> None:
        self._metric_artifact_cache[(cache_key, snapshot_token)] = artifact_ref

    def close_all(self) -> None:
        self.service.close_all()
        self._validated.clear()
        self._capture_buffer = None
        self._metric_artifact_cache.clear()
