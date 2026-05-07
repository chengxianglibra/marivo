from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from fastapi import HTTPException, Request

from app.config import MarivoConfig
from app.datasources import DatasourceService
from app.observability import MetricsCollector
from app.routing import QueryRouter
from app.runtime.runtime import MarivoRuntime
from app.semantic_service_v2.service import SemanticModelV2Service
from app.service import SemanticLayerService
from app.storage.analytics import AnalyticsEngine
from app.storage.metadata import MetadataStore


@dataclass(slots=True)
class AppServices:
    resolved_path: Path | str  # Analytics path; str ":memory:" means in-memory DuckDB.
    config: MarivoConfig
    runtime: MarivoRuntime  # Phase 3: preferred entry point
    service: SemanticLayerService
    datasource_service: DatasourceService
    query_router: QueryRouter
    metadata_store: MetadataStore
    analytics_engine: AnalyticsEngine
    metrics: MetricsCollector | None
    semantic_v2_service: SemanticModelV2Service


def get_services(request: Request) -> AppServices:
    return cast("AppServices", request.app.state.services)


def http_error(error: KeyError | ValueError) -> HTTPException:
    if isinstance(error, KeyError):
        return HTTPException(status_code=404, detail=str(error))
    return HTTPException(status_code=400, detail=str(error))
