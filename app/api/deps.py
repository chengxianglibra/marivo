from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from fastapi import HTTPException, Request

from app.approvals import ApprovalService
from app.bindings import BindingService
from app.config import FactumConfig
from app.engines import EngineService
from app.governance import GovernanceService
from app.jobs import JobService
from app.observability import MetricsCollector
from app.routing import QueryRouter
from app.semantic import SemanticService
from app.semantic_runtime.catalog import CatalogRuntimeService
from app.service import SemanticLayerService
from app.sources import SourceService
from app.storage.analytics import AnalyticsEngine
from app.storage.metadata import MetadataStore
from app.storage.repositories import JobRepository
from app.sync import SyncEngine


@dataclass(slots=True)
class AppServices:
    resolved_path: Path
    config: FactumConfig
    service: SemanticLayerService
    source_service: SourceService
    sync_engine: SyncEngine
    engine_service: EngineService
    binding_service: BindingService
    query_router: QueryRouter
    metadata_store: MetadataStore
    analytics_engine: AnalyticsEngine
    governance_service: GovernanceService | None
    approval_service: ApprovalService
    metrics: MetricsCollector | None
    job_service: JobService
    job_repository: JobRepository
    semantic_service: SemanticService
    catalog_runtime: CatalogRuntimeService
    admin_enabled: bool
    user_enabled: bool
    reflection_enabled: bool
    static_dir: Path


def get_services(request: Request) -> AppServices:
    return cast("AppServices", request.app.state.services)


def require_governance(services: AppServices) -> GovernanceService:
    if services.governance_service is None:
        raise HTTPException(status_code=400, detail="Governance is disabled")
    return services.governance_service


def http_error(error: KeyError | ValueError) -> HTTPException:
    if isinstance(error, KeyError):
        return HTTPException(status_code=404, detail=str(error))
    return HTTPException(status_code=400, detail=str(error))
