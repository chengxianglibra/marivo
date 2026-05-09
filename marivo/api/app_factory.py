from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.requests import Request
from starlette.responses import Response

from marivo.api.deps import AppServices
from marivo.api.errors import (
    GuidedValidationError,
    guided_validation_exception_handler,
    request_validation_exception_handler,
)
from marivo.api.middleware import UserIdentityMiddleware
from marivo.api.router import include_api_routers
from marivo.config import MarivoConfig, load_config, resolve_config_path, resolve_metadata_path
from marivo.observability import TimingMiddleware
from marivo.profiles.server import _resolve_storage as _resolve_storage_for_server
from marivo.storage.analytics import AnalyticsEngine
from marivo.storage.metadata import MetadataStore
from marivo.storage.sqlite_metadata import SQLiteMetadataStore
from marivo.transports.mcp.http import mount_mcp_app

logger = logging.getLogger(__name__)


def _resolve_storage(
    db_path: str | Path | None,
    metadata_store: MetadataStore | None,
    analytics_engine: AnalyticsEngine | None,
    config: MarivoConfig,
    config_path: Path,
    config_path_explicit: bool,
) -> tuple[Path | str, MetadataStore, AnalyticsEngine]:
    """Workspace-aware wrapper.

    Resolves workspace-relative metadata paths and enforces the
    config_path_explicit gate up front (the two things the lifted
    helper doesn't know about), then delegates to the server
    profile's _resolve_storage.
    """
    if metadata_store is None:
        metadata_config = config.metadata

        if config_path_explicit and db_path is not None:
            if metadata_config is None:
                raise RuntimeError(
                    "Marivo config must define metadata.engine=sqlite|mysql when "
                    "metadata_store is not provided"
                )
            if metadata_config.engine == "sqlite":
                if metadata_config.path is None:
                    raise RuntimeError(
                        "Marivo config metadata.path is required for sqlite metadata"
                    )
                resolved_metadata_path = resolve_metadata_path(config_path, metadata_config.path)
                metadata_store = SQLiteMetadataStore(resolved_metadata_path)
        elif not config_path_explicit and db_path is not None and str(db_path) == ":memory:":
            metadata_store = SQLiteMetadataStore(Path(":memory:").with_suffix(".meta.sqlite"))
        elif (
            metadata_config is not None
            and metadata_config.engine == "sqlite"
            and metadata_config.path is not None
        ):
            resolved_metadata_path = resolve_metadata_path(config_path, metadata_config.path)
            if db_path is None or str(db_path) == ":memory:":
                metadata_store = SQLiteMetadataStore(resolved_metadata_path)

    return _resolve_storage_for_server(db_path, metadata_store, analytics_engine, config)


def _build_services(
    *,
    resolved_path: Path | str,
    metadata_store: MetadataStore,
    analytics_engine: AnalyticsEngine,
    config: MarivoConfig,
) -> AppServices:
    from marivo.profiles.resolver import resolve_profile
    from marivo.profiles.server import ServerConfig, create_server_runtime

    resolve_profile(entry_point="server_http", service_config=config)

    composition = create_server_runtime(
        ServerConfig(
            marivo_config=config,
            db_path=resolved_path if str(resolved_path) != ":memory:" else None,
            metadata_store=metadata_store,
            analytics_engine=analytics_engine,
        )
    )

    runtime = composition.runtime
    datasource_service = runtime.get_service("datasource")
    query_router = runtime.get_service("query_router")
    semantic_v2_service = runtime.get_service("semantic_v2")

    return AppServices(
        resolved_path=composition.resolved_analytics_path,
        config=config,
        runtime=runtime,
        datasource_service=datasource_service,
        query_router=query_router,
        metadata_store=composition.metadata_store,
        analytics_engine=composition.analytics_engine,
        metrics=composition.metrics,
        semantic_v2_service=semantic_v2_service,
    )


def _attach_state(app: FastAPI, services: AppServices) -> None:
    app.state.services = services
    app.state.config = services.config
    app.state.runtime = services.runtime
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
    mount_mcp_app(app, services.runtime)
    services.runtime.wire_app(app)
    return app
