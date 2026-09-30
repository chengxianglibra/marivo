"""Typed lazy analysis with explicit execution and committed Dataset reads."""

from datetime import date as _date
from datetime import datetime as _datetime
from types import ModuleType
from typing import TYPE_CHECKING, Literal

from marivo._temporal import BeforeEndBoundary as BeforeEndBoundary
from marivo._temporal import Grain, TimeScope
from marivo._temporal import time_scope as _time_scope

if TYPE_CHECKING:
    from marivo.analysis import runtime_metric as runtime_metric
    from marivo.analysis import session as session
    from marivo.analysis._comparison import CohortContrast as CohortContrast
    from marivo.analysis._comparison import ExactKeys as ExactKeys
    from marivo.analysis._comparison import PeriodChange as PeriodChange
    from marivo.analysis._comparison import TimeChange as TimeChange
    from marivo.analysis._comparison import UnionKeys as UnionKeys
    from marivo.analysis._comparison import WindowBucketAlignment as WindowBucketAlignment
    from marivo.analysis._comparison import window_bucket as window_bucket
    from marivo.analysis.datasets.base import Dataset as Dataset
    from marivo.analysis.datasets.base import LogicalDataset as LogicalDataset
    from marivo.analysis.datasets.base import MaterializedDataset as MaterializedDataset
    from marivo.analysis.datasets.contract import DatasetContract as DatasetContract
    from marivo.analysis.datasets.descriptors import DatasetByteCount as DatasetByteCount
    from marivo.analysis.datasets.descriptors import DatasetCardinality as DatasetCardinality
    from marivo.analysis.datasets.descriptors import (
        DatasetFamilyRowSemantics as DatasetFamilyRowSemantics,
    )
    from marivo.analysis.datasets.descriptors import DatasetField as DatasetField
    from marivo.analysis.datasets.descriptors import DatasetFieldId as DatasetFieldId
    from marivo.analysis.datasets.descriptors import DatasetFieldIdentity as DatasetFieldIdentity
    from marivo.analysis.datasets.descriptors import DatasetOrdering as DatasetOrdering
    from marivo.analysis.datasets.descriptors import DatasetOrderTerm as DatasetOrderTerm
    from marivo.analysis.datasets.descriptors import (
        DatasetPhysicalTypeState as DatasetPhysicalTypeState,
    )
    from marivo.analysis.datasets.descriptors import DatasetRowBound as DatasetRowBound
    from marivo.analysis.datasets.descriptors import DatasetRowContract as DatasetRowContract
    from marivo.analysis.datasets.descriptors import DatasetRowSetContract as DatasetRowSetContract
    from marivo.analysis.datasets.descriptors import DatasetSchema as DatasetSchema
    from marivo.analysis.datasets.descriptors import DatasetShapeId as DatasetShapeId
    from marivo.analysis.datasets.fields import DatasetFieldRef as DatasetFieldRef
    from marivo.analysis.datasets.fields import DatasetFields as DatasetFields
    from marivo.analysis.datasets.state import LogicalDatasetState as LogicalDatasetState
    from marivo.analysis.datasets.state import MaterializedDatasetState as MaterializedDatasetState
    from marivo.analysis.domains.completeness import (
        BoundedCompletenessDeclarationV1 as BoundedCompletenessDeclarationV1,
    )
    from marivo.analysis.domains.completeness import (
        SourceOriginCompletenessDeclarationV1 as SourceOriginCompletenessDeclarationV1,
    )
    from marivo.analysis.domains.event import LogicalEventDataset as LogicalEventDataset
    from marivo.analysis.domains.event import MaterializedEventDataset as MaterializedEventDataset
    from marivo.analysis.domains.lifecycle import LogicalLifecycleDataset as LogicalLifecycleDataset
    from marivo.analysis.domains.lifecycle import (
        MaterializedLifecycleDataset as MaterializedLifecycleDataset,
    )
    from marivo.analysis.domains.lifecycle_reducers import InState as InState
    from marivo.analysis.domains.lifecycle_reducers import in_state as in_state
    from marivo.analysis.errors import EvidenceIntegrityError as EvidenceIntegrityError
    from marivo.analysis.event import EventPattern as EventPattern
    from marivo.analysis.event import EveryStart as EveryStart
    from marivo.analysis.event import FirstPerSubject as FirstPerSubject
    from marivo.analysis.event import PatternStep as PatternStep
    from marivo.analysis.event import every_start as every_start
    from marivo.analysis.event import first_per_subject as first_per_subject
    from marivo.analysis.event import sequence as sequence
    from marivo.analysis.event import step as step
    from marivo.analysis.evidence._dataset_types import ArtifactDigest as ArtifactDigest
    from marivo.analysis.evidence._dataset_types import ArtifactRevalidation as ArtifactRevalidation
    from marivo.analysis.evidence._dataset_types import Finding as Finding
    from marivo.analysis.evidence._dataset_types import FindingPage as FindingPage
    from marivo.analysis.funnel import FunnelLossRate as FunnelLossRate
    from marivo.analysis.funnel import funnel_loss_rate as funnel_loss_rate
    from marivo.analysis.lifecycle import FromInception as FromInception
    from marivo.analysis.lifecycle import from_inception as from_inception
    from marivo.analysis.observation.metric import LogicalMetricDataset as LogicalMetricDataset
    from marivo.analysis.observation.metric import (
        MaterializedMetricDataset as MaterializedMetricDataset,
    )
    from marivo.analysis.observation.population import (
        LogicalPopulationDataset as LogicalPopulationDataset,
    )
    from marivo.analysis.observation.population import (
        MaterializedPopulationDataset as MaterializedPopulationDataset,
    )
    from marivo.analysis.observation.predicates import AnalysisPredicate as AnalysisPredicate
    from marivo.analysis.observation.predicates import all_of as all_of
    from marivo.analysis.observation.predicates import any_of as any_of
    from marivo.analysis.observation.predicates import eq as eq
    from marivo.analysis.observation.predicates import gt as gt
    from marivo.analysis.observation.predicates import gte as gte
    from marivo.analysis.observation.predicates import is_in as is_in
    from marivo.analysis.observation.predicates import is_not_null as is_not_null
    from marivo.analysis.observation.predicates import is_null as is_null
    from marivo.analysis.observation.predicates import lt as lt
    from marivo.analysis.observation.predicates import lte as lte
    from marivo.analysis.observation.predicates import not_ as not_
    from marivo.analysis.observation.predicates import not_eq as not_eq
    from marivo.analysis.operators.association import (
        LogicalAssociationDataset as LogicalAssociationDataset,
    )
    from marivo.analysis.operators.association import (
        MaterializedAssociationDataset as MaterializedAssociationDataset,
    )
    from marivo.analysis.operators.attribution import (
        LogicalAttributionDataset as LogicalAttributionDataset,
    )
    from marivo.analysis.operators.attribution import (
        MaterializedAttributionDataset as MaterializedAttributionDataset,
    )
    from marivo.analysis.operators.candidate_dataset import (
        LogicalCandidateDataset as LogicalCandidateDataset,
    )
    from marivo.analysis.operators.candidate_dataset import (
        MaterializedCandidateDataset as MaterializedCandidateDataset,
    )
    from marivo.analysis.operators.delta import LogicalDeltaDataset as LogicalDeltaDataset
    from marivo.analysis.operators.delta import MaterializedDeltaDataset as MaterializedDeltaDataset
    from marivo.analysis.operators.forecast_contracts import ForecastHorizon as ForecastHorizon
    from marivo.analysis.operators.forecast_contracts import ForecastModel as ForecastModel
    from marivo.analysis.operators.forecast_contracts import drift as drift
    from marivo.analysis.operators.forecast_contracts import naive as naive
    from marivo.analysis.operators.forecast_contracts import periods as periods
    from marivo.analysis.operators.forecast_contracts import seasonal_naive as seasonal_naive
    from marivo.analysis.operators.forecast_dataset import (
        LogicalForecastDataset as LogicalForecastDataset,
    )
    from marivo.analysis.operators.forecast_dataset import (
        MaterializedForecastDataset as MaterializedForecastDataset,
    )
    from marivo.analysis.public_dsl import AnalysisAction as AnalysisAction
    from marivo.analysis.public_dsl import AnalysisContract as AnalysisContract
    from marivo.analysis.public_dsl import CountMethod as CountMethod
    from marivo.analysis.public_dsl import GridEndpoint as GridEndpoint
    from marivo.analysis.public_dsl import GridWindow as GridWindow
    from marivo.analysis.public_dsl import GroupedAnalysisDomain as GroupedAnalysisDomain
    from marivo.analysis.public_dsl import GroupedNumericRelation as GroupedNumericRelation
    from marivo.analysis.public_dsl import GroupedRatioRelation as GroupedRatioRelation
    from marivo.analysis.public_dsl import GroupedStatisticRelation as GroupedStatisticRelation
    from marivo.analysis.public_dsl import LogicalAnalysisDomain as LogicalAnalysisDomain
    from marivo.analysis.public_dsl import LogicalAssociationResult as LogicalAssociationResult
    from marivo.analysis.public_dsl import LogicalBooleanRelation as LogicalBooleanRelation
    from marivo.analysis.public_dsl import LogicalCategoryRelation as LogicalCategoryRelation
    from marivo.analysis.public_dsl import (
        LogicalCoefficientSelectionRelation as LogicalCoefficientSelectionRelation,
    )
    from marivo.analysis.public_dsl import LogicalDifferenceRelation as LogicalDifferenceRelation
    from marivo.analysis.public_dsl import LogicalFixedAnalysisDomain as LogicalFixedAnalysisDomain
    from marivo.analysis.public_dsl import LogicalNumericRelation as LogicalNumericRelation
    from marivo.analysis.public_dsl import LogicalRatioRelation as LogicalRatioRelation
    from marivo.analysis.public_dsl import (
        LogicalRolledNumericRelation as LogicalRolledNumericRelation,
    )
    from marivo.analysis.public_dsl import LogicalRolledRatioRelation as LogicalRolledRatioRelation
    from marivo.analysis.public_dsl import (
        LogicalSelectedBooleanRelation as LogicalSelectedBooleanRelation,
    )
    from marivo.analysis.public_dsl import (
        LogicalSelectedCategoryRelation as LogicalSelectedCategoryRelation,
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
    from marivo.analysis.public_dsl import LogicalTemporalRelation as LogicalTemporalRelation
    from marivo.analysis.public_dsl import LogicalTimeAnalysisDomain as LogicalTimeAnalysisDomain
    from marivo.analysis.public_dsl import MaterializedAnalysisDomain as MaterializedAnalysisDomain
    from marivo.analysis.public_dsl import (
        MaterializedAssociationResult as MaterializedAssociationResult,
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
        MaterializedDifferenceRelation as MaterializedDifferenceRelation,
    )
    from marivo.analysis.public_dsl import (
        MaterializedGroupedNumericRelation as MaterializedGroupedNumericRelation,
    )
    from marivo.analysis.public_dsl import (
        MaterializedNumericRelation as MaterializedNumericRelation,
    )
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
        MaterializedSelectedCategoryRelation as MaterializedSelectedCategoryRelation,
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
    from marivo.analysis.public_dsl import (
        MaterializedTemporalRelation as MaterializedTemporalRelation,
    )
    from marivo.analysis.public_dsl import (
        MaterializedTimeAnalysisDomain as MaterializedTimeAnalysisDomain,
    )
    from marivo.analysis.public_dsl import OneToOneCorrespondence as OneToOneCorrespondence
    from marivo.analysis.public_dsl import RootRoute as RootRoute
    from marivo.analysis.public_dsl import RootRoutes as RootRoutes
    from marivo.analysis.public_dsl import RowMethod as RowMethod
    from marivo.analysis.public_dsl import TimeGrid as TimeGrid
    from marivo.analysis.public_dsl import count as count
    from marivo.analysis.public_dsl import count_defined as count_defined
    from marivo.analysis.public_dsl import max as max
    from marivo.analysis.public_dsl import mean as mean
    from marivo.analysis.public_dsl import min as min
    from marivo.analysis.public_dsl import one_to_one as one_to_one
    from marivo.analysis.public_dsl import route as route
    from marivo.analysis.public_dsl import routes as routes
    from marivo.analysis.public_dsl import sum as sum
    from marivo.analysis.public_dsl import time_grid as time_grid
    from marivo.analysis.refs import ArtifactRef as ArtifactRef
    from marivo.analysis.session._lazy_read_model import ArtifactSummary as ArtifactSummary
    from marivo.analysis.session._lazy_read_model import FailedRun as FailedRun
    from marivo.analysis.session._lazy_read_model import IncompleteRun as IncompleteRun
    from marivo.analysis.session._lazy_read_model import RunPage as RunPage
    from marivo.analysis.session._lazy_read_model import SessionGraph as SessionGraph
    from marivo.analysis.session._lazy_read_model import SucceededRun as SucceededRun
    from marivo.analysis.session.core import Session as Session
    from marivo.analysis.subject import DroppedBefore as DroppedBefore
    from marivo.analysis.subject import dropped_before as dropped_before


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
