from __future__ import annotations

import importlib
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
from app.bindings import BindingService
from app.config import FactumConfig, load_config, resolve_config_path
from app.engines import EngineService
from app.governance import GovernanceService
from app.jobs import JobService
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
from app.ui import register_ui

logger = logging.getLogger(__name__)


def _require_runtime_dependencies(config: FactumConfig) -> None:
    needs_trino = any(source.type == "trino" for source in config.sources) or any(
        engine.type == "trino" for engine in config.engines
    )
    if not needs_trino:
        return
    try:
        importlib.import_module("trino")
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "factum.yaml references a Trino source or engine, but the optional dependency "
            "'trino' is not installed. Install it with: pip install -e .[trino]"
        ) from error


def _resolve_storage(
    db_path: str | Path | None,
    metadata_store: MetadataStore | None,
    analytics_engine: AnalyticsEngine | None,
    config: FactumConfig,
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
                "Factum config must define metadata.engine=sqlite and metadata.path when "
                "metadata_store is not provided"
            )
    if analytics_engine is None:
        analytics_engine = DuckDBAnalyticsEngine(resolved_path)
    metadata_store.initialize()
    if created_analytics_engine:
        analytics_engine.initialize()
    return resolved_path, metadata_store, analytics_engine


def _register_configured_sources(
    config: FactumConfig, source_service: SourceService, sync_engine: SyncEngine
) -> None:
    for source_config in config.sources:
        try:
            source = source_service.ensure_source(
                source_type=source_config.type,
                display_name=source_config.name,
                connection=source_config.connection,
                sync_mode=source_config.sync.mode,
            )
            sync_mode = source.get("sync_mode", source_config.sync.mode)
            if sync_mode == "none":
                logger.info("Config source '%s' registered (sync disabled)", source_config.name)
            elif sync_mode == "by_select":
                selections = source_service.list_sync_selections(source["source_id"])
                if selections:
                    selection_dicts = [
                        {"schema_name": row["schema_name"], "table_name": row["table_name"]}
                        for row in selections
                    ]
                    adapter = source_service.get_adapter(source["source_id"])
                    sync_engine.trigger_sync(
                        source["source_id"], adapter, selections=selection_dicts
                    )
                    logger.info(
                        "Config source '%s' registered and selectively synced", source_config.name
                    )
                else:
                    logger.info(
                        "Config source '%s' registered (by_select, no selections yet)",
                        source_config.name,
                    )
            else:
                logger.warning(
                    "Config source '%s' has unknown sync_mode '%s'; skipping sync",
                    source_config.name,
                    sync_mode,
                )
        except Exception:
            logger.exception("Failed to register/sync config source '%s'", source_config.name)


def _register_configured_engines(config: FactumConfig, engine_service: EngineService) -> None:
    for engine_config in config.engines:
        try:
            engine_service.ensure_engine(
                engine_type=engine_config.type,
                display_name=engine_config.name,
                connection=engine_config.connection,
            )
            logger.info("Config engine '%s' registered", engine_config.name)
        except Exception:
            logger.exception("Failed to register config engine '%s'", engine_config.name)


def _register_configured_bindings(
    config: FactumConfig,
    metadata_store: MetadataStore,
    binding_service: BindingService,
) -> None:
    for binding_config in config.bindings:
        try:
            source_row = metadata_store.query_one(
                "SELECT source_id FROM sources WHERE display_name = ?",
                [binding_config.source],
            )
            engine_row = metadata_store.query_one(
                "SELECT engine_id FROM engines WHERE display_name = ?",
                [binding_config.engine],
            )
            if source_row and engine_row:
                binding_service.ensure_binding(
                    source_row["source_id"],
                    engine_row["engine_id"],
                    binding_config.priority,
                    namespace=binding_config.namespace,
                )
                logger.info(
                    "Config binding '%s' -> '%s' registered",
                    binding_config.source,
                    binding_config.engine,
                )
            else:
                if not source_row:
                    logger.warning("Config binding: source '%s' not found", binding_config.source)
                if not engine_row:
                    logger.warning("Config binding: engine '%s' not found", binding_config.engine)
        except Exception:
            logger.exception(
                "Failed to register config binding '%s' -> '%s'",
                binding_config.source,
                binding_config.engine,
            )


def _register_configured_governance(
    config: FactumConfig,
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
    config: FactumConfig,
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
    _register_configured_sources(config, source_service, sync_engine)
    engine_service = EngineService(metadata_store)
    _register_configured_engines(config, engine_service)
    binding_service = BindingService(metadata_store)
    _register_configured_bindings(config, metadata_store, binding_service)
    query_router = QueryRouter(metadata_store, engine_service)
    service.query_router = query_router
    _register_configured_governance(config, metadata_store, governance_service)
    semantic_service = SemanticService(metadata_store)
    catalog_runtime = CatalogRuntimeService(metadata_store, binding_service)
    job_repository = JobRepository(metadata_store)
    job_service = JobService(
        metadata_store,
        service,
        job_repository=job_repository,
        metrics=metrics_collector,
    )
    admin_enabled = (
        config.ui.admin_enabled if config.ui.admin_enabled is not None else config.ui.enabled
    )
    user_enabled = (
        config.ui.user_enabled if config.ui.user_enabled is not None else config.ui.enabled
    )
    static_dir = Path(__file__).resolve().parent.parent / "static"
    return AppServices(
        resolved_path=resolved_path,
        config=config,
        service=service,
        source_service=source_service,
        sync_engine=sync_engine,
        engine_service=engine_service,
        binding_service=binding_service,
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
        admin_enabled=admin_enabled,
        user_enabled=user_enabled,
        static_dir=static_dir,
    )


def _attach_state(app: FastAPI, services: AppServices) -> None:
    app.state.services = services
    app.state.config = services.config
    app.state.service = services.service
    app.state.source_service = services.source_service
    app.state.sync_engine = services.sync_engine
    app.state.engine_service = services.engine_service
    app.state.binding_service = services.binding_service
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
    explicit_config_path = config_path is not None
    resolved_config_path = resolve_config_path(
        Path(config_path) if config_path is not None else None
    )
    config = load_config(resolved_config_path)
    _require_runtime_dependencies(config)
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
    app = FastAPI(title="Factum Semantic Layer", version="0.2.0")
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
    register_ui(
        app,
        static_dir=services.static_dir,
        admin_enabled=services.admin_enabled,
        user_enabled=services.user_enabled,
    )
    return app
