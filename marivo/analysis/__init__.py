"""Marivo's typed analysis runtime, loaded on first public use."""

from datetime import date as _date
from datetime import datetime as _datetime
from types import ModuleType
from typing import TYPE_CHECKING, Literal

from marivo._temporal import Grain, TimeScope
from marivo._temporal import time_scope as _time_scope

if TYPE_CHECKING:
    from marivo.analysis import errors as errors
    from marivo.analysis import runtime_metric as runtime_metric
    from marivo.analysis import session
    from marivo.analysis.candidate_lineage import CandidateOrigin, CandidateResolutionIssue
    from marivo.analysis.errors import EvidenceIntegrityError
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
        ArtifactIssue,
        ArtifactRevalidation,
        AssociationFact,
        ChangeFact,
        ComparabilityIssue,
        ContributionFact,
        DataQualityIssue,
        EvidenceAvailabilityIssue,
        EvidenceRuleIssue,
        ForecastOutput,
        ObservationFact,
        QualityCheckResult,
        TestDecision,
    )
    from marivo.analysis.evidence.artifact_reads import Finding, FindingPage
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
    from marivo.analysis.session._read_model import (
        ArtifactSummary,
        FailedRun,
        IncompleteRun,
        RunPage,
        SessionGraph,
        SucceededRun,
    )
    from marivo.analysis.session._store import SessionSummary
    from marivo.analysis.session.core import Session
    from marivo.analysis.slice_types import (
        SlicePredicate,
        SlicePredicateOp,
        SliceScalar,
        SliceValue,
    )
    from marivo.analysis.subject import DroppedBefore, dropped_before
    from marivo.analysis.windows.spec import (
        AbsoluteWindow,
        TimeScopeInput,
    )


def __getattr__(name: str) -> object:
    from importlib import import_module
    from importlib.util import find_spec

    if name.startswith("__") and name not in ("__all__", "__marivo_telemetry_capabilities__"):
        raise AttributeError(name)
    # Private workers can import their owning modules without initializing every
    # public Frame, Help descriptor and telemetry wrapper in the analysis surface.
    if not name.startswith("_") and find_spec(f"marivo.analysis.{name}") is not None:
        return import_module(f"marivo.analysis.{name}")
    return getattr(_initialize_public(), name)


def _initialize_public() -> ModuleType:
    from importlib import import_module

    public = import_module("marivo.analysis._public")
    import_module("marivo.analysis._capabilities.registry")
    return public


def __dir__() -> list[str]:
    return sorted(_initialize_public().__all__)


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

    _initialize_public()
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

    _initialize_public()
    return _time_scope(start=start, end=end)
