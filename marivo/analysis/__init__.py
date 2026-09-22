"""Typed lazy analysis with explicit execution and committed Dataset reads."""

from datetime import date as _date
from datetime import datetime as _datetime
from types import ModuleType
from typing import TYPE_CHECKING, Literal

from marivo._temporal import Grain, TimeScope
from marivo._temporal import time_scope as _time_scope

if TYPE_CHECKING:
    from marivo.analysis import runtime_metric as runtime_metric
    from marivo.analysis import session as session
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
    from marivo.analysis.operators.contracts import WindowBucketAlignment as WindowBucketAlignment
    from marivo.analysis.operators.contracts import window_bucket as window_bucket
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
