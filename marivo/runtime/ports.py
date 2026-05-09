from __future__ import annotations

from marivo.ports.artifact_store import ArtifactStore
from marivo.ports.audit_log import AuditLog
from marivo.ports.authz import AuthZ
from marivo.ports.cache_store import CacheStore
from marivo.ports.data_source import DataSource
from marivo.ports.evidence_store import EvidenceStore
from marivo.ports.model_store import ModelStore
from marivo.ports.runtime_config import RuntimeConfig
from marivo.ports.session_store import SessionStore
from marivo.ports.step_store import StepStore
from marivo.ports.telemetry import Telemetry


class RuntimePorts:
    """Typed container for domain port implementations.

    Only true domain ports (the 11 required ports) belong here.
    Server-mode infrastructure services (metadata, analytics,
    evidence_repos, semantic_repository, etc.) are wired directly
    onto MarivoRuntime via wire methods and properties, keeping
    the port layer clean and infrastructure-agnostic.
    """

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
