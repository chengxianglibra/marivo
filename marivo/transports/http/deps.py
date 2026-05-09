from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from fastapi import HTTPException, Request

from marivo.adapters.metadata import MetadataStore
from marivo.adapters.server.semantic_service_adapter import SemanticServiceAdapter
from marivo.config import MarivoConfig
from marivo.datasources import DatasourceService
from marivo.observability import MetricsCollector
from marivo.ports.analytics import AnalyticsEngine
from marivo.routing import QueryRouter
from marivo.runtime.runtime import MarivoRuntime


@dataclass(slots=True)
class AppServices:
    resolved_path: Path | str  # Analytics path; str ":memory:" means in-memory DuckDB.
    config: MarivoConfig
    runtime: MarivoRuntime  # Phase 3: preferred entry point
    datasource_service: DatasourceService
    query_router: QueryRouter
    metadata_store: MetadataStore
    analytics_engine: AnalyticsEngine
    metrics: MetricsCollector | None
    semantic_v2_service: SemanticServiceAdapter


def get_services(request: Request) -> AppServices:
    return cast("AppServices", request.app.state.services)


def http_error(error: KeyError | ValueError) -> HTTPException:
    if isinstance(error, KeyError):
        return HTTPException(status_code=404, detail=str(error))
    return HTTPException(status_code=400, detail=str(error))
