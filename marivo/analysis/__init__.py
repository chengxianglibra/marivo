"""Marivo Python-native analysis runtime (analysis)."""

from typing import Any

from marivo.analysis import errors as errors
from marivo.analysis import session
from marivo.analysis.calendar.model import CalendarPolicy
from marivo.analysis.datasources.metadata import (
    ColumnMetadata,
    MetadataWarning,
    PartitionMetadata,
    TableMetadata,
)
from marivo.analysis.errors import DiscoverInsufficientDataError, PromotionFailedError
from marivo.analysis.evidence import (
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
from marivo.analysis.followups import (
    BlockingIssue,
    ConfidenceScope,
    FollowupAction,
)
from marivo.analysis.frames.association import AssociationResult
from marivo.analysis.frames.attribution import AttributionFrame
from marivo.analysis.frames.base import BaseFrame
from marivo.analysis.frames.candidate import (
    CandidateObjective,
    CandidateSet,
)
from marivo.analysis.frames.component import ComponentFrame
from marivo.analysis.frames.delta import DeltaFrame
from marivo.analysis.frames.exploration import ExplorationResult
from marivo.analysis.frames.forecast import ForecastFrame
from marivo.analysis.frames.hypothesis import HypothesisTestResult
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.frames.quality import (
    CheckResult,
    QualityReport,
    QualityReportSummary,
)
from marivo.analysis.help import help, help_text
from marivo.analysis.intents._types import (
    DiscoverSensitivity,
    SlicePredicate,
    SlicePredicateOp,
    SliceScalar,
    SliceValue,
)
from marivo.analysis.policies import (
    AlignmentKind,
    AlignmentPolicy,
    LagPolicy,
    PromotionPolicy,
    PromotionSemanticAnchors,
    SamplingPolicy,
)
from marivo.analysis.refs import ArtifactRef, CalendarRef, DimensionRef, MetricRef
from marivo.analysis.session._introspection import install_intent_docstrings
from marivo.analysis.session._load import load_frame
from marivo.analysis.session.core import FrameRef, FrameSummaryEntry
from marivo.analysis.windows import GrainUnit, ensure_grain_supported
from marivo.analysis.windows.spec import (
    AbsoluteWindow,
    Grain,
    GrainInput,
    TimeGrain,
    TimeScope,
    TimeScopeInput,
)
from marivo.preview import PreviewResult, PreviewSamplePolicy, PreviewWarning


def __getattr__(name: str) -> Any:
    if name == "datasources":
        from importlib import import_module

        return import_module("marivo.analysis.datasources")
    if name == "publish":
        from importlib import import_module

        return import_module("marivo.analysis.publish")
    raise AttributeError(name)


__all__ = [
    "AbsoluteWindow",
    "AlignmentKind",
    "AlignmentPolicy",
    "ArtifactRef",
    "Assessment",
    "AssociationResult",
    "AssociationSummary",
    "AttributedDriver",
    "AttributionFrame",
    "BaseFrame",
    "BlockedFollowup",
    "BlockingIssue",
    "CalendarPolicy",
    "CalendarRef",
    "CandidateObjective",
    "CandidateSet",
    "ChangeFact",
    "CheckResult",
    "ColumnMetadata",
    "ComponentFrame",
    "ConfidenceScope",
    "DeltaFrame",
    "DimensionRef",
    "DiscoverInsufficientDataError",
    "DiscoverSensitivity",
    "EvidenceTrace",
    "ExplorationResult",
    "Finding",
    "FollowupAction",
    "ForecastFrame",
    "ForecastSummary",
    "FrameRef",
    "FrameSummaryEntry",
    "Grain",
    "GrainInput",
    "GrainUnit",
    "HypothesisTestResult",
    "LagPolicy",
    "MetadataWarning",
    "MetricFrame",
    "MetricRef",
    "OpenAnomaly",
    "OpenQuestion",
    "PartitionMetadata",
    "PreviewResult",
    "PreviewSamplePolicy",
    "PreviewWarning",
    "PromotionFailedError",
    "PromotionPolicy",
    "PromotionSemanticAnchors",
    "Proposition",
    "QualityReport",
    "QualityReportSummary",
    "QualitySummary",
    "SamplingPolicy",
    "SessionKnowledge",
    "SlicePredicate",
    "SlicePredicateOp",
    "SliceScalar",
    "SliceValue",
    "Subject",
    "TableMetadata",
    "TestedHypothesis",
    "TimeGrain",
    "TimeScope",
    "TimeScopeInput",
    "TimeWindow",
    "TriggeredByFollowup",
    "datasources",
    "ensure_grain_supported",
    "errors",
    "help",
    "help_text",
    "load_frame",
    "publish",
    "session",
]


# Mirror intent docstrings onto Session.observe/compare/... so help() and IPython
# `?` surface them. Real type annotations live in core.py source; only the
# docstring text is copied here (authored once on the intent functions).
install_intent_docstrings()
