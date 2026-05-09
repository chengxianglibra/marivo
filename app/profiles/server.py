from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.config import MarivoConfig
from app.datasources import DatasourceService
from app.observability import MetricsCollector, setup_logging
from app.routing import QueryRouter
from app.runtime.ports import RuntimePorts
from app.runtime.runtime import MarivoRuntime
from app.semantic_service_v2.service import SemanticModelV2Service
from app.storage.analytics import AnalyticsEngine
from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from app.storage.metadata import MetadataStore
from app.storage.mysql_metadata import MySQLMetadataStore
from app.storage.sqlite_metadata import SQLiteMetadataStore
from app.storage.step_metadata_repository import StepMetadataRepository


@dataclass
class ServerConfig:
    """Inputs needed to construct a server-profile runtime.

    Carries an already-loaded MarivoConfig plus the few inputs that
    are not in it (analytics path, optional infrastructure overrides
    for tests). Phase 9, when adapters go native, revisits this and
    likely adds direct fields for db_url, S3 config, etc.
    """

    marivo_config: MarivoConfig
    db_path: Path | str | None = None
    metadata_store: MetadataStore | None = None
    analytics_engine: AnalyticsEngine | None = None
    file_store_dir: Path | str | None = None  # evidence file storage dir
    audit_dir: Path | str | None = None  # audit log dir


@dataclass
class ServerComposition:
    runtime: MarivoRuntime
    metadata_store: MetadataStore
    analytics_engine: AnalyticsEngine
    metrics: MetricsCollector | None
    resolved_analytics_path: Path | str


def _resolve_storage(
    db_path: Path | str | None,
    metadata_store: MetadataStore | None,
    analytics_engine: AnalyticsEngine | None,
    config: MarivoConfig,
) -> tuple[Path | str, MetadataStore, AnalyticsEngine]:
    if db_path is not None:
        resolved_path: Path | str = Path(db_path)
    else:
        resolved_path = ":memory:"

    created_analytics_engine = analytics_engine is None

    if metadata_store is None:
        metadata_config = config.metadata
        if db_path is not None and str(db_path) != ":memory:":
            metadata_store = SQLiteMetadataStore(Path(resolved_path).with_suffix(".meta.sqlite"))
        elif metadata_config is not None and metadata_config.engine == "sqlite":
            if metadata_config.path is None:
                raise RuntimeError("Marivo config metadata.path is required for sqlite metadata")
            metadata_store = SQLiteMetadataStore(Path(metadata_config.path))
        elif metadata_config is not None and metadata_config.engine == "mysql":
            mysql_config = metadata_config.mysql_connection_config()
            metadata_store = MySQLMetadataStore(
                host=str(mysql_config["host"]),
                port=int(mysql_config["port"]),
                database=str(mysql_config["database"]),
                user=str(mysql_config["user"]),
                password=(
                    str(mysql_config["password"])
                    if mysql_config.get("password") is not None
                    else None
                ),
                connect_timeout=int(mysql_config["connect_timeout"]),
                pool_size=int(mysql_config["pool_size"]),
                ssl=mysql_config.get("ssl"),
                dsn=metadata_config.dsn,
            )
        else:
            raise RuntimeError(
                "Marivo config must define metadata.engine=sqlite|mysql when "
                "metadata_store is not provided"
            )

    if analytics_engine is None:
        analytics_engine = DuckDBAnalyticsEngine(resolved_path)

    metadata_store.initialize()
    if created_analytics_engine:
        analytics_engine.initialize()
    return resolved_path, metadata_store, analytics_engine


def create_server_runtime(config: ServerConfig) -> ServerComposition:
    from app.core.engine import CoreEngine

    setup_logging(level=config.marivo_config.observability.log_level)
    metrics = MetricsCollector() if config.marivo_config.observability.metrics_enabled else None

    resolved_path, metadata_store, analytics_engine = _resolve_storage(
        config.db_path,
        config.metadata_store,
        config.analytics_engine,
        config.marivo_config,
    )

    # §5.4: All adapters share this single MetadataStore instance,
    # which manages its own connection pool. This prevents N independent
    # connection pools against the same database.
    datasource_service = DatasourceService(metadata_store)
    query_router = QueryRouter(metadata_store, datasource_service)
    # SemanticModelV2Service is typed for SQLiteMetadataStore, but the server
    # profile always resolves to a SQLite-compatible store (either provided
    # directly or created by _resolve_storage).
    semantic_v2 = SemanticModelV2Service(
        metadata_store,  # type: ignore[arg-type]
        datasource_service=datasource_service,
    )

    ports = _build_server_ports(
        metadata_store=metadata_store,
        analytics_engine=analytics_engine,
        datasource_service=datasource_service,
        query_router=query_router,
        semantic_v2_service=semantic_v2,
        marivo_config=config.marivo_config,
    )
    runtime = MarivoRuntime(ports, CoreEngine())
    runtime.register_service("datasource", datasource_service)
    runtime.register_service("semantic_v2", semantic_v2)
    runtime.register_service("query_router", query_router)
    runtime.wire_evidence_repos(_build_evidence_repos(metadata_store))
    runtime.wire_metadata(metadata_store)
    runtime.wire_analytics(analytics_engine)

    return ServerComposition(
        runtime=runtime,
        metadata_store=metadata_store,
        analytics_engine=analytics_engine,
        metrics=metrics,
        resolved_analytics_path=resolved_path,
    )


