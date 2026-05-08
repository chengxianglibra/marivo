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
from app.observability import TimingMiddleware
from app.profiles.server import _resolve_storage as _resolve_storage_for_server
from app.service import SemanticLayerService
from app.storage.analytics import AnalyticsEngine
from app.storage.metadata import MetadataStore
from app.storage.sqlite_metadata import SQLiteMetadataStore
from app.transports.mcp.http import mount_mcp_app

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
            # When an explicit config path is provided alongside a db_path,
            # the original code skips the db_path carveout and requires
            # metadata to be defined in config. Pre-create the store so the
            # lifted helper won't fall into the carveout.
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
            # mysql and other engines are handled by the lifted helper.
        elif not config_path_explicit and db_path is not None and str(db_path) == ":memory:":
            # The original code's carveout condition
            #   db_path is not None and not config_path_explicit
            # also matched ":memory:" db_path, creating a sidecar
            # .meta.sqlite. The lifted helper excludes :memory: from its
            # carveout, so pre-create the store here.
            metadata_store = SQLiteMetadataStore(Path(":memory:").with_suffix(".meta.sqlite"))
        elif (
            metadata_config is not None
            and metadata_config.engine == "sqlite"
            and metadata_config.path is not None
        ):
            # config_path_explicit=False with sqlite config: pre-resolve
            # workspace-relative path so the lifted helper's sqlite branch
            # (which uses metadata_config.path directly) gets the right path.
            resolved_metadata_path = resolve_metadata_path(config_path, metadata_config.path)
            # Only pre-create when the lifted helper would NOT use the
            # db_path carveout (i.e., no real db_path).
            if db_path is None or str(db_path) == ":memory:":
                metadata_store = SQLiteMetadataStore(resolved_metadata_path)

    return _resolve_storage_for_server(db_path, metadata_store, analytics_engine, config)


def _build_services(
    *,
    resolved_path: Path | str,  # Analytics path; str ":memory:" means in-memory DuckDB.
    metadata_store: MetadataStore,
    analytics_engine: AnalyticsEngine,
    config: MarivoConfig,
) -> AppServices:
    from app.profiles.resolver import resolve_profile
    from app.profiles.server import ServerConfig, create_server_runtime

    # Defense-in-depth guard: enterprise HTTP entry must resolve to "server"
    # profile. Misconfiguration (MARIVO_PROFILE=local, profile: local in
    # marivo.yaml) fails fast here with a typed VALIDATION error instead of
    # silently constructing an enterprise runtime against local-only stubs.
    resolve_profile(entry_point="server_http", service_config=config)

    composition = create_server_runtime(
        ServerConfig(
            marivo_config=config,
            db_path=resolved_path if str(resolved_path) != ":memory:" else None,
            metadata_store=metadata_store,
            analytics_engine=analytics_engine,
        )
    )
    # Backward-compat shim: SemanticLayerService is still referenced by
    # sessions.py and health.py via AppServices.service.  Construct it from
    # the same components so the legacy path works until 6.1 drops the field.
    service = SemanticLayerService(
        composition.metadata_store,
        composition.analytics_engine,
        query_router=composition.query_router,
        config=config,
        metrics=composition.metrics,
    )
    # Wire the runtime back onto the service so legacy intent runners that
    # traverse service._runtime continue to work.
    service._runtime = composition.runtime
    composition.runtime.wire_datasource_svc(composition.datasource_service)
    composition.runtime.wire_semantic_v2_svc(composition.semantic_v2_service)

    return AppServices(
        resolved_path=composition.resolved_analytics_path,
        config=config,
        runtime=composition.runtime,
        service=service,
        datasource_service=composition.datasource_service,
        query_router=composition.query_router,
        metadata_store=composition.metadata_store,
        analytics_engine=composition.analytics_engine,
        metrics=composition.metrics,
        semantic_v2_service=composition.semantic_v2_service,
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
    mount_mcp_app(app, services.runtime)
    services.runtime.wire_app(app)
    return app
