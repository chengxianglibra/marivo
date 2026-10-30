"""Canonical AnalysisFrame construction, operations and metadata reading surface."""

import marivo.analysis.session as session
from marivo._definition.correspondence import CalendarCorrespondence as CalendarCorrespondence
from marivo._grouping import Grouping as Grouping
from marivo._temporal import Grain as Grain
from marivo._temporal import InstantAnchor as InstantAnchor
from marivo._temporal import TimeScope as TimeScope
from marivo.analysis import grain as grain
from marivo.analysis.core.frame_interpretation import (
    FrameAllHistoryWindow as FrameAllHistoryWindow,
)
from marivo.analysis.core.frame_interpretation import (
    FrameAxisGroup as FrameAxisGroup,
)
from marivo.analysis.core.frame_interpretation import (
    FrameAxisTime as FrameAxisTime,
)
from marivo.analysis.core.frame_interpretation import (
    FrameBuiltinGrain as FrameBuiltinGrain,
)
from marivo.analysis.core.frame_interpretation import (
    FrameCalendarAxisSchema as FrameCalendarAxisSchema,
)
from marivo.analysis.core.frame_interpretation import (
    FrameCalendarComparisonBinding as FrameCalendarComparisonBinding,
)
from marivo.analysis.core.frame_interpretation import (
    FrameCalendarEndpoint as FrameCalendarEndpoint,
)
from marivo.analysis.core.frame_interpretation import (
    FrameChangeTarget as FrameChangeTarget,
)
from marivo.analysis.core.frame_interpretation import (
    FrameComparedTime as FrameComparedTime,
)
from marivo.analysis.core.frame_interpretation import (
    FrameComparison as FrameComparison,
)
from marivo.analysis.core.frame_interpretation import (
    FrameComparisonGroup as FrameComparisonGroup,
)
from marivo.analysis.core.frame_interpretation import (
    FrameComparisonPresence as FrameComparisonPresence,
)
from marivo.analysis.core.frame_interpretation import (
    FrameComparisonValue as FrameComparisonValue,
)
from marivo.analysis.core.frame_interpretation import (
    FrameDecomposition as FrameDecomposition,
)
from marivo.analysis.core.frame_interpretation import (
    FrameDecompositionField as FrameDecompositionField,
)
from marivo.analysis.core.frame_interpretation import (
    FrameFixedAxis as FrameFixedAxis,
)
from marivo.analysis.core.frame_interpretation import (
    FrameFixedGroup as FrameFixedGroup,
)
from marivo.analysis.core.frame_interpretation import (
    FrameFixedTime as FrameFixedTime,
)
from marivo.analysis.core.frame_interpretation import (
    FrameIntervalAxisSchema as FrameIntervalAxisSchema,
)
from marivo.analysis.core.frame_interpretation import (
    FrameIntervalComparisonBinding as FrameIntervalComparisonBinding,
)
from marivo.analysis.core.frame_interpretation import (
    FrameIntervalEndpoint as FrameIntervalEndpoint,
)
from marivo.analysis.core.frame_interpretation import (
    FrameLevelTarget as FrameLevelTarget,
)
from marivo.analysis.core.frame_interpretation import (
    FrameNamedGrain as FrameNamedGrain,
)
from marivo.analysis.core.frame_interpretation import (
    FrameObservationContext as FrameObservationContext,
)
from marivo.analysis.core.frame_interpretation import (
    FrameOrdinaryAxisSchema as FrameOrdinaryAxisSchema,
)
from marivo.analysis.core.frame_interpretation import (
    FrameOrdinaryComparisonBinding as FrameOrdinaryComparisonBinding,
)
from marivo.analysis.core.frame_interpretation import (
    FramePairedAxisSchema as FramePairedAxisSchema,
)
from marivo.analysis.core.frame_interpretation import (
    FrameSinceWindow as FrameSinceWindow,
)
from marivo.analysis.core.frame_interpretation import (
    FrameStatisticContext as FrameStatisticContext,
)
from marivo.analysis.core.frame_interpretation import (
    FrameToDateWindow as FrameToDateWindow,
)
from marivo.analysis.core.frame_interpretation import (
    FrameTrailingDurationWindow as FrameTrailingDurationWindow,
)
from marivo.analysis.core.frame_interpretation import (
    FrameTrailingPeriodsWindow as FrameTrailingPeriodsWindow,
)
from marivo.analysis.core.frame_method_facts import FrameMethodFacts as FrameMethodFacts
from marivo.analysis.core.frame_page import FrameCalendarPeriod as FrameCalendarPeriod
from marivo.analysis.core.frame_page import FrameComparisonPeriod as FrameComparisonPeriod
from marivo.analysis.core.frame_page import FrameIntervalPeriod as FrameIntervalPeriod
from marivo.analysis.core.frame_page import FramePage as FramePage
from marivo.analysis.core.frame_page import FramePairedPeriod as FramePairedPeriod
from marivo.analysis.core.frame_page import FramePeriodPage as FramePeriodPage
from marivo.analysis.core.frame_page import FrameReadStats as FrameReadStats
from marivo.analysis.core.frame_schema import FrameAxisSchema as FrameAxisSchema
from marivo.analysis.core.frame_schema import FrameField as FrameField
from marivo.analysis.core.frame_schema import FrameSchema as FrameSchema
from marivo.analysis.core.frame_source_reads import (
    FrameSourceRead as FrameSourceRead,
)
from marivo.analysis.core.frame_source_reads import (
    FrameSourceReadPeriod as FrameSourceReadPeriod,
)
from marivo.analysis.core.frame_source_reads import (
    FrameSourceReadScope as FrameSourceReadScope,
)
from marivo.analysis.core.lineage import ColumnLineage as ColumnLineage
from marivo.analysis.core.lineage import FrameLineage as FrameLineage
from marivo.analysis.core.lineage import SemanticObjectIdentity as SemanticObjectIdentity
from marivo.analysis.dsl.baseline import ComparisonBaseline as ComparisonBaseline
from marivo.analysis.dsl.baseline import baseline as baseline
from marivo.analysis.dsl.column import Column as Column
from marivo.analysis.dsl.column import row_count as row_count
from marivo.analysis.dsl.correspondence import offset as offset
from marivo.analysis.dsl.correspondence import ordinal as ordinal
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
from marivo.analysis.session.core import Session as Session

