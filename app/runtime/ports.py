from __future__ import annotations

from app.ports.artifact_store import ArtifactStore, StepStore
from app.ports.audit_log import AuditLog
from app.ports.authz import AuthZ
from app.ports.cache_store import CacheStore
from app.ports.data_source import DataSource
from app.ports.evidence_store import EvidenceStore
from app.ports.model_store import ModelStore
from app.ports.runtime_config import RuntimeConfig
from app.ports.session_store import SessionStore
from app.ports.telemetry import Telemetry


class RuntimePorts:
    """Typed container for all port implementations."""

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
        *,
        artifact_store: ArtifactStore | None = None,
        step_store: StepStore | None = None,
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
        self.artifact_store: ArtifactStore | None = artifact_store
        self.step_store: StepStore | None = step_store
