from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.evidence_engine.contract import ExtractorContract
from app.evidence_engine.extractors.base import ObservationExtractor


class ExtractorRegistry:
    """Registry for ExtractorContract instances."""

    def __init__(self) -> None:
        self._extractors: dict[str, ExtractorContract] = {}

    def register(self, extractor: ExtractorContract) -> None:
        self._extractors[extractor.name] = extractor

    def get(self, name: str) -> ExtractorContract:
        if name not in self._extractors:
            raise KeyError(f"Unknown extractor: {name!r}")
        return self._extractors[name]

    def find_for_artifact(self, artifact_type: str) -> list[ExtractorContract]:
        return [e for e in self._extractors.values() if e.artifact_type == artifact_type]

    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "name": e.name,
                "artifact_type": e.artifact_type,
                "observation_types": e.observation_types,
                "preconditions": e.preconditions,
            }
            for e in self._extractors.values()
        ]

    def as_mapping(self) -> dict[str, ObservationExtractor]:
        """Backward-compatible shim returning a plain dict of ObservationExtractor."""
        return dict(self._extractors)


# Default registry — populated at module bottom to avoid circular imports.
_default_registry = ExtractorRegistry()


def _bootstrap() -> None:
    from app.evidence_engine.extractors.aggregate import AggregateRowExtractor
    from app.evidence_engine.extractors.anomaly import AnomalyExtractor
    from app.evidence_engine.extractors.contribution_shift import ContributionShiftExtractor
    from app.evidence_engine.extractors.correlation import CorrelationObservationExtractor
    from app.evidence_engine.extractors.funnel import FunnelExtractor
    from app.evidence_engine.extractors.metric_observation import MetricObservationExtractor

    for extractor in [
        MetricObservationExtractor(),
        AggregateRowExtractor(),
        FunnelExtractor(),
        AnomalyExtractor(),
        ContributionShiftExtractor(),
        CorrelationObservationExtractor(),
    ]:
        _default_registry.register(extractor)


_bootstrap()
