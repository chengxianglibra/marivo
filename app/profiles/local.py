from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.adapters.local.duckdb_data_source import DuckDBDataSource
from app.adapters.local.file_artifact_store import FileArtifactStore
from app.adapters.local.file_audit_log import FileAuditLog
from app.adapters.local.file_evidence_store import FileEvidenceStore
from app.adapters.local.file_model_store import FileModelStore
from app.adapters.local.local_telemetry import LocalTelemetry
from app.adapters.local.noop_authz import NoopAuthZ
from app.adapters.local.sqlite_cache_store import SqliteCacheStore
from app.adapters.local.sqlite_session_store import SqliteSessionStore
from app.adapters.local.sqlite_step_store import SqliteStepStore
from app.adapters.local.toml_runtime_config import TomlRuntimeConfig
from app.contracts.errors import ErrorCode, ValidationError
from app.core.engine import CoreEngine
from app.profiles.resolver import resolve_profile
from app.runtime.ports import RuntimePorts
from app.runtime.runtime import MarivoRuntime

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
    marivo_dir = config.workspace_root / ".marivo"
    resolve_profile(
        entry_point="local_stdio",
        explicit=explicit,
        workspace_config_path=marivo_dir / "marivo.toml",
    )

    data_source = _create_data_source(config.datasource_type, config.datasource_config)

    ports = RuntimePorts(
        model_store=FileModelStore(marivo_dir / "models"),
        session_store=SqliteSessionStore(marivo_dir / "state.db"),
        evidence_store=FileEvidenceStore(marivo_dir / "evidence"),
        data_source=data_source,
        cache_store=SqliteCacheStore(marivo_dir / "state.db"),
        authz=NoopAuthZ(),
        audit_log=FileAuditLog(marivo_dir / "audit.jsonl"),
        telemetry=LocalTelemetry(
            sink=config.telemetry_sink, log_path=marivo_dir / "telemetry.jsonl"
        ),
        runtime_config=TomlRuntimeConfig(marivo_dir / "marivo.toml"),
        artifact_store=FileArtifactStore(marivo_dir / "artifacts"),
        step_store=SqliteStepStore(marivo_dir / "state.db"),
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
