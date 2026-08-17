"""Marivo's typed analysis runtime."""

from datetime import date as _date
from datetime import datetime as _datetime
from typing import Any as _Any
from typing import Literal

from marivo._temporal import Grain
from marivo._temporal import time_scope as _time_scope
from marivo.analysis import errors as errors
from marivo.analysis import runtime_metric as runtime_metric
from marivo.analysis import session
from marivo.analysis.candidate_lineage import CandidateOrigin, CandidateResolutionIssue
from marivo.analysis.event import (
    CompletenessDeclaration,
    EventOccurrenceBounds,
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
    day_of_week,
    occurrence_progress,
    period_correspondence,
    period_progress,
    window_bucket,
    working_day_progress,
)
from marivo.analysis.refs import ArtifactRef
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


def grain(
    unit: Literal[
        "second",
        "minute",
        "hour",
        "day",
        "week",
        "month",
        "quarter",
        "year",
    ],
    *,
    count: int = 1,
) -> Grain:
    """Construct one builtin aggregation grain.

    Args:
        unit: One builtin unit from second through year.
        count: Positive sub-day width; calendar-variable units require one.

    Returns:
        The immutable public Grain value.

    Example:
        >>> import marivo.analysis as mv
        >>> mv.grain("month")

    Constraints:
        Semantic calendar levels are constructed by ``ms.calendar_grain(...)``.
    """
    from marivo._temporal import builtin_grain

    return builtin_grain(unit, count=count)


def time_scope(
    *,
    start: _date | _datetime | str,
    end: _date | _datetime | str,
) -> TimeScope:
    """Construct one validated absolute analysis scope.

    Calendar-period scopes come from certified catalog lookups; absolute
    callers should use this helper rather than constructing ``TimeScope``
    directly.
    """

    return _time_scope(start=start, end=end)


def __getattr__(name: str) -> _Any:
    if name == "evidence":
        from importlib import import_module

        return import_module("marivo.analysis.evidence")
    if name == "frames":
        from importlib import import_module

        return import_module("marivo.analysis.frames")
    if name == "help":
        raise AttributeError(
            "module 'marivo.analysis' has no attribute 'help'; the single public "
            "help coordinator lives on the top-level namespace — use marivo.help(...)"
        )
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
    "EventOccurrenceBounds",
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
    "Grain",
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
    "day_of_week",
    "declared_complete_through",
    "dropped_before",
    "every_start",
    "first_per_subject",
    "from_inception",
    "funnel_loss_rate",
    "grain",
    "in_state",
    "occurrence_progress",
    "period_correspondence",
    "period_progress",
    "runtime_metric",
    "sequence",
    "session",
    "step",
    "time_scope",
    "window_bucket",
    "working_day_progress",
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
