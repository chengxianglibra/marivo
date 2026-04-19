"""Compatibility facade for governance runtime."""

from __future__ import annotations

from app.governance_engine import GovernanceRepository, GovernanceRuntime
from app.observability import MetricsCollector
from app.storage.analytics import AnalyticsEngine
from app.storage.metadata import MetadataStore


class GovernanceService(GovernanceRuntime):
    def __init__(
        self,
        metadata: MetadataStore,
        analytics: AnalyticsEngine,
        metrics: MetricsCollector | None = None,
    ) -> None:
        self.metadata = metadata
        repository = GovernanceRepository(metadata)
        self.repository = repository
        super().__init__(repository, analytics, metrics=metrics)
