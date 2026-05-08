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
from app.storage.metadata import MetadataStore


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
