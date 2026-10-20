"""Canonical AnalysisFrame construction, operations and audit reading surface."""

import marivo.analysis.session as session
from marivo._definition.correspondence import CalendarCorrespondence as CalendarCorrespondence
from marivo._grouping import Grouping as Grouping
from marivo._temporal import BeforeEndBoundary as BeforeEndBoundary
from marivo._temporal import Grain as Grain
from marivo._temporal import InstantAnchor as InstantAnchor
from marivo._temporal import TimeScope as TimeScope
from marivo.analysis import grain as grain
from marivo.analysis.core.frame_method_facts import FrameAdditiveMethod as FrameAdditiveMethod
from marivo.analysis.core.frame_method_facts import FrameLinearMethod as FrameLinearMethod
from marivo.analysis.core.frame_method_facts import FrameLinearTerm as FrameLinearTerm
from marivo.analysis.core.frame_method_facts import FrameMethodCoordinate as FrameMethodCoordinate
from marivo.analysis.core.frame_method_facts import FrameMethodFacts as FrameMethodFacts
from marivo.analysis.core.frame_method_facts import FrameMethodMetric as FrameMethodMetric
from marivo.analysis.core.frame_method_facts import FrameMethodParent as FrameMethodParent
from marivo.analysis.core.frame_method_facts import FrameMethodSupport as FrameMethodSupport
from marivo.analysis.core.frame_method_facts import FrameMethodValue as FrameMethodValue
from marivo.analysis.core.frame_method_facts import FrameRatioMethod as FrameRatioMethod
from marivo.analysis.dsl.artifact import Artifact as Artifact
from marivo.analysis.dsl.attribute import AttributeEndpoint as AttributeEndpoint
from marivo.analysis.dsl.column import Column as Column
from marivo.analysis.dsl.column import row_count as row_count
from marivo.analysis.dsl.correspondence import ordinal as ordinal
from marivo.analysis.dsl.correspondence import period_offset as period_offset
from marivo.analysis.dsl.correspondence import previous_year as previous_year
from marivo.analysis.dsl.frame import AnalysisFrame as AnalysisFrame
from marivo.analysis.dsl.frame_axis import FrameAxis as FrameAxis
from marivo.analysis.dsl.population import Population as Population
from marivo.analysis.dsl.population import coordinates as coordinates
from marivo.analysis.dsl.population import identity as identity
from marivo.analysis.dsl.population import in_population as in_population
from marivo.analysis.dsl.population import membership as membership
from marivo.analysis.dsl.population import product as product
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
from marivo.analysis.session._lazy_read_model import FailedRun as FailedRun
from marivo.analysis.session._lazy_read_model import IncompleteRun as IncompleteRun
from marivo.analysis.session._lazy_read_model import RunPage as RunPage
from marivo.analysis.session._lazy_read_model import SucceededRun as SucceededRun
from marivo.analysis.session.core import Session as Session

__all__ = [  # noqa: RUF022 - public order is contractual
    "AnalysisFrame",
    "FrameAxis",
    "AttributeEndpoint",
    "FrameMethodValue",
    "FrameMethodCoordinate",
    "FrameMethodSupport",
    "FrameAdditiveMethod",
    "FrameRatioMethod",
    "FrameLinearTerm",
    "FrameLinearMethod",
    "FrameMethodMetric",
    "FrameMethodParent",
    "FrameMethodFacts",
    "Column",
    "Grouping",
    "identity",
    "Population",
    "CalendarCorrespondence",
    "coordinates",
    "membership",
    "product",
    "in_population",
    "row_count",
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
    "Finding",
    "FindingPage",
    "FailedRun",
    "IncompleteRun",
    "SucceededRun",
    "RunPage",
    "Session",
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
