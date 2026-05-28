"""Marivo Python-native analysis runtime (analysis_py)."""

from typing import Any

from marivo.analysis_py import errors as errors
from marivo.analysis_py import session
from marivo.analysis_py.calendar.model import CalendarPolicy
from marivo.analysis_py.errors import DiscoverInsufficientDataError, PromotionFailedError
from marivo.analysis_py.evidence import (
    Assessment,
    AssociationSummary,
    AttributedDriver,
    BlockedFollowup,
    ChangeFact,
    EvidenceTrace,
    Finding,
    ForecastSummary,
    OpenAnomaly,
    OpenQuestion,
    Proposition,
    QualitySummary,
    SessionKnowledge,
    Subject,
    TestedHypothesis,
    TimeWindow,
    TriggeredByFollowup,
)
from marivo.analysis_py.followups import (
    BlockingIssue,
    ConfidenceScope,
    FollowupAction,
)
from marivo.analysis_py.frames.association import AssociationResult, AssociationResultMeta
from marivo.analysis_py.frames.attribution import AttributionFrame, AttributionFrameMeta
from marivo.analysis_py.frames.base import BaseFrame, BaseFrameMeta, FramePreview
from marivo.analysis_py.frames.candidate import (
    CandidateObjective,
    CandidateSet,
    CandidateSetMeta,
    CandidateShape,
)
from marivo.analysis_py.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis_py.frames.exploration import ExplorationResult, ExplorationResultMeta
from marivo.analysis_py.frames.forecast import ForecastFrame, ForecastFrameMeta
from marivo.analysis_py.frames.hypothesis import HypothesisTestResult, HypothesisTestResultMeta
from marivo.analysis_py.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis_py.frames.quality import QualityReport, QualityReportMeta
from marivo.analysis_py.help import help, help_text
from marivo.analysis_py.intents._types import (
    DiscoverSensitivity,
    SlicePredicate,
    SlicePredicateOp,
    SliceScalar,
    SliceValue,
)
from marivo.analysis_py.policies import (
    AlignmentKind,
    AlignmentPolicy,
    LagPolicy,
    PromotionPolicy,
    PromotionSemanticAnchors,
    SamplingPolicy,
)
from marivo.analysis_py.refs import ArtifactRef, CalendarRef, DimensionRef, MetricRef
from marivo.analysis_py.session._load import load_frame
from marivo.analysis_py.windows.spec import AbsoluteWindow, RelativeWindow, WindowInput


def __getattr__(name: str) -> Any:
    if name == "datasources":
        from importlib import import_module

        return import_module("marivo.analysis_py.datasources")
    raise AttributeError(name)


__all__ = [
    "AbsoluteWindow",
    "AlignmentKind",
    "AlignmentPolicy",
    "ArtifactRef",
    "Assessment",
    "AssociationResult",
    "AssociationResultMeta",
    "AssociationSummary",
    "AttributedDriver",
    "AttributionFrame",
    "AttributionFrameMeta",
    "BaseFrame",
    "BaseFrameMeta",
    "BlockedFollowup",
    "BlockingIssue",
    "CalendarPolicy",
    "CalendarRef",
    "CandidateObjective",
    "CandidateSet",
    "CandidateSetMeta",
    "CandidateShape",
    "ChangeFact",
    "ConfidenceScope",
    "DeltaFrame",
    "DeltaFrameMeta",
    "DimensionRef",
    "DiscoverInsufficientDataError",
    "DiscoverSensitivity",
    "EvidenceTrace",
    "ExplorationResult",
    "ExplorationResultMeta",
    "Finding",
    "FollowupAction",
    "ForecastFrame",
    "ForecastFrameMeta",
    "ForecastSummary",
    "FramePreview",
    "HypothesisTestResult",
    "HypothesisTestResultMeta",
    "LagPolicy",
    "MetricFrame",
    "MetricFrameMeta",
    "MetricRef",
    "OpenAnomaly",
    "OpenQuestion",
    "PromotionFailedError",
    "PromotionPolicy",
    "PromotionSemanticAnchors",
    "Proposition",
    "QualityReport",
    "QualityReportMeta",
    "QualitySummary",
    "RelativeWindow",
    "SamplingPolicy",
    "SessionKnowledge",
    "SlicePredicate",
    "SlicePredicateOp",
    "SliceScalar",
    "SliceValue",
    "Subject",
    "TestedHypothesis",
    "TimeWindow",
    "TriggeredByFollowup",
    "WindowInput",
    "datasources",
    "errors",
    "help",
    "help_text",
    "load_frame",
    "session",
]
