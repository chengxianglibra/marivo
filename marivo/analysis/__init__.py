"""Marivo's typed analysis runtime."""

from typing import Any as _Any

from marivo.analysis import errors as errors
from marivo.analysis import runtime_metric as runtime_metric
from marivo.analysis import session
from marivo.analysis.calendar.model import CalendarPolicy
from marivo.analysis.candidate_lineage import CandidateOrigin, CandidateResolutionIssue
from marivo.analysis.event import (
    CompletenessDeclaration,
    EventPattern,
    EventWatermarkReceipt,
    EventWatermarkRequest,
    EveryStart,
    FirstPerSubject,
    PatternStep,
    declared_complete_through,
    every_start,
    first_per_subject,
    sequence,
    step,
)
from marivo.analysis.evidence import (
    AnalysisScope,
    AnomalyCandidate,
    ArtifactDigest,
    ArtifactDigestPage,
    ArtifactIssue,
    AssociationFact,
    ChangeFact,
    ComparabilityIssue,
    ContributionFact,
    DataQualityIssue,
    EvidenceAvailabilityIssue,
    EvidenceDerivationTrace,
    Finding,
    FindingPage,
    ForecastOutput,
    ObservationFact,
    QualityCheckResult,
    TestDecision,
)
from marivo.analysis.frames.association import AssociationResult
from marivo.analysis.frames.attribution import AttributionFrame
from marivo.analysis.frames.base import (
    ArtifactAffordance,
    ArtifactColumn,
    ArtifactContract,
    ArtifactInputRequirement,
    ArtifactPrecondition,
    ArtifactSchema,
    ArtifactSemanticInput,
    ArtifactState,
    BaseFrame,
    BaseFrameMeta,
)
from marivo.analysis.frames.candidate import (
    CandidateObjective,
    CandidateSelection,
    CandidateSet,
    CrossSectionalOutlierSelection,
    DriverAxisSelection,
    OntologyMetricCandidate,
    PeriodShiftSelection,
    PointAnomalySelection,
    SliceSelection,
    WindowSelection,
)
from marivo.analysis.frames.component import ComponentFrame
from marivo.analysis.frames.coverage import CoverageFrame
from marivo.analysis.frames.delta import DeltaFrame
from marivo.analysis.frames.event import EventFrame
from marivo.analysis.frames.forecast import ForecastFrame
from marivo.analysis.frames.hypothesis import HypothesisTestResult
from marivo.analysis.frames.lifecycle import LifecycleFrame
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.frames.quality import QualityReport
from marivo.analysis.frames.subject import SubjectSet
from marivo.analysis.funnel import FunnelLossRate, funnel_loss_rate
from marivo.analysis.lifecycle import FromInception, InState, from_inception, in_state
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.policies import (
    AlignmentKind,
    AlignmentPolicy,
    SamplingPolicy,
    dow_aligned,
    holiday_aligned,
    holiday_and_dow_aligned,
    window_bucket,
)
from marivo.analysis.refs import ArtifactRef, CalendarRef
from marivo.analysis.session._store import SessionSummary
from marivo.analysis.session.core import (
    FrameSummaryEntry,
    FrameSummaryPage,
    JobSummary,
    Session,
)
from marivo.analysis.slice_types import (
    SlicePredicate,
    SlicePredicateOp,
    SliceScalar,
    SliceValue,
)
from marivo.analysis.subject import DroppedBefore, dropped_before
from marivo.analysis.windows.spec import (
    AbsoluteWindow,
    TimeScope,
    TimeScopeInput,
)


def __getattr__(name: str) -> _Any:
    if name == "evidence":
        from importlib import import_module

        return import_module("marivo.analysis.evidence")
    if name == "frames":
        from importlib import import_module

        return import_module("marivo.analysis.frames")
    raise AttributeError(name)


def __dir__() -> list[str]:
    return sorted(__all__)


__all__ = [
    "AbsoluteWindow",
    "AlignmentPolicy",
    "AnalysisScope",
    "AnomalyCandidate",
    "ArtifactDigest",
    "ArtifactDigestPage",
    "ArtifactIssue",
    "ArtifactRef",
    "AssociationFact",
    "AssociationResult",
    "AttributionFrame",
    "CalendarRef",
    "CandidateOrigin",
    "CandidateResolutionIssue",
    "CandidateSelection",
    "CandidateSet",
    "ChangeFact",
    "ComparabilityIssue",
    "CompletenessDeclaration",
    "ContributionFact",
    "CrossSectionalOutlierSelection",
    "DataQualityIssue",
    "DeltaFrame",
    "DriverAxisSelection",
    "DroppedBefore",
    "EventFrame",
    "EventPattern",
    "EventWatermarkReceipt",
    "EventWatermarkRequest",
    "EveryStart",
    "EvidenceAvailabilityIssue",
    "EvidenceDerivationTrace",
    "Finding",
    "FindingPage",
    "FirstPerSubject",
    "ForecastFrame",
    "ForecastOutput",
    "FrameSummaryEntry",
    "FrameSummaryPage",
    "FromInception",
    "FunnelLossRate",
    "HypothesisTestResult",
    "InState",
    "LifecycleFrame",
    "MetricFrame",
    "ObservationFact",
    "OntologyMetricCandidate",
    "PatternStep",
    "PeriodShiftSelection",
    "PointAnomalySelection",
    "QualityCheckResult",
    "QualityReport",
    "Session",
    "SliceSelection",
    "SubjectSet",
    "TestDecision",
    "TimeScope",
    "WindowSelection",
    "declared_complete_through",
    "dow_aligned",
    "dropped_before",
    "every_start",
    "first_per_subject",
    "from_inception",
    "funnel_loss_rate",
    "holiday_aligned",
    "holiday_and_dow_aligned",
    "in_state",
    "runtime_metric",
    "sequence",
    "session",
    "step",
    "window_bucket",
]


def _install_telemetry() -> None:
    import sys

    from marivo.analysis._capabilities.registry import REGISTRY
    from marivo.telemetry import install_surface_instrumentation

    install_surface_instrumentation(
        surface="analysis",
        descriptors=REGISTRY._descriptors,
        root_module=sys.modules[__name__],
    )


_install_telemetry()
