"""Eager public exports, initialized only by public analysis consumers."""

from typing import Any as _Any

import marivo.analysis.runtime_metric as runtime_metric
import marivo.analysis.session as session
from marivo._temporal import Grain as Grain
from marivo.analysis import errors as errors
from marivo.analysis import grain as grain
from marivo.analysis import time_scope as time_scope
from marivo.analysis.candidate_lineage import CandidateOrigin as CandidateOrigin
from marivo.analysis.candidate_lineage import CandidateResolutionIssue as CandidateResolutionIssue
from marivo.analysis.errors import EvidenceIntegrityError as EvidenceIntegrityError
from marivo.analysis.event import CompletenessDeclaration as CompletenessDeclaration
from marivo.analysis.event import EventOccurrenceBounds as EventOccurrenceBounds
from marivo.analysis.event import EventPattern as EventPattern
from marivo.analysis.event import EventWatermarkReceipt as EventWatermarkReceipt
from marivo.analysis.event import EventWatermarkRequest as EventWatermarkRequest
from marivo.analysis.event import EveryStart as EveryStart
from marivo.analysis.event import FirstPerSubject as FirstPerSubject
from marivo.analysis.event import PatternStep as PatternStep
from marivo.analysis.event import declared_complete_through as declared_complete_through
from marivo.analysis.event import every_start as every_start
from marivo.analysis.event import first_per_subject as first_per_subject
from marivo.analysis.event import sequence as sequence
from marivo.analysis.event import step as step
from marivo.analysis.evidence import AnalysisScope as AnalysisScope
from marivo.analysis.evidence import AnomalyCandidate as AnomalyCandidate
from marivo.analysis.evidence import ArtifactDigest as ArtifactDigest
from marivo.analysis.evidence import ArtifactIssue as ArtifactIssue
from marivo.analysis.evidence import ArtifactRevalidation as ArtifactRevalidation
from marivo.analysis.evidence import AssociationFact as AssociationFact
from marivo.analysis.evidence import ChangeFact as ChangeFact
from marivo.analysis.evidence import ComparabilityIssue as ComparabilityIssue
from marivo.analysis.evidence import ContributionFact as ContributionFact
from marivo.analysis.evidence import DataQualityIssue as DataQualityIssue
from marivo.analysis.evidence import EvidenceAvailabilityIssue as EvidenceAvailabilityIssue
from marivo.analysis.evidence import EvidenceRuleIssue as EvidenceRuleIssue
from marivo.analysis.evidence import ForecastOutput as ForecastOutput
from marivo.analysis.evidence import ObservationFact as ObservationFact
from marivo.analysis.evidence import QualityCheckResult as QualityCheckResult
from marivo.analysis.evidence import TestDecision as TestDecision
from marivo.analysis.evidence.artifact_reads import Finding as Finding
from marivo.analysis.evidence.artifact_reads import FindingPage as FindingPage
from marivo.analysis.frames.association import AssociationResult as AssociationResult
from marivo.analysis.frames.attribution import AttributionFrame as AttributionFrame
from marivo.analysis.frames.base import ArtifactAffordance as ArtifactAffordance
from marivo.analysis.frames.base import ArtifactColumn as ArtifactColumn
from marivo.analysis.frames.base import ArtifactContract as ArtifactContract
from marivo.analysis.frames.base import ArtifactInputRequirement as ArtifactInputRequirement
from marivo.analysis.frames.base import ArtifactPrecondition as ArtifactPrecondition
from marivo.analysis.frames.base import ArtifactSchema as ArtifactSchema
from marivo.analysis.frames.base import ArtifactSemanticInput as ArtifactSemanticInput
from marivo.analysis.frames.base import ArtifactState as ArtifactState
from marivo.analysis.frames.base import BaseFrame as BaseFrame
from marivo.analysis.frames.base import BaseFrameMeta as BaseFrameMeta
from marivo.analysis.frames.candidate import CandidateObjective as CandidateObjective
from marivo.analysis.frames.candidate import CandidateSelection as CandidateSelection
from marivo.analysis.frames.candidate import CandidateSet as CandidateSet
from marivo.analysis.frames.candidate import (
    CrossSectionalOutlierSelection as CrossSectionalOutlierSelection,
)
from marivo.analysis.frames.candidate import DriverAxisSelection as DriverAxisSelection
from marivo.analysis.frames.candidate import OntologyMetricCandidate as OntologyMetricCandidate
from marivo.analysis.frames.candidate import PeriodShiftSelection as PeriodShiftSelection
from marivo.analysis.frames.candidate import PointAnomalySelection as PointAnomalySelection
from marivo.analysis.frames.candidate import SliceSelection as SliceSelection
from marivo.analysis.frames.candidate import WindowSelection as WindowSelection
from marivo.analysis.frames.component import ComponentFrame as ComponentFrame
from marivo.analysis.frames.coverage import CoverageFrame as CoverageFrame
from marivo.analysis.frames.delta import DeltaFrame as DeltaFrame
from marivo.analysis.frames.event import EventFrame as EventFrame
from marivo.analysis.frames.forecast import ForecastFrame as ForecastFrame
from marivo.analysis.frames.hypothesis import HypothesisTestResult as HypothesisTestResult
from marivo.analysis.frames.lifecycle import LifecycleFrame as LifecycleFrame
from marivo.analysis.frames.metric import MetricFrame as MetricFrame
from marivo.analysis.frames.subject import SubjectSet as SubjectSet
from marivo.analysis.funnel import FunnelLossRate as FunnelLossRate
from marivo.analysis.funnel import funnel_loss_rate as funnel_loss_rate
from marivo.analysis.lifecycle import FromInception as FromInception
from marivo.analysis.lifecycle import InState as InState
from marivo.analysis.lifecycle import from_inception as from_inception
from marivo.analysis.lifecycle import in_state as in_state
from marivo.analysis.lineage import Lineage as Lineage
from marivo.analysis.lineage import LineageStep as LineageStep
from marivo.analysis.policies import AlignmentKind as AlignmentKind
from marivo.analysis.policies import AlignmentPolicy as AlignmentPolicy
from marivo.analysis.policies import SamplingPolicy as SamplingPolicy
from marivo.analysis.policies import day_of_week as day_of_week
from marivo.analysis.policies import occurrence_progress as occurrence_progress
from marivo.analysis.policies import period_correspondence as period_correspondence
from marivo.analysis.policies import period_progress as period_progress
from marivo.analysis.policies import window_bucket as window_bucket
from marivo.analysis.policies import working_day_progress as working_day_progress
from marivo.analysis.refs import ArtifactRef as ArtifactRef
from marivo.analysis.session._read_model import ArtifactSummary as ArtifactSummary
from marivo.analysis.session._read_model import FailedRun as FailedRun
from marivo.analysis.session._read_model import IncompleteRun as IncompleteRun
from marivo.analysis.session._read_model import RunPage as RunPage
from marivo.analysis.session._read_model import SessionGraph as SessionGraph
from marivo.analysis.session._read_model import SucceededRun as SucceededRun
from marivo.analysis.session._store import SessionSummary as SessionSummary
from marivo.analysis.session.core import Session as Session
from marivo.analysis.slice_types import SlicePredicate as SlicePredicate
from marivo.analysis.slice_types import SlicePredicateOp as SlicePredicateOp
from marivo.analysis.slice_types import SliceScalar as SliceScalar
from marivo.analysis.slice_types import SliceValue as SliceValue
from marivo.analysis.subject import DroppedBefore as DroppedBefore
from marivo.analysis.subject import dropped_before as dropped_before
from marivo.analysis.windows.spec import AbsoluteWindow as AbsoluteWindow
from marivo.analysis.windows.spec import TimeScope as TimeScope
from marivo.analysis.windows.spec import TimeScopeInput as TimeScopeInput


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
    if name == "catalog":
        raise AttributeError(
            "module 'marivo.analysis' has no attribute 'catalog'; catalog is session-bound — "
            "use session = mv.session.get_or_create('<stable-session-name>', "
            "question='<business question>'), then catalog = session.catalog"
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
    "ArtifactIssue",
    "ArtifactRef",
    "ArtifactRevalidation",
    "ArtifactSummary",
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
    "EvidenceIntegrityError",
    "EvidenceRuleIssue",
    "FailedRun",
    "Finding",
    "FindingPage",
    "FirstPerSubject",
    "ForecastFrame",
    "ForecastOutput",
    "FromInception",
    "FunnelLossRate",
    "Grain",
    "HypothesisTestResult",
    "InState",
    "IncompleteRun",
    "LifecycleFrame",
    "MetricFrame",
    "ObservationFact",
    "OntologyMetricCandidate",
    "PatternStep",
    "PeriodShiftSelection",
    "PointAnomalySelection",
    "QualityCheckResult",
    "RunPage",
    "Session",
    "SessionGraph",
    "SliceSelection",
    "SubjectSet",
    "SucceededRun",
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
