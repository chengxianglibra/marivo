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
from app.api.middleware import UserIdentityMiddleware
from app.api.router import include_api_routers
from app.config import MarivoConfig, load_config, resolve_config_path, resolve_metadata_path
from app.datasources import DatasourceService
from app.observability import MetricsCollector, TimingMiddleware, setup_logging
from app.routing import QueryRouter
from app.runtime.factory import create_runtime_from_service
from app.semantic_service_v2.service import SemanticModelV2Service
from app.service import SemanticLayerService
from app.storage.analytics import AnalyticsEngine
from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from app.storage.metadata import MetadataStore
from app.storage.mysql_metadata import MySQLMetadataStore
from app.storage.sqlite_metadata import SQLiteMetadataStore

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
        elif metadata_config is not None and metadata_config.engine == "sqlite":
            if metadata_config.path is None:
                raise RuntimeError("Marivo config metadata.path is required for sqlite metadata")
            metadata_path = resolve_metadata_path(config_path, metadata_config.path)
            metadata_store = SQLiteMetadataStore(metadata_path)
        elif metadata_config is not None and metadata_config.engine == "mysql":
            mysql_config = metadata_config.mysql_connection_config()
            metadata_store = MySQLMetadataStore(
                host=str(mysql_config["host"]),
                port=int(mysql_config["port"]),
                database=str(mysql_config["database"]),
                user=str(mysql_config["user"]),
                password=(
                    str(mysql_config["password"])
                    if mysql_config.get("password") is not None
                    else None
                ),
                connect_timeout=int(mysql_config["connect_timeout"]),
                pool_size=int(mysql_config["pool_size"]),
                ssl=mysql_config.get("ssl"),
                dsn=metadata_config.dsn,
            )
        else:
            raise RuntimeError(
                "Marivo config must define metadata.engine=sqlite|mysql when "
                "metadata_store is not provided"
            )
    if analytics_engine is None:
        analytics_engine = DuckDBAnalyticsEngine(resolved_path)
    metadata_store.initialize()
    if created_analytics_engine:
        analytics_engine.initialize()
    return resolved_path, metadata_store, analytics_engine


def _build_services(
    *,
    resolved_path: Path | str,  # Analytics path; str ":memory:" means in-memory DuckDB.
    metadata_store: MetadataStore,
    analytics_engine: AnalyticsEngine,
    config: MarivoConfig,
) -> AppServices:
    setup_logging(level=config.observability.log_level)
    metrics_collector = MetricsCollector() if config.observability.metrics_enabled else None
    service = SemanticLayerService(
        metadata_store,
        analytics_engine,
        config=config,
        metrics=metrics_collector,
    )
    datasource_service = DatasourceService(metadata_store)
    query_router = QueryRouter(metadata_store, datasource_service)
    service.query_router = query_router
    semantic_v2_service = SemanticModelV2Service(
        cast("SQLiteMetadataStore", metadata_store),
        datasource_service=datasource_service,
    )
    runtime = create_runtime_from_service(service, datasource_service, config)
    return AppServices(
        resolved_path=resolved_path,
        config=config,
        runtime=runtime,
        service=service,
        datasource_service=datasource_service,
        query_router=query_router,
        metadata_store=metadata_store,
        analytics_engine=analytics_engine,
        metrics=metrics_collector,
        semantic_v2_service=semantic_v2_service,
    )


def _attach_state(app: FastAPI, services: AppServices) -> None:
    app.state.services = services
    app.state.config = services.config
    app.state.runtime = services.runtime
    app.state.service = services.service
    app.state.datasource_service = services.datasource_service
    app.state.query_router = services.query_router
    app.state.metadata_store = services.metadata_store
    app.state.analytics_engine = services.analytics_engine
    app.state.metrics = services.metrics
    app.state.semantic_v2_service = services.semantic_v2_service


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
    app.add_middleware(UserIdentityMiddleware)
    app.add_middleware(TimingMiddleware)
    include_api_routers(app)
    return app
