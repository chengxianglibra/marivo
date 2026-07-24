"""Frame wrappers for analysis."""

from marivo.analysis.frames.association import AssociationResult, AssociationResultMeta
from marivo.analysis.frames.attribution import AttributionFrame, AttributionFrameMeta
from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta
from marivo.analysis.frames.candidate import CandidateSet, CandidateSetMeta
from marivo.analysis.frames.component import ComponentFrame, ComponentFrameMeta
from marivo.analysis.frames.coverage import CoverageFrame, CoverageFrameMeta
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.frames.event import EventFrame, EventFrameMeta, EventInputCoverage
from marivo.analysis.frames.forecast import ForecastFrame, ForecastFrameMeta
from marivo.analysis.frames.hypothesis import HypothesisTestResult, HypothesisTestResultMeta
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.frames.quality import QualityReport, QualityReportMeta

__all__ = [
    "AssociationResult",
    "AssociationResultMeta",
    "AttributionFrame",
    "AttributionFrameMeta",
    "BaseFrame",
    "BaseFrameMeta",
    "CandidateSet",
    "CandidateSetMeta",
    "ComponentFrame",
    "ComponentFrameMeta",
    "CoverageFrame",
    "CoverageFrameMeta",
    "DeltaFrame",
    "DeltaFrameMeta",
    "EventFrame",
    "EventFrameMeta",
    "EventInputCoverage",
    "ForecastFrame",
    "ForecastFrameMeta",
    "HypothesisTestResult",
    "HypothesisTestResultMeta",
    "MetricFrame",
    "MetricFrameMeta",
    "QualityReport",
    "QualityReportMeta",
]
