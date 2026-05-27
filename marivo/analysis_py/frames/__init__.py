"""Frame wrappers for analysis_py."""

from marivo.analysis_py.frames.association import AssociationResult, AssociationResultMeta
from marivo.analysis_py.frames.attribution import AttributionFrame, AttributionFrameMeta
from marivo.analysis_py.frames.base import BaseFrame, BaseFrameMeta, FramePreview
from marivo.analysis_py.frames.candidate import CandidateSet, CandidateSetMeta
from marivo.analysis_py.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis_py.frames.exploration import ExplorationResult, ExplorationResultMeta
from marivo.analysis_py.frames.forecast import ForecastFrame, ForecastFrameMeta
from marivo.analysis_py.frames.hypothesis import HypothesisTestResult, HypothesisTestResultMeta
from marivo.analysis_py.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis_py.frames.quality import QualityReport, QualityReportMeta

__all__ = [
    "AssociationResult",
    "AssociationResultMeta",
    "AttributionFrame",
    "AttributionFrameMeta",
    "BaseFrame",
    "BaseFrameMeta",
    "CandidateSet",
    "CandidateSetMeta",
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
]