def _build_server_ports(
    *,
    metadata_store: MetadataStore,
    analytics_engine: AnalyticsEngine,
    datasource_service: DatasourceService,
    query_router: QueryRouter,
    semantic_v2_service: SemanticModelV2Service,
    marivo_config: MarivoConfig,
) -> RuntimePorts:
    from app.adapters.server.artifact_store import (
        MetadataArtifactStoreAdapter,
        MetadataStepStoreAdapter,
    )
    from app.adapters.server.audit_log import FileAuditLogAdapter
    from app.adapters.server.authz import NoopAuthZAdapter
    from app.adapters.server.cache_store import InMemoryCacheStore
    from app.adapters.server.data_source import RoutingDataSource
    from app.adapters.server.evidence_store import MetadataEvidenceStoreAdapter
    from app.adapters.server.model_store import SqlModelStoreAdapter
    from app.adapters.server.runtime_config import TomlRuntimeConfigAdapter
    from app.adapters.server.session_store import SqlSessionStore
    from app.adapters.server.telemetry import LocalTelemetryAdapter
    from app.storage.evidence_repositories import (
        ActionProposalRepository,
        AssessmentRepository,
        EvidenceGapRepository,
        FindingRepository,
        InferenceRecordRepository,
        PropositionRepository,
    )

    finding_repo = FindingRepository(metadata_store)
    proposition_repo = PropositionRepository(metadata_store)
    assessment_repo = AssessmentRepository(metadata_store)
    gap_repo = EvidenceGapRepository(metadata_store)
    inference_repo = InferenceRecordRepository(metadata_store)
    proposal_repo = ActionProposalRepository(metadata_store)

    step_metadata_repo = StepMetadataRepository(metadata_store)

    return RuntimePorts(
        model_store=SqlModelStoreAdapter(semantic_v2_service, metadata_store),
        session_store=SqlSessionStore(metadata_store),
        evidence_store=MetadataEvidenceStoreAdapter(
            finding_repo=finding_repo,
            proposition_repo=proposition_repo,
            assessment_repo=assessment_repo,
            gap_repo=gap_repo,
            inference_repo=inference_repo,
            action_proposal_repo=proposal_repo,
        ),
        data_source=RoutingDataSource(
            default_engine=analytics_engine,
            registry=datasource_service,
            query_router=query_router,
        ),
        cache_store=InMemoryCacheStore(),
        authz=NoopAuthZAdapter(),
        audit_log=FileAuditLogAdapter(),
        telemetry=LocalTelemetryAdapter(),
        runtime_config=TomlRuntimeConfigAdapter(marivo_config),
        artifact_store=MetadataArtifactStoreAdapter(
            metadata_store,
            step_metadata_repo=step_metadata_repo,
        ),
        step_store=MetadataStepStoreAdapter(
            metadata_store,
            step_metadata_repo=step_metadata_repo,
        ),
    )


def _build_evidence_repos(metadata_store: MetadataStore) -> dict[str, object]:
    from app.storage.evidence_repositories import (
        ActionProposalRepository,
        AssessmentRepository,
        EvidenceGapRepository,
        FindingRepository,
        InferenceRecordRepository,
        PropositionRepository,
    )

    return {
        "proposition_repo": PropositionRepository(metadata_store),
        "assessment_repo": AssessmentRepository(metadata_store),
        "finding_repo": FindingRepository(metadata_store),
        "gap_repo": EvidenceGapRepository(metadata_store),
        "inference_record_repo": InferenceRecordRepository(metadata_store),
        "proposal_repo": ActionProposalRepository(metadata_store),
    }
