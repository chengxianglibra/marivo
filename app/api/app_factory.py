from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.requests import Request
from starlette.responses import Response

from app.api.deps import AppServices
from app.api.errors import (
    GuidedValidationError,
    guided_validation_exception_handler,
    request_validation_exception_handler,
)
from app.api.router import include_api_routers
from app.approvals import ApprovalService
from app.config import MarivoConfig, load_config, resolve_config_path
from app.engines import EngineService
from app.governance import GovernanceService
from app.jobs import JobService
from app.mappings import MappingService
from app.observability import MetricsCollector, TimingMiddleware, setup_logging
from app.routing import QueryRouter
from app.semantic import SemanticService
from app.semantic_runtime import CatalogRuntimeService
from app.service import SemanticLayerService
from app.sources import SourceService
from app.storage.analytics import AnalyticsEngine
from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from app.storage.metadata import MetadataStore
from app.storage.repositories import JobRepository
from app.storage.sqlite_metadata import SQLiteMetadataStore
from app.sync import SyncEngine

logger = logging.getLogger(__name__)


def _resolve_storage(
    db_path: str | Path | None,
    metadata_store: MetadataStore | None,
    analytics_engine: AnalyticsEngine | None,
    config: MarivoConfig,
    config_path: Path,
    config_path_explicit: bool,
) -> tuple[Path | str, MetadataStore, AnalyticsEngine]:
    if db_path is not None:
        resolved_path: Path | str = Path(db_path)
    else:
        resolved_path = ":memory:"
    created_analytics_engine = analytics_engine is None
    if metadata_store is None:
        metadata_config = config.metadata
        if db_path is not None and not config_path_explicit:
            metadata_store = SQLiteMetadataStore(Path(resolved_path).with_suffix(".meta.sqlite"))
        elif metadata_config is not None and metadata_config.path.strip():
            metadata_path = Path(metadata_config.path)
            if not metadata_path.is_absolute():
                metadata_path = config_path.parent / metadata_path
            metadata_store = SQLiteMetadataStore(metadata_path)
        else:
            raise RuntimeError(
                "Marivo config must define metadata.engine=sqlite and metadata.path when "
                "metadata_store is not provided"
            )
    if analytics_engine is None:
        analytics_engine = DuckDBAnalyticsEngine(resolved_path)
    metadata_store.initialize()
    if created_analytics_engine:
        analytics_engine.initialize()
    return resolved_path, metadata_store, analytics_engine


def _register_configured_governance(
    config: MarivoConfig,
    metadata_store: MetadataStore,
    governance_service: GovernanceService | None,
) -> None:
    if governance_service is None:
        return
    for policy_config in config.governance.policies:
        try:
            existing = metadata_store.query_one(
                "SELECT policy_id FROM policies WHERE name = ?",
                [policy_config.name],
            )
            if not existing:
                governance_service.create_policy(
                    name=policy_config.name,
                    policy_type=policy_config.type,
                    definition=policy_config.definition,
                    scope=policy_config.scope,
                )
                logger.info("Config governance policy '%s' registered", policy_config.name)
        except Exception:
            logger.exception("Failed to register config governance policy '%s'", policy_config.name)
    for quality_rule_config in config.governance.quality_rules:
        try:
            existing = metadata_store.query_one(
                "SELECT rule_id FROM quality_rules WHERE name = ?",
                [quality_rule_config.name],
            )
            if not existing:
                governance_service.create_quality_rule(
                    name=quality_rule_config.name,
                    rule_type=quality_rule_config.type,
                    table_name=quality_rule_config.table,
                    threshold=quality_rule_config.threshold,
                    severity=quality_rule_config.severity,
                )
                logger.info("Config quality rule '%s' registered", quality_rule_config.name)
        except Exception:
            logger.exception(
                "Failed to register config quality rule '%s'", quality_rule_config.name
            )


