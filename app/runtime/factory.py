from __future__ import annotations

from typing import TYPE_CHECKING

from app.adapters.server.artifact_store import (
    MetadataArtifactStoreAdapter,
    MetadataStepStoreAdapter,
)
from app.adapters.server.wrappers import (
    DataSourceAdapter,
    FileAuditLogAdapter,
    LocalTelemetryAdapter,
    MetadataCacheStoreAdapter,
    MetadataEvidenceStoreAdapter,
    NoopAuthZAdapter,
    SqlModelStoreAdapter,
    SqlSessionStoreAdapter,
    TomlRuntimeConfigAdapter,
)
from app.core.engine import CoreEngine
from app.runtime.ports import RuntimePorts
from app.runtime.runtime import MarivoRuntime

if TYPE_CHECKING:
    from app.config import MarivoConfig
    from app.datasources import DatasourceService
    from app.service import SemanticLayerService


def create_runtime_from_service(
    svc: SemanticLayerService,
    datasource_svc: DatasourceService,
    config: MarivoConfig,
) -> MarivoRuntime:
    """Phase 3a factory: wraps existing infrastructure into Runtime."""

    metadata = svc.metadata
    query_router = svc.query_router
    if query_router is None:
        raise RuntimeError(
            "Cannot create Runtime: SemanticLayerService.query_router is not set. "
            "Ensure query_router is assigned before calling create_runtime_from_service."
        )

    ports = RuntimePorts(
        model_store=SqlModelStoreAdapter(svc.semantic_repository, metadata),
        session_store=SqlSessionStoreAdapter(svc.session_manager, metadata),
        evidence_store=MetadataEvidenceStoreAdapter(
            finding_repo=svc._finding_repo,
            proposition_repo=svc._proposition_repo,
            assessment_repo=svc._assessment_repo,
            gap_repo=svc._gap_repo,
            inference_repo=svc._inference_record_repo,
            action_proposal_repo=svc._proposal_repo,
        ),
        data_source=DataSourceAdapter(svc.analytics, query_router),
        cache_store=MetadataCacheStoreAdapter(metadata),
        authz=NoopAuthZAdapter(),
        audit_log=FileAuditLogAdapter(),
        telemetry=LocalTelemetryAdapter(),
        runtime_config=TomlRuntimeConfigAdapter(config),
        artifact_store=MetadataArtifactStoreAdapter(
            metadata,
            step_metadata_repo=svc._step_metadata_repo,
            svc=svc,
        ),
        step_store=MetadataStepStoreAdapter(
            metadata,
            step_metadata_repo=svc._step_metadata_repo,
        ),
    )
    core = CoreEngine()
    runtime = MarivoRuntime(ports, core)
    runtime.wire_svc(svc)

    # Phase 4b-1: expose runtime on svc so intent runners can access I/O + pure methods
    svc._runtime = runtime

    return runtime
