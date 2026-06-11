"""Frame wrappers for analysis."""

from marivo.analysis.frames.association import AssociationResult, AssociationResultMeta
from marivo.analysis.frames.attribution import AttributionFrame, AttributionFrameMeta
from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta, FramePreview
from marivo.analysis.frames.candidate import CandidateSet, CandidateSetMeta
from marivo.analysis.frames.component import ComponentFrame, ComponentFrameMeta
from marivo.analysis.frames.coverage import CoverageFrame, CoverageFrameMeta
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.frames.exploration import ExplorationResult, ExplorationResultMeta
from marivo.analysis.frames.forecast import ForecastFrame, ForecastFrameMeta
from marivo.analysis.frames.hypothesis import HypothesisTestResult, HypothesisTestResultMeta
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.frames.quality import (
    CheckResult,
    QualityReport,
    QualityReportMeta,
    QualityReportSummary,
)

__all__ = [
    "AssociationResult",
    "AssociationResultMeta",
    "AttributionFrame",
    "AttributionFrameMeta",
    "BaseFrame",
    "BaseFrameMeta",
    "CandidateSet",
    "CandidateSetMeta",
    "CheckResult",
    "ComponentFrame",
    "ComponentFrameMeta",
    "CoverageFrame",
    "CoverageFrameMeta",
    "DeltaFrame",
    "DeltaFrameMeta",
    "ExplorationResult",
    "ExplorationResultMeta",
    "ForecastFrame",
    "ForecastFrameMeta",
    "FramePreview",
    "HypothesisTestResult",
    "HypothesisTestResultMeta",
    "MetricFrame",
    "MetricFrameMeta",
    "QualityReport",
    "QualityReportMeta",
    "QualityReportSummary",
]
