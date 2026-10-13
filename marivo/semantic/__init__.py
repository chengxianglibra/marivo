"""marivo.semantic - Python-native semantic layer (v1.1).

Public surface::

    import marivo.datasource as md
    import marivo.semantic as ms

    catalog = ms.load()                # returns SemanticCatalog
    catalog = ms.load(domains=['sales'])  # filter to specific domains
    catalog.domains.show()
    catalog.metrics.show()                                  # all metrics across domains

    ms.domain(name="sales", owner="Mina Zhang", default=True)
    warehouse = md.duckdb("warehouse").ref
    orders = ms.entity(name="orders", datasource=warehouse, source=md.table("orders"))
    amount = ms.measure_column(
        name="amount", entity=orders, column="amount",
        additivity=ms.additive_all(), unit="USD",
    )

    revenue = ms.aggregate(name="revenue", measure=amount, agg="sum")
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from marivo.refs import (
    PeriodCalendarKind,
    Ref,
    SemanticKind,
    TemporalSetKind,
    WorkScheduleKind,
    ref,
)
from marivo.semantic import errors as errors
from marivo.semantic import typing as typing
from marivo.semantic.authoring import (
    GrainToDate,
    PeriodCorrespondence,
    additive,
    additive_all,
    aggregate,
    ai_context,
    bind,
    calendar_grain,
    count,
    cumulative,
    datetime,
    dimension,
    dimension_column,
    domain,
    empty,
    entity,
    grain_to_date,
    hour_prefix,
    join_on,
    linear,
    measure,
    measure_column,
    metric,
    non_additive,
    nulls,
    period_calendar,
    period_correspondence,
    ratio,
    relationship,
    snapshot,
    strptime,
    temporal_set,
    time_dimension,
    time_dimension_column,
    timestamp,
    trailing,
    validity,
    weighted_mean,
    where,
    work_schedule,
    zero_denominator,
)
from marivo.semantic.catalog import (
    CalendarLevelDetails,
    CalendarPeriodPage,
    CatalogCollection,
    CatalogEntry,
    DatasourceDetails,
    DatasourceEntry,
    DerivedMetricDetails,
    DimensionDetails,
    DimensionEntry,
    DomainDetails,
    DomainEntry,
    EntityDetails,
    EntityEntry,
    MeasureDetails,
    MeasureEntry,
    MetricDetails,
    MetricEntry,
    PeriodCalendarDetails,
    PeriodCalendarEntry,
    RelationshipDetails,
    RelationshipEntry,
    SemanticCatalog,
    SimpleMetricDetails,
    TemporalOccurrencePage,
    TemporalSetDetails,
    TemporalSetEntry,
    TimeDimensionDetails,
    TimeDimensionEntry,
    WorkScheduleDetails,
    WorkScheduleEntry,
    load,
)
from marivo.semantic.definition import SemanticDefinition
from marivo.semantic.dtos import PreviewBatchResult
from marivo.semantic.errors import SemanticDefinitionReadError
from marivo.semantic.ir import (
    AggregateFoldInput,
    AggregateFoldValue,
    JoinKey,
)
from marivo.semantic.readiness import (
    ReadinessInputSummary,
    ReadinessIssue,
    ReadinessReport,
)
from marivo.semantic.richness import RichnessReport
from marivo.semantic.source_health import (
    SourceCheck,
    SourceHealthCheckResult,
    SourceHealthReport,
    source_check,
)
from marivo.semantic.typing import AiContextValue

if TYPE_CHECKING:
    from marivo.semantic.richness import DemandSignal


def richness(
    *,
    demand: DemandSignal | None = None,
) -> RichnessReport:
    """Return a demand-ranked advisory richness report.

    Pure advisory: it never blocks and never mutates readiness. ``demand``
    seeds coverage/depth ranking from example questions, analysis intents,
    run-history refs, and the build purpose.

    Args:
        demand: Optional demand signal for ranking richness gaps.

    Returns:
        RichnessReport with demand-ranked coverage and depth gaps.

    Example:
        >>> import marivo.semantic as ms
        >>> report = ms.richness()
        >>> report.show()

    Constraints:
        Advisory only — does not block readiness certification or runtime analysis.
    """
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject()
    project.load()
    return project.richness(demand=demand)


__all__ = [
    "AggregateFoldInput",
    "AggregateFoldValue",
    "AiContextValue",
    "CalendarLevelDetails",
    "CalendarPeriodPage",
    "CatalogCollection",
    "CatalogEntry",
    "DatasourceDetails",
    "DatasourceEntry",
    "DerivedMetricDetails",
    "DimensionDetails",
    "DimensionEntry",
    "DomainDetails",
    "DomainEntry",
    "EntityDetails",
    "EntityEntry",
    "GrainToDate",
    "JoinKey",
    "MeasureDetails",
    "MeasureEntry",
    "MetricDetails",
    "MetricEntry",
    "PeriodCalendarDetails",
    "PeriodCalendarEntry",
    "PeriodCalendarKind",
    "PeriodCorrespondence",
    "PreviewBatchResult",
    "ReadinessInputSummary",
    "ReadinessIssue",
    "ReadinessReport",
    "Ref",
    "RelationshipDetails",
    "RelationshipEntry",
    "RichnessReport",
    "SemanticCatalog",
    "SemanticDefinition",
    "SemanticDefinitionReadError",
    "SemanticKind",
    "SimpleMetricDetails",
    "SourceCheck",
    "SourceHealthCheckResult",
    "SourceHealthReport",
    "TemporalOccurrencePage",
    "TemporalSetDetails",
    "TemporalSetEntry",
    "TemporalSetKind",
    "TimeDimensionDetails",
    "TimeDimensionEntry",
    "WorkScheduleDetails",
    "WorkScheduleEntry",
    "WorkScheduleKind",
    "additive",
    "additive_all",
    "aggregate",
    "ai_context",
    "bind",
    "calendar_grain",
    "count",
    "cumulative",
    "datetime",
    "dimension",
    "dimension_column",
    "domain",
    "empty",
    "entity",
    "errors",
    "grain_to_date",
    "hour_prefix",
    "join_on",
    "linear",
    "load",
    "measure",
    "measure_column",
    "metric",
    "non_additive",
    "nulls",
    "period_calendar",
    "period_correspondence",
    "ratio",
    "ref",
    "relationship",
    "richness",
    "snapshot",
    "source_check",
    "strptime",
    "temporal_set",
    "time_dimension",
    "time_dimension_column",
    "timestamp",
    "trailing",
    "typing",
    "validity",
    "weighted_mean",
    "where",
    "work_schedule",
    "zero_denominator",
]


def _install_telemetry() -> None:
    import sys

    from marivo.semantic._capabilities.registry import REGISTRY
    from marivo.telemetry import install_surface_instrumentation

    install_surface_instrumentation(
        surface="semantic",
        descriptors=REGISTRY._descriptors,
        root_module=sys.modules[__name__],
    )


_install_telemetry()
