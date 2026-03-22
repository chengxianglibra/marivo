"""Extractor package — uses lazy imports to avoid circular dependency with contract.py."""

from __future__ import annotations

from typing import Any

__all__ = [
    "AggregateRowExtractor",
    "AnomalyExtractor",
    "ComparisonRowExtractor",
    "ContributionShiftExtractor",
    "FunnelExtractor",
    "ObservationExtractor",
]


def __getattr__(name: str) -> Any:
    if name == "ObservationExtractor":
        from app.evidence_engine.extractors.base import ObservationExtractor
        return ObservationExtractor
    if name == "ComparisonRowExtractor":
        from app.evidence_engine.extractors.comparison import ComparisonRowExtractor
        return ComparisonRowExtractor
    if name == "AggregateRowExtractor":
        from app.evidence_engine.extractors.aggregate import AggregateRowExtractor
        return AggregateRowExtractor
    if name == "AnomalyExtractor":
        from app.evidence_engine.extractors.anomaly import AnomalyExtractor
        return AnomalyExtractor
    if name == "FunnelExtractor":
        from app.evidence_engine.extractors.funnel import FunnelExtractor
        return FunnelExtractor
    if name == "ContributionShiftExtractor":
        from app.evidence_engine.extractors.contribution_shift import ContributionShiftExtractor
        return ContributionShiftExtractor
    raise AttributeError(name)
