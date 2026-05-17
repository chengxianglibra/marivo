from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from marivo.adapters.local.file_artifact_store import FileArtifactStore
from marivo.adapters.local.file_audit_log import FileAuditLog
from marivo.adapters.local.file_evidence_store import FileEvidenceStore
from marivo.adapters.local.file_model_store import FileModelStore
from marivo.adapters.local.local_telemetry import LocalTelemetry
from marivo.adapters.local.noop_authz import NoopAuthZ
from marivo.adapters.local.sqlite_cache_store import SqliteCacheStore
from marivo.adapters.local.sqlite_metadata import SQLiteMetadataStore
from marivo.adapters.local.sqlite_session_store import SqliteSessionStore
from marivo.adapters.local.sqlite_step_store import SqliteStepStore
from marivo.adapters.local.toml_runtime_config import TomlRuntimeConfig
from marivo.adapters.server.data_source import RoutingDataSource
from marivo.adapters.server.semantic_service_adapter import SemanticServiceAdapter
from marivo.contracts.errors import ErrorCode, ValidationError
from marivo.core.engine import CoreEngine
from marivo.datasources import DatasourceService
from marivo.local.state_layout import (
    artifacts_dir,
    audit_log_path,
    evidence_dir,
    metadata_db_path,
    models_dir,
    runtime_log_path,
    state_db_path,
    telemetry_log_path,
    toml_config_path,
)
from marivo.observability import setup_logging
from marivo.ports.analytics import AnalyticsEngine
from marivo.profiles.resolver import resolve_profile
from marivo.routing import QueryRouter
from marivo.runtime.ports import RuntimePorts
from marivo.runtime.runtime import MarivoRuntime
from marivo.runtime.semantic.calendar_data_runtime import CalendarDataReader
from marivo.runtime.semantic.calendar_data_service import CalendarDataService

logger = logging.getLogger(__name__)


@dataclass
class LocalConfig:
    workspace_root: Path
    datasource_type: str = "duckdb"
    datasource_config: dict[str, Any] = field(default_factory=dict)
    telemetry_sink: str = "none"


def create_local_runtime(
    config: LocalConfig,
    explicit: str | None = None,
) -> MarivoRuntime:
    """Create a local embedded MarivoRuntime."""
    setup_logging(log_file=runtime_log_path(config.workspace_root))

    resolve_profile(
        entry_point="local_stdio",
        explicit=explicit,
        workspace_config_path=toml_config_path(config.workspace_root),
    )

    default_engine = _create_analytics_engine(config.datasource_type, config.datasource_config)

    metadata_store = SQLiteMetadataStore(metadata_db_path(config.workspace_root))
    metadata_store.initialize()
    from marivo.profiles.evidence import build_evidence_repos

    evidence_repos = build_evidence_repos(metadata_store)
    datasource_service = DatasourceService(metadata_store)
    query_router = QueryRouter(metadata_store, datasource_service)

    routing_data_source = RoutingDataSource(
        registry=datasource_service,
        query_router=query_router,
        default_engine=default_engine,
    )

    ports = RuntimePorts(
        model_store=FileModelStore(models_dir(config.workspace_root)),
        session_store=SqliteSessionStore(state_db_path(config.workspace_root)),
        evidence_store=FileEvidenceStore(evidence_dir(config.workspace_root)),
        data_source=routing_data_source,
        cache_store=SqliteCacheStore(state_db_path(config.workspace_root)),
        authz=NoopAuthZ(),
        audit_log=FileAuditLog(audit_log_path(config.workspace_root)),
        telemetry=LocalTelemetry(
            sink=config.telemetry_sink, log_path=telemetry_log_path(config.workspace_root)
        ),
        runtime_config=TomlRuntimeConfig(toml_config_path(config.workspace_root)),
        artifact_store=FileArtifactStore(
            artifacts_dir(config.workspace_root),
            metadata_store=metadata_store,
            evidence_repos=evidence_repos,
        ),
        step_store=SqliteStepStore(state_db_path(config.workspace_root)),
    )
    core = CoreEngine()
    runtime = MarivoRuntime(ports, core)

    semantic_v2 = SemanticServiceAdapter(metadata_store, datasource_service=datasource_service)
    from marivo.runtime.evidence.semantic_repository import SemanticRuntimeRepository

    semantic_repo = SemanticRuntimeRepository(metadata_store)
    runtime.register_service("datasource", datasource_service)
    runtime.register_service("semantic_v2", semantic_v2)
    runtime.register_service("semantic_repository", semantic_repo)
    runtime.register_service("query_router", query_router)
    runtime.register_service("calendar_data", CalendarDataService(metadata_store))
    runtime.wire_evidence_repos(evidence_repos)
    runtime.wire_metadata(metadata_store)
    runtime.wire_calendar_data_reader(CalendarDataReader(metadata=metadata_store))

    from marivo.time_axis_metadata import TimeAxisMetadataProvider

    runtime.wire_time_axis_metadata_provider(TimeAxisMetadataProvider(metadata_store))

    return runtime


def _create_analytics_engine(dtype: str | None, config: dict[str, Any]) -> AnalyticsEngine | None:
    if dtype is None:
        return None
    if dtype == "duckdb":
        try:
            from marivo.adapters.local.duckdb_analytics import DuckDBAnalyticsEngine
        except ImportError:
            logger.warning(
                "DuckDB is not installed. Local mode will start without a default "
                "analytics engine. Queries that require a default engine will fail. "
                "Install with: pip install marivo[duckdb]"
            )
            return None
        return DuckDBAnalyticsEngine(config.get("path", ":memory:"))
    raise ValidationError(
        code=ErrorCode.VALIDATION,
        message=f"Unknown datasource type: {dtype}",
    )
