from __future__ import annotations

from typing import Any

from app.ports.artifact_store import ArtifactStore
from app.ports.audit_log import AuditLog
from app.ports.authz import AuthZ
from app.ports.cache_store import CacheStore
from app.ports.data_source import DataSource
from app.ports.evidence_store import EvidenceStore
from app.ports.model_store import ModelStore
from app.ports.runtime_config import RuntimeConfig
from app.ports.session_store import SessionStore
from app.ports.step_store import StepStore
from app.ports.telemetry import Telemetry


class RuntimePorts:
    """Typed container for all port implementations.

    Required ports are passed positionally. Optional server-mode services
    are set as keyword arguments — they are None in local mode and wired
    in the server factory.
    """

    # Optional server-mode ports (None in local mode)
    semantic_repository: Any | None
    semantic_resolver: Any | None
    metadata: Any | None
    evidence_repos: Any | None
    analytics: Any | None
    calendar_data_reader: Any | None
    time_axis_metadata_provider: Any | None

    def __init__(
        self,
        model_store: ModelStore,
        session_store: SessionStore,
        evidence_store: EvidenceStore,
        data_source: DataSource,
        cache_store: CacheStore,
        authz: AuthZ,
        audit_log: AuditLog,
        telemetry: Telemetry,
        runtime_config: RuntimeConfig,
        artifact_store: ArtifactStore,
        step_store: StepStore,
        *,
        semantic_repository: Any | None = None,
        semantic_resolver: Any | None = None,
        metadata: Any | None = None,
        evidence_repos: Any | None = None,
        analytics: Any | None = None,
        calendar_data_reader: Any | None = None,
        time_axis_metadata_provider: Any | None = None,
    ) -> None:
        self.model_store = model_store
        self.session_store = session_store
        self.evidence_store = evidence_store
        self.data_source = data_source
        self.cache_store = cache_store
        self.authz = authz
        self.audit_log = audit_log
        self.telemetry = telemetry
        self.runtime_config = runtime_config
        self.artifact_store: ArtifactStore = artifact_store
        self.step_store: StepStore = step_store
        self.semantic_repository = semantic_repository
        self.semantic_resolver = semantic_resolver
        self.metadata = metadata
        self.evidence_repos = evidence_repos
        self.analytics = analytics
        self.calendar_data_reader = calendar_data_reader
        self.time_axis_metadata_provider = time_axis_metadata_provider
