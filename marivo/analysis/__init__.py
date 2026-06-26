"""Marivo Python-native analysis runtime (analysis)."""

from typing import Any as _Any

from marivo.analysis import errors as errors
from marivo.analysis import session
from marivo.analysis.calendar.model import CalendarPolicy
from marivo.analysis.followups import (
    BlockingIssue,
    ConfidenceScope,
)
from marivo.analysis.frames.association import AssociationResult
from marivo.analysis.frames.attribution import AttributionFrame
from marivo.analysis.frames.base import (
    ArtifactAffordance,
    ArtifactColumn,
    ArtifactContract,
    ArtifactParamTemplate,
    ArtifactPrecondition,
    ArtifactSchema,
    ArtifactState,
    BaseFrame,
    BaseFrameMeta,
    FramePreview,
    FrameSummary,
)
from marivo.analysis.frames.candidate import (
    CandidateObjective,
    CandidateSet,
)
from marivo.analysis.frames.component import ComponentFrame
from marivo.analysis.frames.coverage import CoverageFrame
from marivo.analysis.frames.delta import DeltaFrame
from marivo.analysis.frames.exploration import ExplorationResult
from marivo.analysis.frames.forecast import ForecastFrame
from marivo.analysis.frames.hypothesis import HypothesisTestResult
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.frames.quality import QualityReport
from marivo.analysis.help import help, help_text
from marivo.analysis.intents._types import (
    DiscoverSensitivity,
    SlicePredicate,
    SlicePredicateOp,
    SliceScalar,
    SliceValue,
)
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.policies import (
    AlignmentKind,
    AlignmentPolicy,
    PromotionPolicy,
    PromotionSemanticAnchors,
    SamplingPolicy,
    dow_aligned,
    holiday_aligned,
    holiday_and_dow_aligned,
    window_bucket,
)
from marivo.analysis.refs import ArtifactRef, CalendarRef
from marivo.analysis.session._store import SessionSummary
from marivo.analysis.session.core import FrameSummaryEntry, JobSummary, ReportRegistration, Session
from marivo.analysis.windows.spec import (
    AbsoluteWindow,
    TimeScope,
    TimeScopeInput,
)
from marivo.refs import SemanticRef
from marivo.semantic.catalog import SemanticObject


def __getattr__(name: str) -> _Any:
    if name == "evidence":
        from importlib import import_module

        return import_module("marivo.analysis.evidence")
    if name == "frames":
        from importlib import import_module

        return import_module("marivo.analysis.frames")
    if name == "publish":
        from importlib import import_module

        return import_module("marivo.analysis.publish")
    if name == "SemanticKind":
        from marivo.semantic.catalog import SemanticKind

        return SemanticKind
    raise AttributeError(name)


__all__ = [
    "AbsoluteWindow",
    "AlignmentKind",
    "AlignmentPolicy",
    "ArtifactAffordance",
    "ArtifactColumn",
    "ArtifactContract",
    "ArtifactParamTemplate",
    "ArtifactPrecondition",
    "ArtifactRef",
    "ArtifactSchema",
    "ArtifactState",
    "AssociationResult",
    "AttributionFrame",
    "BaseFrame",
    "BaseFrameMeta",
    "BlockingIssue",
    "CalendarPolicy",
    "CalendarRef",
    "CandidateObjective",
    "CandidateSet",
    "ComponentFrame",
    "ConfidenceScope",
    "CoverageFrame",
    "DeltaFrame",
    "DiscoverSensitivity",
    "ExplorationResult",
    "ForecastFrame",
    "FramePreview",
    "FrameSummary",
    "FrameSummaryEntry",
    "HypothesisTestResult",
    "JobSummary",
    "Lineage",
    "LineageStep",
    "MetricFrame",
    "PromotionPolicy",
    "PromotionSemanticAnchors",
    "QualityReport",
    "ReportRegistration",
    "SamplingPolicy",
    "SemanticObject",
    "SemanticRef",
    "Session",
    "SessionSummary",
    "SlicePredicate",
    "SlicePredicateOp",
    "SliceScalar",
    "SliceValue",
    "TimeScope",
    "TimeScopeInput",
    "dow_aligned",
    "errors",
    "evidence",
    "frames",
    "help",
    "help_text",
    "holiday_aligned",
    "holiday_and_dow_aligned",
    "publish",
    "session",
    "window_bucket",
]
