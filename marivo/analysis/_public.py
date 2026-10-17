"""Canonical lazy Query construction and terminal output reading surface."""

import marivo.analysis.session as session
from marivo._definition.correspondence import CalendarCorrespondence as CalendarCorrespondence
from marivo._definition.correspondence import CorrespondenceAxis as CorrespondenceAxis
from marivo._definition.correspondence import DeclaredCorrespondence as DeclaredCorrespondence
from marivo._temporal import BeforeEndBoundary as BeforeEndBoundary
from marivo._temporal import Grain as Grain
from marivo._temporal import InstantAnchor as InstantAnchor
from marivo._temporal import TimeScope as TimeScope
from marivo.analysis import grain as grain
from marivo.analysis._contract import AnalysisAction as AnalysisAction
from marivo.analysis._contract import AnalysisContract as AnalysisContract
from marivo.analysis.dsl.artifact import Artifact as Artifact
from marivo.analysis.dsl.column import Column as Column
from marivo.analysis.dsl.column import row_count as row_count
from marivo.analysis.dsl.correspondence import corresponding as corresponding
from marivo.analysis.dsl.correspondence import ordinal as ordinal
from marivo.analysis.dsl.correspondence import period_offset as period_offset
from marivo.analysis.dsl.correspondence import previous_year as previous_year
from marivo.analysis.dsl.metrics import member_count as member_count
from marivo.analysis.dsl.population import MemberAxis as MemberAxis
from marivo.analysis.dsl.population import MembershipAxis as MembershipAxis
from marivo.analysis.dsl.population import Population as Population
from marivo.analysis.dsl.population import PopulationPredicate as PopulationPredicate
from marivo.analysis.dsl.population import coordinates as coordinates
from marivo.analysis.dsl.population import identities as identities
from marivo.analysis.dsl.population import in_population as in_population
from marivo.analysis.dsl.population import member as member
from marivo.analysis.dsl.population import membership as membership
from marivo.analysis.dsl.population import product as product
from marivo.analysis.dsl.query import Query as Query
from marivo.analysis.dsl.references import broadcast as broadcast
from marivo.analysis.dsl.temporal import CalendarAxis as CalendarAxis
from marivo.analysis.dsl.windows import all_history as all_history
from marivo.analysis.dsl.windows import since as since
from marivo.analysis.dsl.windows import to_date as to_date
from marivo.analysis.dsl.windows import trailing_duration as trailing_duration
from marivo.analysis.dsl.windows import trailing_periods as trailing_periods
from marivo.analysis.evidence._dataset_types import ArtifactDigest as ArtifactDigest
from marivo.analysis.evidence._dataset_types import Finding as Finding
from marivo.analysis.evidence._dataset_types import FindingPage as FindingPage
from marivo.analysis.refs import ArtifactRef as ArtifactRef
from marivo.analysis.session._lazy_read_model import ArtifactSummary as ArtifactSummary
from marivo.analysis.session._lazy_read_model import FailedRun as FailedRun
from marivo.analysis.session._lazy_read_model import IncompleteRun as IncompleteRun
from marivo.analysis.session._lazy_read_model import RunPage as RunPage
from marivo.analysis.session._lazy_read_model import SessionGraph as SessionGraph
from marivo.analysis.session._lazy_read_model import SucceededRun as SucceededRun
from marivo.analysis.session.core import Session as Session

__all__ = [  # noqa: RUF022 - public order is contractual
    "Query",
    "Column",
    "Population",
    "PopulationPredicate",
    "MembershipAxis",
    "MemberAxis",
    "CorrespondenceAxis",
    "CalendarCorrespondence",
    "DeclaredCorrespondence",
    "identities",
    "coordinates",
    "membership",
    "product",
    "in_population",
    "member",
    "member_count",
    "row_count",
    "broadcast",
    "corresponding",
    "ordinal",
    "period_offset",
    "previous_year",
    "grain",
    "Grain",
    "TimeScope",
    "InstantAnchor",
    "BeforeEndBoundary",
    "CalendarAxis",
    "all_history",
    "since",
    "to_date",
    "trailing_duration",
    "trailing_periods",
    "Artifact",
    "ArtifactDigest",
    "ArtifactRef",
    "ArtifactSummary",
    "Finding",
    "FindingPage",
    "FailedRun",
    "IncompleteRun",
    "SucceededRun",
    "RunPage",
    "SessionGraph",
    "Session",
    "AnalysisAction",
    "AnalysisContract",
    "session",
]


def _install_telemetry() -> None:
    import sys
    from dataclasses import replace

    from marivo.analysis._capabilities.dataset_model import CallableInput
    from marivo.analysis._capabilities.registry import REGISTRY
    from marivo.telemetry import install_surface_instrumentation

    # Each native binding owns its exact implementation, including inherited methods.
    descriptors = tuple(
        replace(descriptor, bindings=(binding,))
        for descriptor in REGISTRY.descriptors
        if isinstance(descriptor, CallableInput) and descriptor.telemetry
        for binding in descriptor.bindings
    )
    install_surface_instrumentation(
        surface="analysis", descriptors=descriptors, root_module=sys.modules[__name__]
    )


_install_telemetry()
