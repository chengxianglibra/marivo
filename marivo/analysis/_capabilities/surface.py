"""Analysis-owned configuration for the neutral live target resolver."""

from __future__ import annotations

import inspect
from types import MappingProxyType
from typing import Literal, NoReturn, cast

from marivo.analysis._capabilities.model import CapabilityDescriptor
from marivo.analysis._capabilities.registry import REGISTRY
from marivo.analysis.errors import AnalysisError, HelpTargetError
from marivo.introspection.live.resolve import (
    LiveSurface,
    ResolvedLiveTarget,
    build_string_target_index,
    build_suggestion_index,
)


def _build_type_registry() -> MappingProxyType[type, str]:
    """Build the exact public analysis type index."""
    from marivo.analysis.event import (
        CompletenessDeclaration,
        EventPattern,
        EventWatermarkReceipt,
        EventWatermarkRequest,
        EveryStart,
        FirstPerSubject,
        PatternStep,
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
    from marivo.analysis.frames.base import BaseFrame
    from marivo.analysis.frames.candidate import (
        CandidateSelection,
        CandidateSet,
        CrossSectionalOutlierSelection,
        DriverAxisSelection,
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
    from marivo.analysis.frames.metric import MetricFrame
    from marivo.analysis.frames.quality import QualityReport
    from marivo.analysis.frames.subject import SubjectSet
    from marivo.analysis.session.core import FrameSummaryEntry, FrameSummaryPage, Session
    from marivo.analysis.subject import DroppedBefore

    return MappingProxyType(
        {
            Session: "Session",
            BaseFrame: "BaseFrame",
            MetricFrame: "MetricFrame",
            EventFrame: "EventFrame",
            SubjectSet: "SubjectSet",
            DeltaFrame: "DeltaFrame",
            AttributionFrame: "AttributionFrame",
            CandidateSet: "CandidateSet",
            ForecastFrame: "ForecastFrame",
            QualityReport: "QualityReport",
            HypothesisTestResult: "HypothesisTestResult",
            AssociationResult: "AssociationResult",
            ComponentFrame: "ComponentFrame",
            CoverageFrame: "CoverageFrame",
            AnalysisScope: "AnalysisScope",
            Finding: "Finding",
            ArtifactDigest: "ArtifactDigest",
            EvidenceDerivationTrace: "EvidenceDerivationTrace",
            ObservationFact: "ObservationFact",
            ChangeFact: "ChangeFact",
            ContributionFact: "ContributionFact",
            AssociationFact: "AssociationFact",
            TestDecision: "TestDecision",
            ForecastOutput: "ForecastOutput",
            AnomalyCandidate: "AnomalyCandidate",
            QualityCheckResult: "QualityCheckResult",
            DataQualityIssue: "DataQualityIssue",
            ComparabilityIssue: "ComparabilityIssue",
            EvidenceAvailabilityIssue: "EvidenceAvailabilityIssue",
            ArtifactDigestPage: "ArtifactDigestPage",
            FindingPage: "FindingPage",
            FrameSummaryPage: "FrameSummaryPage",
            FrameSummaryEntry: "FrameSummaryEntry",
            PointAnomalySelection: "PointAnomalySelection",
            PeriodShiftSelection: "PeriodShiftSelection",
            DriverAxisSelection: "DriverAxisSelection",
            SliceSelection: "SliceSelection",
            WindowSelection: "WindowSelection",
            CrossSectionalOutlierSelection: "CrossSectionalOutlierSelection",
            PatternStep: "PatternStep",
            EventPattern: "EventPattern",
            FirstPerSubject: "FirstPerSubject",
            EveryStart: "EveryStart",
            CompletenessDeclaration: "CompletenessDeclaration",
            DroppedBefore: "DroppedBefore",
            EventWatermarkRequest: "EventWatermarkRequest",
            EventWatermarkReceipt: "EventWatermarkReceipt",
            cast("type", ArtifactIssue): "ArtifactIssue",
            cast("type", CandidateSelection): "CandidateSelection",
        }
    )


def _build_error_registry() -> MappingProxyType[str, type]:
    """Build the exact analysis error-name index from the installed module."""
    import marivo.analysis.errors as errors

    return MappingProxyType(
        {
            name: error_type
            for name, error_type in inspect.getmembers(errors, inspect.isclass)
            if issubclass(error_type, AnalysisError)
        }
    )


TYPE_REGISTRY = _build_type_registry()
ERROR_TYPES = _build_error_registry()


def _help_target_error(target: object, suggestions: tuple[str, ...]) -> NoReturn:
    raise HelpTargetError(
        target=target,
        suggestions=suggestions,
        owning_surface=_cross_surface_owner(target),
    )


def _cross_surface_owner(
    target: object,
) -> Literal["datasource", "semantic"] | None:
    """Return the owner of a public callable rejected by analysis help."""
    callable_target = getattr(target, "__func__", target)
    module = getattr(callable_target, "__module__", None)
    if not isinstance(module, str):
        module = type(target).__module__
    if module.startswith("marivo.datasource"):
        return "datasource"
    if module.startswith("marivo.semantic"):
        return "semantic"
    return None


def _enrich(target: object) -> ResolvedLiveTarget[CapabilityDescriptor] | None:
    """Resolve analysis-owned runtime briefings before generic dispatch."""
    from marivo.analysis.evidence import ArtifactIssue
    from marivo.analysis.frames.candidate import CandidateSelection

    if target is ArtifactIssue:
        return ResolvedLiveTarget(
            kind="type_contract",
            surface="analysis",
            type_name="ArtifactIssue",
        )
    if target is CandidateSelection:
        return ResolvedLiveTarget(
            kind="type_contract",
            surface="analysis",
            type_name="CandidateSelection",
        )
    if isinstance(target, AnalysisError):
        repair = target.repair
        help_target = repair.help_target if repair is not None else None
        if help_target is None:
            return ResolvedLiveTarget(
                kind="error_contract",
                surface="analysis",
                error_name=type(target).__name__,
            )
        return ResolvedLiveTarget(
            kind="error_briefing",
            surface="analysis",
            error_name=type(target).__name__,
            error_kind=target.kind,
            original=target,
        )

    from marivo.refs import Ref

    if type(target) is Ref:
        return ResolvedLiveTarget(
            kind="reference_briefing",
            surface="analysis",
            reference_id=target.path,
            original=target,
        )

    from marivo.semantic.catalog import CatalogEntry

    if isinstance(target, CatalogEntry):
        return ResolvedLiveTarget(
            kind="reference_briefing",
            surface="analysis",
            reference_id=target.path,
            original=target,
        )
    return None


ANALYSIS_LIVE_SURFACE: LiveSurface[CapabilityDescriptor] = LiveSurface(
    registry=REGISTRY,
    type_index=TYPE_REGISTRY,
    error_types=ERROR_TYPES,
    error_base=AnalysisError,
    default_suggestions=("observe", "compare", "attribute", "forecast", "help"),
    help_target_error=_help_target_error,
    enrich=_enrich,
    string_target_index=build_string_target_index(
        REGISTRY,
        public_type_names=frozenset(TYPE_REGISTRY.values()),
    ),
    suggestion_index=build_suggestion_index(REGISTRY),
)
