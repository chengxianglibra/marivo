from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from marivo.adapters.local.duckdb_data_source import DuckDBDataSource
from marivo.adapters.local.file_artifact_store import FileArtifactStore
from marivo.adapters.local.file_audit_log import FileAuditLog
from marivo.adapters.local.file_evidence_store import FileEvidenceStore
from marivo.adapters.local.file_model_store import FileModelStore
from marivo.adapters.local.local_telemetry import LocalTelemetry
from marivo.adapters.local.noop_authz import NoopAuthZ
from marivo.adapters.local.sqlite_cache_store import SqliteCacheStore
from marivo.adapters.local.sqlite_session_store import SqliteSessionStore
from marivo.adapters.local.sqlite_step_store import SqliteStepStore
from marivo.adapters.local.toml_runtime_config import TomlRuntimeConfig
from marivo.contracts.errors import ErrorCode, ValidationError
from marivo.core.engine import CoreEngine
from marivo.local.state_layout import (
    artifacts_dir,
    audit_log_path,
    evidence_dir,
    models_dir,
    state_db_path,
    telemetry_log_path,
    toml_config_path,
)
from marivo.profiles.resolver import resolve_profile
from marivo.runtime.ports import RuntimePorts
from marivo.runtime.runtime import MarivoRuntime

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
    resolve_profile(
        entry_point="local_stdio",
        explicit=explicit,
        workspace_config_path=toml_config_path(config.workspace_root),
    )

    data_source = _create_data_source(config.datasource_type, config.datasource_config)

    ports = RuntimePorts(
        model_store=FileModelStore(models_dir(config.workspace_root)),
        session_store=SqliteSessionStore(state_db_path(config.workspace_root)),
        evidence_store=FileEvidenceStore(evidence_dir(config.workspace_root)),
        data_source=data_source,
        cache_store=SqliteCacheStore(state_db_path(config.workspace_root)),
        authz=NoopAuthZ(),
        audit_log=FileAuditLog(audit_log_path(config.workspace_root)),
        telemetry=LocalTelemetry(
            sink=config.telemetry_sink, log_path=telemetry_log_path(config.workspace_root)
        ),
        runtime_config=TomlRuntimeConfig(toml_config_path(config.workspace_root)),
        artifact_store=FileArtifactStore(artifacts_dir(config.workspace_root)),
        step_store=SqliteStepStore(state_db_path(config.workspace_root)),
    )
    core = CoreEngine()
    return MarivoRuntime(ports, core)


def _create_data_source(dtype: str, config: dict[str, Any]) -> DuckDBDataSource:
    if dtype == "duckdb":
        return DuckDBDataSource(path=config.get("path"))
    raise ValidationError(
        code=ErrorCode.VALIDATION,
        message=f"Unknown datasource type: {dtype}",
    )
