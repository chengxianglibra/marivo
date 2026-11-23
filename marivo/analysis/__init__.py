"""Immutable analysis data with explicit computation and persistence."""

from importlib import import_module
from types import ModuleType
from typing import TYPE_CHECKING, Literal

from marivo._definition.windows import all_history as all_history
from marivo._definition.windows import since as since
from marivo._definition.windows import to_date as to_date
from marivo._definition.windows import trailing_duration as trailing_duration
from marivo._definition.windows import trailing_periods as trailing_periods
from marivo._kernel.time_types import Grain as Grain
from marivo._kernel.time_types import builtin_grain

if TYPE_CHECKING:
    from marivo._kernel.temporal import TimeScope as TimeScope
    from marivo.analysis.materialization.carriers.dataframe_result import (
        DataFrameResult as DataFrameResult,
    )
    from marivo.analysis.model.result_reference import DataFrameReference as DataFrameReference
    from marivo.analysis.model.result_reference import FrameReference as FrameReference
    from marivo.analysis.model.time_facts import (
        FrameClockedContribution as FrameClockedContribution,
    )
    from marivo.analysis.model.time_facts import (
        FrameContributionSampleWindowEndpoint as FrameContributionSampleWindowEndpoint,
    )
    from marivo.analysis.model.time_facts import (
        FrameDerivedTimeSemantics as FrameDerivedTimeSemantics,
    )
    from marivo.analysis.model.time_facts import (
        FrameFieldSampleWindowEndpoint as FrameFieldSampleWindowEndpoint,
    )
    from marivo.analysis.model.time_facts import (
        FrameIndependentContribution as FrameIndependentContribution,
    )
    from marivo.analysis.model.time_facts import (
        FrameObservedTimeSemantics as FrameObservedTimeSemantics,
    )
    from marivo.analysis.model.time_facts import (
        FrameReportWindowEndpoint as FrameReportWindowEndpoint,
    )
    from marivo.analysis.results import persist_dataframe as persist_dataframe
    from marivo.analysis.results import read_dataframe as read_dataframe

    __all__: list[str]
    from marivo._definition.correspondence import CalendarCorrespondence as CalendarCorrespondence
    from marivo._kernel.grouping import Grouping as Grouping
    from marivo._kernel.temporal import InstantAnchor as InstantAnchor
    from marivo.analysis import session as session
    from marivo.analysis.core.frame_method_facts import FrameMethodFacts as FrameMethodFacts
    from marivo.analysis.dsl.column import Column as Column
    from marivo.analysis.dsl.column import row_count as row_count
    from marivo.analysis.dsl.correspondence import offset as offset
    from marivo.analysis.dsl.correspondence import ordinal as ordinal
    from marivo.analysis.dsl.frame import AnalysisFrame as AnalysisFrame
    from marivo.analysis.dsl.frame import ComparisonBaseline as ComparisonBaseline
    from marivo.analysis.dsl.frame import baseline as baseline
    from marivo.analysis.dsl.frame_axis import FrameAxis as FrameAxis
    from marivo.analysis.dsl.population import Population as Population
    from marivo.analysis.dsl.population import coordinates as coordinates
    from marivo.analysis.dsl.population import identity as identity
    from marivo.analysis.dsl.population import in_population as in_population
    from marivo.analysis.dsl.population import membership as membership
    from marivo.analysis.dsl.population import product as product
    from marivo.analysis.dsl.temporal import CalendarAxis as CalendarAxis
    from marivo.analysis.materialization.carriers.frame_page import (
        FrameCalendarPeriod as FrameCalendarPeriod,
    )
    from marivo.analysis.materialization.carriers.frame_page import (
        FrameComparisonPeriod as FrameComparisonPeriod,
    )
    from marivo.analysis.materialization.carriers.frame_page import (
        FrameIntervalPeriod as FrameIntervalPeriod,
    )
    from marivo.analysis.materialization.carriers.frame_page import FramePage as FramePage
    from marivo.analysis.materialization.carriers.frame_page import (
        FramePairedPeriod as FramePairedPeriod,
    )
    from marivo.analysis.materialization.carriers.frame_page import (
        FramePeriodPage as FramePeriodPage,
    )
    from marivo.analysis.materialization.carriers.frame_page import FrameReadStats as FrameReadStats
    from marivo.analysis.model.frame_schema import FrameAxisSchema as FrameAxisSchema
    from marivo.analysis.model.frame_schema import FrameField as FrameField
    from marivo.analysis.model.frame_schema import FrameSchema as FrameSchema
    from marivo.analysis.model.frame_source_reads import (
        FrameSourceRead as FrameSourceRead,
    )
    from marivo.analysis.model.frame_source_reads import (
        FrameSourceReadPeriod as FrameSourceReadPeriod,
    )
    from marivo.analysis.model.frame_source_reads import (
        FrameSourceReadScope as FrameSourceReadScope,
    )
    from marivo.analysis.model.layout_facts import FrameAlignment as FrameAlignment
    from marivo.analysis.model.layout_facts import FrameAlignmentAxis as FrameAlignmentAxis
    from marivo.analysis.model.layout_facts import FrameAlignmentSide as FrameAlignmentSide
    from marivo.analysis.model.layout_facts import FrameAxisGroup as FrameAxisGroup
    from marivo.analysis.model.layout_facts import (
        FrameCalendarComparisonBinding as FrameCalendarComparisonBinding,
    )
    from marivo.analysis.model.layout_facts import FrameChangeTarget as FrameChangeTarget
    from marivo.analysis.model.layout_facts import FrameComparison as FrameComparison
    from marivo.analysis.model.layout_facts import FrameComparisonGroup as FrameComparisonGroup
    from marivo.analysis.model.layout_facts import (
        FrameComparisonPresence as FrameComparisonPresence,
    )
    from marivo.analysis.model.layout_facts import FrameComparisonValue as FrameComparisonValue
    from marivo.analysis.model.layout_facts import FrameDecomposition as FrameDecomposition
    from marivo.analysis.model.layout_facts import (
        FrameDecompositionField as FrameDecompositionField,
    )
    from marivo.analysis.model.layout_facts import FrameDerivedValueInput as FrameDerivedValueInput
    from marivo.analysis.model.layout_facts import FrameFixedGroup as FrameFixedGroup
    from marivo.analysis.model.layout_facts import (
        FrameIntervalComparisonBinding as FrameIntervalComparisonBinding,
    )
    from marivo.analysis.model.layout_facts import FrameLevelTarget as FrameLevelTarget
    from marivo.analysis.model.layout_facts import FrameNamedValueInput as FrameNamedValueInput
    from marivo.analysis.model.layout_facts import (
        FrameOrdinaryComparisonBinding as FrameOrdinaryComparisonBinding,
    )
    from marivo.analysis.model.layout_facts import (
        FramePairStatisticContext as FramePairStatisticContext,
    )
    from marivo.analysis.model.layout_facts import FrameShareContext as FrameShareContext
    from marivo.analysis.model.layout_facts import FrameStatisticContext as FrameStatisticContext
    from marivo.analysis.model.lineage import ColumnLineage as ColumnLineage
    from marivo.analysis.model.lineage import FrameLineage as FrameLineage
    from marivo.analysis.model.lineage import SemanticObjectIdentity as SemanticObjectIdentity
    from marivo.analysis.model.time_facts import FrameAllHistoryWindow as FrameAllHistoryWindow
    from marivo.analysis.model.time_facts import FrameAxisTime as FrameAxisTime
    from marivo.analysis.model.time_facts import FrameBuiltinGrain as FrameBuiltinGrain
    from marivo.analysis.model.time_facts import FrameCalendarAxisSchema as FrameCalendarAxisSchema
    from marivo.analysis.model.time_facts import FrameCalendarEndpoint as FrameCalendarEndpoint
    from marivo.analysis.model.time_facts import (
        FrameCivilDateSourceTime as FrameCivilDateSourceTime,
    )
    from marivo.analysis.model.time_facts import FrameComparedTime as FrameComparedTime
    from marivo.analysis.model.time_facts import FrameFixedAxis as FrameFixedAxis
    from marivo.analysis.model.time_facts import FrameFixedTime as FrameFixedTime
    from marivo.analysis.model.time_facts import FrameInstantSourceTime as FrameInstantSourceTime
    from marivo.analysis.model.time_facts import FrameIntervalAxisSchema as FrameIntervalAxisSchema
    from marivo.analysis.model.time_facts import FrameIntervalEndpoint as FrameIntervalEndpoint
    from marivo.analysis.model.time_facts import FrameObservationContext as FrameObservationContext
    from marivo.analysis.model.time_facts import FrameOrdinaryAxisSchema as FrameOrdinaryAxisSchema
    from marivo.analysis.model.time_facts import FramePairedAxisSchema as FramePairedAxisSchema
    from marivo.analysis.model.time_facts import FrameRowAttributeTime as FrameRowAttributeTime
    from marivo.analysis.model.time_facts import FrameSinceWindow as FrameSinceWindow
    from marivo.analysis.model.time_facts import FrameToDateWindow as FrameToDateWindow
    from marivo.analysis.model.time_facts import (
        FrameTrailingDurationWindow as FrameTrailingDurationWindow,
    )
    from marivo.analysis.model.time_facts import (
        FrameTrailingPeriodsWindow as FrameTrailingPeriodsWindow,
    )
    from marivo.analysis.model.time_facts import (
        FrameWallClockSourceTime as FrameWallClockSourceTime,
    )
    from marivo.analysis.session.core import Session as Session
    from marivo.semantic.spi import Path as Path
    from marivo.semantic.spi import RelationshipStep as RelationshipStep
    from marivo.semantic.spi import path as path


