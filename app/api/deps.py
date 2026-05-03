from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from fastapi import HTTPException, Request

from app.approvals import ApprovalService
from app.config import MarivoConfig
from app.datasources import DatasourceService
from app.governance import GovernanceService
from app.jobs import JobService
from app.observability import MetricsCollector
from app.routing import QueryRouter
from app.semantic_service_v2.service import SemanticModelV2Service
from app.semantic_service_v2.session import SessionService
from app.service import SemanticLayerService
from app.storage.analytics import AnalyticsEngine
from app.storage.metadata import MetadataStore
from app.storage.repositories import JobRepository


@dataclass(slots=True)
class AppServices:
    resolved_path: Path | str  # Analytics path; str ":memory:" means in-memory DuckDB.
    config: MarivoConfig
    service: SemanticLayerService
    datasource_service: DatasourceService
    query_router: QueryRouter
    metadata_store: MetadataStore
    analytics_engine: AnalyticsEngine
    governance_service: GovernanceService | None
    approval_service: ApprovalService
    metrics: MetricsCollector | None
    job_service: JobService
    job_repository: JobRepository
    semantic_v2_service: SemanticModelV2Service
    session_service: SessionService


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