__all__ = [  # noqa: RUF022 - public order is contractual
    "AnalysisFrame",
    "FrameAxis",
    "ComparisonBaseline",
    "baseline",
    "FrameMethodFacts",
    "FrameLineage",
    "ColumnLineage",
    "SemanticObjectIdentity",
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
    "offset",
    "grain",
    "Grain",
    "TimeScope",
    "InstantAnchor",
    "CalendarAxis",
    "all_history",
    "since",
    "to_date",
    "trailing_duration",
    "trailing_periods",
    "FrameSourceRead",
    "FrameSourceReadScope",
    "FrameSourceReadPeriod",
    "FrameSchema",
    "FrameField",
    "FrameAxisSchema",
    "FrameOrdinaryAxisSchema",
    "FrameCalendarAxisSchema",
    "FrameIntervalAxisSchema",
    "FramePairedAxisSchema",
    "FrameBuiltinGrain",
    "FrameNamedGrain",
    "FrameCalendarEndpoint",
    "FrameIntervalEndpoint",
    "FrameFixedAxis",
    "FrameAxisTime",
    "FrameComparedTime",
    "FrameFixedTime",
    "FrameAllHistoryWindow",
    "FrameSinceWindow",
    "FrameToDateWindow",
    "FrameTrailingDurationWindow",
    "FrameTrailingPeriodsWindow",
    "FrameObservationContext",
    "FrameAxisGroup",
    "FrameFixedGroup",
    "FrameStatisticContext",
    "FrameComparison",
    "FrameComparisonGroup",
    "FrameComparisonValue",
    "FrameComparisonPresence",
    "FrameOrdinaryComparisonBinding",
    "FrameCalendarComparisonBinding",
    "FrameIntervalComparisonBinding",
    "FrameLevelTarget",
    "FrameChangeTarget",
    "FrameDecomposition",
    "FrameDecompositionField",
    "FramePage",
    "FrameReadStats",
    "FramePeriodPage",
    "FrameCalendarPeriod",
    "FrameIntervalPeriod",
    "FramePairedPeriod",
    "FrameComparisonPeriod",
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