if not TYPE_CHECKING:

    def __getattr__(name: str) -> object:
        if name == "help":
            raise AttributeError('Use marivo.help("analysis") for the public analysis contract.')
        if name == "catalog":
            raise AttributeError(
                "The analysis catalog is session-bound; acquire session = mv.session.get_or_create(name), then use catalog = session.catalog."
            )
        if name.startswith("__") and name != "__all__":
            raise AttributeError(name)
        if name == "model":
            # A from-list import can resolve the pure package directly.
            raise AttributeError(name)
        if name == "TimeScope":
            from marivo._kernel.temporal import TimeScope  # noqa: PLC0415  # Keep the pure model import free of Pydantic.

            return TimeScope
        return getattr(_initialize_public(), name)


def _initialize_public() -> ModuleType:

    return import_module("marivo.analysis._public")


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
        count: Positive width; units day through year require one.

    Returns:
        The immutable public Grain value.

    Example:
        >>> import marivo.analysis as mv
        >>> mv.grain("month")

    Constraints:
        Subday widths must tile a nominal day and remain shorter than one day.
        Use one spelling: 60second, 120second and 60minute reject in favor of
        larger units; 24hour rejects. 90second and 90minute remain legal.
        Grains have unit/count equality and hashing, with no ordering.
    """
    return builtin_grain(unit, count=count)