def _build_services(
    *,
    resolved_path: Path | str,  # Analytics path; str ":memory:" means in-memory DuckDB.
    metadata_store: MetadataStore,
    analytics_engine: AnalyticsEngine,
    config: MarivoConfig,
) -> AppServices:
    setup_logging(level=config.observability.log_level)
    metrics_collector = MetricsCollector() if config.observability.metrics_enabled else None
    governance_service = (
        GovernanceService(metadata_store, analytics_engine, metrics=metrics_collector)
        if config.governance.enabled
        else None
    )
    approval_service = ApprovalService(metadata_store)
    service = SemanticLayerService(
        metadata_store,
        analytics_engine,
        config=config,
        governance=governance_service,
        metrics=metrics_collector,
        approvals=approval_service,
    )
    source_service = SourceService(metadata_store)
    sync_engine = SyncEngine(metadata_store)
    engine_service = EngineService(metadata_store)
    mapping_service = MappingService(metadata_store)
    query_router = QueryRouter(metadata_store, engine_service)
    service.query_router = query_router
    _register_configured_governance(config, metadata_store, governance_service)
    semantic_service = SemanticService(metadata_store)
    catalog_runtime = CatalogRuntimeService(metadata_store)
    job_repository = JobRepository(metadata_store)
    job_service = JobService(
        metadata_store,
        service,
        job_repository=job_repository,
        metrics=metrics_collector,
    )
    return AppServices(
        resolved_path=resolved_path,
        config=config,
        service=service,
        source_service=source_service,
        sync_engine=sync_engine,
        engine_service=engine_service,
        mapping_service=mapping_service,
        query_router=query_router,
        metadata_store=metadata_store,
        analytics_engine=analytics_engine,
        governance_service=governance_service,
        approval_service=approval_service,
        metrics=metrics_collector,
        job_service=job_service,
        job_repository=job_repository,
        semantic_service=semantic_service,
        catalog_runtime=catalog_runtime,
    )


def _attach_state(app: FastAPI, services: AppServices) -> None:
    app.state.services = services
    app.state.config = services.config
    app.state.service = services.service
    app.state.source_service = services.source_service
    app.state.sync_engine = services.sync_engine
    app.state.engine_service = services.engine_service
    app.state.mapping_service = services.mapping_service
    app.state.query_router = services.query_router
    app.state.metadata_store = services.metadata_store
    app.state.analytics_engine = services.analytics_engine
    app.state.governance_service = services.governance_service
    app.state.approval_service = services.approval_service
    app.state.metrics = services.metrics
    app.state.job_service = services.job_service
    app.state.job_repository = services.job_repository
    app.state.semantic_service = services.semantic_service
    app.state.catalog_runtime = services.catalog_runtime


def create_app(
    db_path: str | Path | None = None,
    metadata_store: MetadataStore | None = None,
    analytics_engine: AnalyticsEngine | None = None,
    config_path: str | Path | None = None,
) -> FastAPI:
    resolved_config_path = resolve_config_path(
        Path(config_path) if config_path is not None else None
    )
    explicit_config_path = config_path is not None
    if explicit_config_path and not resolved_config_path.is_file():
        raise RuntimeError(f"Config file not found: {resolved_config_path}")
    config = load_config(resolved_config_path)
    resolved_path, metadata_store, analytics_engine = _resolve_storage(
        db_path,
        metadata_store,
        analytics_engine,
        config,
        resolved_config_path,
        explicit_config_path,
    )
    services = _build_services(
        resolved_path=resolved_path,
        metadata_store=metadata_store,
        analytics_engine=analytics_engine,
        config=config,
    )
    app = FastAPI(title="Marivo Semantic Layer", version="0.1.0")
    _attach_state(app, services)
    app.add_exception_handler(
        RequestValidationError,
        cast(
            "Callable[[Request, Exception], Response | Awaitable[Response]]",
            request_validation_exception_handler,
        ),
    )
    app.add_exception_handler(
        GuidedValidationError,
        cast(
            "Callable[[Request, Exception], Response | Awaitable[Response]]",
            guided_validation_exception_handler,
        ),
    )
    app.add_middleware(TimingMiddleware)
    include_api_routers(app)
    return app
