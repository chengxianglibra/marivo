from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.config import MarivoConfig
from app.datasources import DatasourceService
from app.observability import MetricsCollector
from app.routing import QueryRouter
from app.runtime.runtime import MarivoRuntime
from app.semantic_service_v2.service import SemanticModelV2Service
from app.storage.analytics import AnalyticsEngine
from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from app.storage.metadata import MetadataStore
from app.storage.mysql_metadata import MySQLMetadataStore
from app.storage.sqlite_metadata import SQLiteMetadataStore


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


@dataclass
class ServerComposition:
    runtime: MarivoRuntime
    metadata_store: MetadataStore
    analytics_engine: AnalyticsEngine
    datasource_service: DatasourceService
    query_router: QueryRouter
    semantic_v2_service: SemanticModelV2Service
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
