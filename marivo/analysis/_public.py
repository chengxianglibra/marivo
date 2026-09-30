"""The single immutable Dataset analysis surface."""

import marivo.analysis.runtime_metric as runtime_metric
import marivo.analysis.session as session
from marivo._temporal import BeforeEndBoundary as BeforeEndBoundary
from marivo._temporal import Grain as Grain
from marivo._temporal import TimeScope as TimeScope
from marivo.analysis import grain as grain
from marivo.analysis import time_scope as time_scope
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
from marivo.analysis.public_dsl import LogicalRolledNumericRelation as LogicalRolledNumericRelation
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
from marivo.analysis.public_dsl import MaterializedBooleanRelation as MaterializedBooleanRelation
from marivo.analysis.public_dsl import MaterializedCategoryRelation as MaterializedCategoryRelation
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
from marivo.analysis.public_dsl import MaterializedNumericRelation as MaterializedNumericRelation
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
from marivo.analysis.public_dsl import MaterializedTemporalRelation as MaterializedTemporalRelation
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

__all__ = [  # noqa: RUF022 - accepted public export order is contractual
    "Dataset",
    "LogicalDataset",
    "MaterializedDataset",
    "DatasetShapeId",
    "DatasetFieldId",
    "DatasetFieldIdentity",
    "DatasetPhysicalTypeState",
    "DatasetField",
    "DatasetRowBound",
    "DatasetCardinality",
    "DatasetOrderTerm",
    "DatasetOrdering",
    "DatasetByteCount",
    "DatasetFamilyRowSemantics",
    "DatasetRowContract",
    "DatasetRowSetContract",
    "DatasetSchema",
    "LogicalDatasetState",
    "MaterializedDatasetState",
    "DatasetContract",
    "DatasetFields",
    "DatasetFieldRef",
    "LogicalPopulationDataset",
    "MaterializedPopulationDataset",
    "LogicalMetricDataset",
    "MaterializedMetricDataset",
    "LogicalDeltaDataset",
    "MaterializedDeltaDataset",
    "LogicalAttributionDataset",
    "MaterializedAttributionDataset",
    "LogicalAssociationDataset",
    "MaterializedAssociationDataset",
    "LogicalForecastDataset",
    "MaterializedForecastDataset",
    "LogicalCandidateDataset",
    "MaterializedCandidateDataset",
    "LogicalEventDataset",
    "MaterializedEventDataset",
    "LogicalLifecycleDataset",
    "MaterializedLifecycleDataset",
    "AnalysisPredicate",
    "ForecastHorizon",
    "ForecastModel",
    "WindowBucketAlignment",
    "BoundedCompletenessDeclarationV1",
    "SourceOriginCompletenessDeclarationV1",
    "DroppedBefore",
    "EventPattern",
    "EveryStart",
    "FirstPerSubject",
    "FromInception",
    "FunnelLossRate",
    "Grain",
    "InState",
    "PatternStep",
    "TimeScope",
    "BeforeEndBoundary",
    "TimeGrid",
    "GridWindow",
    "GridEndpoint",
    "time_grid",
    "LogicalTimeAnalysisDomain",
    "MaterializedTimeAnalysisDomain",
    "ArtifactDigest",
    "ArtifactRef",
    "ArtifactRevalidation",
    "ArtifactSummary",
    "EvidenceIntegrityError",
    "FailedRun",
    "Finding",
    "FindingPage",
    "IncompleteRun",
    "RunPage",
    "SessionGraph",
    "SucceededRun",
    "Session",
    "PeriodChange",
    "UnionKeys",
    "ExactKeys",
    "TimeChange",
    "CohortContrast",
    "OneToOneCorrespondence",
    "one_to_one",
    "AnalysisAction",
    "AnalysisContract",
    "LogicalAnalysisDomain",
    "MaterializedAnalysisDomain",
    "LogicalCategoryRelation",
    "LogicalBooleanRelation",
    "MaterializedBooleanRelation",
    "LogicalTemporalRelation",
    "MaterializedTemporalRelation",
    "LogicalSelectedBooleanRelation",
    "MaterializedSelectedBooleanRelation",
    "LogicalSelectedTemporalRelation",
    "MaterializedSelectedTemporalRelation",
    "LogicalSelectedNumericRelation",
    "MaterializedSelectedNumericRelation",
    "MaterializedCategoryRelation",
    "LogicalNumericRelation",
    "MaterializedNumericRelation",
    "MaterializedGroupedNumericRelation",
    "LogicalRolledNumericRelation",
    "MaterializedRolledNumericRelation",
    "LogicalRolledRatioRelation",
    "MaterializedRolledRatioRelation",
    "LogicalRatioRelation",
    "MaterializedRatioRelation",
    "LogicalDifferenceRelation",
    "MaterializedDifferenceRelation",
    "LogicalSelectedDifferenceRelation",
    "MaterializedSelectedDifferenceRelation",
    "LogicalStatisticRelation",
    "MaterializedStatisticRelation",
    "MaterializedCoefficientRelation",
    "LogicalCoefficientSelectionRelation",
    "MaterializedCoefficientSelectionRelation",
    "LogicalFixedAnalysisDomain",
    "LogicalSelectedCategoryRelation",
    "MaterializedSelectedCategoryRelation",
    "GroupedAnalysisDomain",
    "GroupedNumericRelation",
    "GroupedRatioRelation",
    "LogicalAssociationResult",
    "MaterializedAssociationResult",
    "RootRoute",
    "RootRoutes",
    "RowMethod",
    "CountMethod",
    "GroupedStatisticRelation",
    "route",
    "routes",
    "sum",
    "count",
    "mean",
    "min",
    "max",
    "count_defined",
    "eq",
    "not_eq",
    "lt",
    "lte",
    "gt",
    "gte",
    "is_in",
    "is_null",
    "is_not_null",
    "all_of",
    "any_of",
    "not_",
    "grain",
    "time_scope",
    "window_bucket",
    "step",
    "sequence",
    "first_per_subject",
    "every_start",
    "dropped_before",
    "in_state",
    "funnel_loss_rate",
    "from_inception",
    "periods",
    "naive",
    "drift",
    "seasonal_naive",
    "runtime_metric",
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
