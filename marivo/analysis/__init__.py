"""Typed lazy analysis with explicit execution and committed Dataset reads."""

from datetime import date as _date
from datetime import datetime as _datetime
from types import ModuleType
from typing import TYPE_CHECKING, Literal

from marivo._definition.windows import all_history as all_history
from marivo._definition.windows import since as since
from marivo._definition.windows import to_date as to_date
from marivo._definition.windows import trailing_duration as trailing_duration
from marivo._definition.windows import trailing_periods as trailing_periods
from marivo._temporal import BeforeEndBoundary as BeforeEndBoundary
from marivo._temporal import Grain, TimeScope
from marivo._temporal import time_scope as _time_scope

if TYPE_CHECKING:
    from marivo._temporal import InstantAnchor as InstantAnchor
    from marivo.analysis import runtime_metric as runtime_metric
    from marivo.analysis import session as session
    from marivo.analysis._cohort import AllInstances as AllInstances
    from marivo.analysis._cohort import AnyInstance as AnyInstance
    from marivo.analysis._cohort import AtLeast as AtLeast
    from marivo.analysis._cohort import EmptyOpportunityPolicy as EmptyOpportunityPolicy
    from marivo.analysis._cohort import all_instances as all_instances
    from marivo.analysis._cohort import any_instance as any_instance
    from marivo.analysis._cohort import at_least as at_least
    from marivo.analysis._cohort import empty_opportunity as empty_opportunity
    from marivo.analysis._comparison import ExactAlignment as ExactAlignment
    from marivo.analysis._comparison import HolidayThenWeekdayTimeKey as HolidayThenWeekdayTimeKey
    from marivo.analysis._comparison import HolidayTimeKey as HolidayTimeKey
    from marivo.analysis._comparison import OrdinalTimeKey as OrdinalTimeKey
    from marivo.analysis._comparison import OuterAlignment as OuterAlignment
    from marivo.analysis._comparison import WeekdayTimeKey as WeekdayTimeKey
    from marivo.analysis._comparison import exact as exact
    from marivo.analysis._comparison import holiday as holiday
    from marivo.analysis._comparison import holiday_then_weekday as holiday_then_weekday
    from marivo.analysis._comparison import ordinal as ordinal
    from marivo.analysis._comparison import outer as outer
    from marivo.analysis._comparison import weekday as weekday
    from marivo.analysis._subject import SubjectBinding as SubjectBinding
    from marivo.analysis.datasets.descriptors import DatasetByteCount as DatasetByteCount
    from marivo.analysis.datasets.state import MaterializedDatasetState as MaterializedDatasetState
    from marivo.analysis.dsl.artifact import Artifact as Artifact
    from marivo.analysis.dsl.population import (
        MembershipAxis as MembershipAxis,
    )
    from marivo.analysis.dsl.population import (
        Population as Population,
    )
    from marivo.analysis.dsl.population import (
        PopulationPredicate as PopulationPredicate,
    )
    from marivo.analysis.dsl.population import (
        coordinates as coordinates,
    )
    from marivo.analysis.dsl.population import (
        identities as identities,
    )
    from marivo.analysis.dsl.population import (
        in_population as in_population,
    )
    from marivo.analysis.dsl.population import (
        membership as membership,
    )
    from marivo.analysis.dsl.population import (
        product as product,
    )
    from marivo.analysis.dsl.query import Query as Query
    from marivo.analysis.dsl.temporal import CalendarAxis as CalendarAxis
    from marivo.analysis.evidence._dataset_types import ArtifactDigest as ArtifactDigest
    from marivo.analysis.evidence._dataset_types import Finding as Finding
    from marivo.analysis.evidence._dataset_types import FindingPage as FindingPage
    from marivo.analysis.forecast_models import ForecastHorizon as ForecastHorizon
    from marivo.analysis.forecast_models import ForecastModel as ForecastModel
    from marivo.analysis.forecast_models import drift as drift
    from marivo.analysis.forecast_models import naive as naive
    from marivo.analysis.forecast_models import periods as periods
    from marivo.analysis.forecast_models import seasonal_naive as seasonal_naive
    from marivo.analysis.materialization.graph_fields import all_of as all_of
    from marivo.analysis.materialization.graph_fields import any_of as any_of
    from marivo.analysis.materialization.graph_fields import not_ as not_
    from marivo.analysis.public_dsl import AnalysisAction as AnalysisAction
    from marivo.analysis.public_dsl import AnalysisContract as AnalysisContract
    from marivo.analysis.public_dsl import CountMethod as CountMethod
    from marivo.analysis.public_dsl import GridEndpoint as GridEndpoint
    from marivo.analysis.public_dsl import GroupedNumericRelation as GroupedNumericRelation
    from marivo.analysis.public_dsl import GroupedRatioRelation as GroupedRatioRelation
    from marivo.analysis.public_dsl import GroupedStatisticRelation as GroupedStatisticRelation
    from marivo.analysis.public_dsl import LogicalAnalysisDomain as LogicalAnalysisDomain
    from marivo.analysis.public_dsl import LogicalAssociationResult as LogicalAssociationResult
    from marivo.analysis.public_dsl import LogicalAttributionResult as LogicalAttributionResult
    from marivo.analysis.public_dsl import LogicalBooleanRelation as LogicalBooleanRelation
    from marivo.analysis.public_dsl import LogicalCategoryRelation as LogicalCategoryRelation
    from marivo.analysis.public_dsl import LogicalCoefficientRelation as LogicalCoefficientRelation
    from marivo.analysis.public_dsl import (
        LogicalCoefficientSelectionRelation as LogicalCoefficientSelectionRelation,
    )
    from marivo.analysis.public_dsl import LogicalComparisonResult as LogicalComparisonResult
    from marivo.analysis.public_dsl import LogicalDeviationResult as LogicalDeviationResult
    from marivo.analysis.public_dsl import LogicalDifferenceRelation as LogicalDifferenceRelation
    from marivo.analysis.public_dsl import LogicalForecastResult as LogicalForecastResult
    from marivo.analysis.public_dsl import LogicalNumericRelation as LogicalNumericRelation
    from marivo.analysis.public_dsl import LogicalRankingResult as LogicalRankingResult
    from marivo.analysis.public_dsl import LogicalRatioRelation as LogicalRatioRelation
    from marivo.analysis.public_dsl import (
        LogicalRolledNumericRelation as LogicalRolledNumericRelation,
    )
    from marivo.analysis.public_dsl import LogicalRolledRatioRelation as LogicalRolledRatioRelation
    from marivo.analysis.public_dsl import (
        LogicalSelectedBooleanRelation as LogicalSelectedBooleanRelation,
    )
    from marivo.analysis.public_dsl import (
        LogicalSelectedDifferenceRelation as LogicalSelectedDifferenceRelation,
    )
    from marivo.analysis.public_dsl import (
        LogicalSelectedNumericRelation as LogicalSelectedNumericRelation,
    )
    from marivo.analysis.public_dsl import (
        LogicalSelectedTemporalRelation as LogicalSelectedTemporalRelation,
    )
    from marivo.analysis.public_dsl import LogicalStatisticRelation as LogicalStatisticRelation
    from marivo.analysis.public_dsl import LogicalTable as LogicalTable
    from marivo.analysis.public_dsl import LogicalTemporalRelation as LogicalTemporalRelation
    from marivo.analysis.public_dsl import LogicalTimeRunResult as LogicalTimeRunResult
    from marivo.analysis.public_dsl import MaterializedAnalysisDomain as MaterializedAnalysisDomain
    from marivo.analysis.public_dsl import (
        MaterializedAssociationResult as MaterializedAssociationResult,
    )
    from marivo.analysis.public_dsl import (
        MaterializedAttributionResult as MaterializedAttributionResult,
    )
    from marivo.analysis.public_dsl import (
        MaterializedBooleanRelation as MaterializedBooleanRelation,
    )
    from marivo.analysis.public_dsl import (
        MaterializedCategoryRelation as MaterializedCategoryRelation,
    )
    from marivo.analysis.public_dsl import (
        MaterializedCoefficientRelation as MaterializedCoefficientRelation,
    )
    from marivo.analysis.public_dsl import (
        MaterializedCoefficientSelectionRelation as MaterializedCoefficientSelectionRelation,
    )
    from marivo.analysis.public_dsl import (
        MaterializedComparisonResult as MaterializedComparisonResult,
    )
    from marivo.analysis.public_dsl import (
        MaterializedDeviationResult as MaterializedDeviationResult,
    )
    from marivo.analysis.public_dsl import (
        MaterializedDifferenceRelation as MaterializedDifferenceRelation,
    )
    from marivo.analysis.public_dsl import MaterializedForecastResult as MaterializedForecastResult
    from marivo.analysis.public_dsl import (
        MaterializedGroupedNumericRelation as MaterializedGroupedNumericRelation,
    )
    from marivo.analysis.public_dsl import (
        MaterializedNumericRelation as MaterializedNumericRelation,
    )
    from marivo.analysis.public_dsl import MaterializedRankingResult as MaterializedRankingResult
    from marivo.analysis.public_dsl import MaterializedRatioRelation as MaterializedRatioRelation
    from marivo.analysis.public_dsl import (
        MaterializedRolledNumericRelation as MaterializedRolledNumericRelation,
    )
    from marivo.analysis.public_dsl import (
        MaterializedRolledRatioRelation as MaterializedRolledRatioRelation,
    )
    from marivo.analysis.public_dsl import (
        MaterializedSelectedBooleanRelation as MaterializedSelectedBooleanRelation,
    )
    from marivo.analysis.public_dsl import (
        MaterializedSelectedDifferenceRelation as MaterializedSelectedDifferenceRelation,
    )
    from marivo.analysis.public_dsl import (
        MaterializedSelectedNumericRelation as MaterializedSelectedNumericRelation,
    )
    from marivo.analysis.public_dsl import (
        MaterializedSelectedTemporalRelation as MaterializedSelectedTemporalRelation,
    )
    from marivo.analysis.public_dsl import (
        MaterializedStatisticRelation as MaterializedStatisticRelation,
    )
    from marivo.analysis.public_dsl import MaterializedTable as MaterializedTable
    from marivo.analysis.public_dsl import (
        MaterializedTemporalRelation as MaterializedTemporalRelation,
    )
    from marivo.analysis.public_dsl import MaterializedTimeRunResult as MaterializedTimeRunResult
    from marivo.analysis.public_dsl import MemberAxis as MemberAxis
    from marivo.analysis.public_dsl import OneToOneCorrespondence as OneToOneCorrespondence
    from marivo.analysis.public_dsl import ReferenceWeights as ReferenceWeights
    from marivo.analysis.public_dsl import RelationshipPath as RelationshipPath
    from marivo.analysis.public_dsl import RowMethod as RowMethod
    from marivo.analysis.public_dsl import TimeGrid as TimeGrid
    from marivo.analysis.public_dsl import count as count
    from marivo.analysis.public_dsl import count_defined as count_defined
    from marivo.analysis.public_dsl import max as max
    from marivo.analysis.public_dsl import mean as mean
    from marivo.analysis.public_dsl import member as member
    from marivo.analysis.public_dsl import min as min
    from marivo.analysis.public_dsl import one_to_one as one_to_one
    from marivo.analysis.public_dsl import path as path
    from marivo.analysis.public_dsl import reference_weights as reference_weights
    from marivo.analysis.public_dsl import sum as sum
    from marivo.analysis.public_dsl import table as table
    from marivo.analysis.public_dsl import time_grid as time_grid
    from marivo.analysis.refs import ArtifactRef as ArtifactRef
    from marivo.analysis.session._lazy_read_model import ArtifactSummary as ArtifactSummary
    from marivo.analysis.session._lazy_read_model import FailedRun as FailedRun
    from marivo.analysis.session._lazy_read_model import IncompleteRun as IncompleteRun
    from marivo.analysis.session._lazy_read_model import RunPage as RunPage
    from marivo.analysis.session._lazy_read_model import SessionGraph as SessionGraph
    from marivo.analysis.session._lazy_read_model import SucceededRun as SucceededRun
    from marivo.analysis.session.core import Session as Session


if not TYPE_CHECKING:

    def __getattr__(name: str) -> object:
        from importlib import import_module
        from importlib.util import find_spec

        if name == "help":
            raise AttributeError('Use marivo.help("analysis") for the public analysis contract.')
        if name == "catalog":
            raise AttributeError(
                "The analysis catalog is session-bound; acquire session = mv.session.get_or_create(name), then use catalog = session.catalog."
            )
        if name.startswith("__") and name not in ("__all__", "__marivo_telemetry_capabilities__"):
            raise AttributeError(name)
        if not name.startswith("_") and find_spec(f"marivo.analysis.{name}") is not None:
            return import_module(f"marivo.analysis.{name}")
        return getattr(_initialize_public(), name)


def _initialize_public() -> ModuleType:
    from importlib import import_module

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

    Args:
        start: Inclusive ISO date or timestamp, or a date/datetime value.
        end: Exclusive endpoint of the same temporal kind.

    Returns:
        An immutable absolute TimeScope.

    Example:
        >>> import marivo.analysis as mv
        >>> window = mv.time_scope(start="2026-01-01", end="2026-02-01")

    Constraints:
        End must follow start. Calendar periods come from certified catalog lookups.
    """

    return _time_scope(start=start, end=end)
