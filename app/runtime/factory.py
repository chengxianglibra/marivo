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
    from app.semantic_service_v2.service import SemanticModelV2Service
    from app.service import SemanticLayerService


def create_runtime_from_service(
    svc: SemanticLayerService,
    datasource_svc: DatasourceService,
    config: MarivoConfig,
    semantic_v2_svc: SemanticModelV2Service | None = None,
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
        semantic_repository=svc.semantic_repository,
        semantic_resolver=svc.semantic_resolver,
        metadata=metadata,
        evidence_repos={
            "proposition_repo": svc._proposition_repo,
            "assessment_repo": svc._assessment_repo,
            "finding_repo": svc._finding_repo,
            "gap_repo": svc._gap_repo,
            "inference_record_repo": svc._inference_record_repo,
            "proposal_repo": svc._proposal_repo,
        },
        analytics=svc.analytics,
        calendar_data_reader=getattr(svc, "calendar_data_reader", None),
        time_axis_metadata_provider=getattr(svc, "time_axis_metadata_provider", None),
    )
    core = CoreEngine()
    runtime = MarivoRuntime(ports, core)
    # Set svc property for semantic_ops resolve_engine compatibility (deprecated)
    runtime.svc = svc
    runtime.wire_datasource_svc(datasource_svc)
    if semantic_v2_svc is not None:
        runtime.wire_semantic_v2_svc(semantic_v2_svc)

    # Phase 4b-1: expose runtime on svc so intent runners can access I/O + pure methods
    svc._runtime = runtime

    return runtime
