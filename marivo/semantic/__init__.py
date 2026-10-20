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

    finalized = ms.finalize()
    amount = finalized.resolve(amount)

    @ms.metric()
    def revenue():
        return amount.sum()
"""

from __future__ import annotations

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
    additive,
    additive_all,
    ai_context,
    bind,
    calendar_grain,
    datetime,
    dimension,
    dimension_column,
    domain,
    entity,
    finalize,
    hour_prefix,
    join_on,
    measure,
    measure_column,
    metric,
    non_additive,
    period_calendar,
    period_correspondence,
    relationship,
    snapshot,
    strptime,
    temporal_set,
    time_dimension,
    time_dimension_column,
    timestamp,
    validity,
    work_schedule,
)
from marivo.semantic.authoring import (
    aggregate as aggregate,
)
from marivo.semantic.authoring import (
    count as count,
)
from marivo.semantic.authoring import (
    cumulative as cumulative,
)
from marivo.semantic.authoring import (
    empty as empty,
)
from marivo.semantic.authoring import (
    grain_to_date as grain_to_date,
)
from marivo.semantic.authoring import (
    linear as linear,
)
from marivo.semantic.authoring import (
    nulls as nulls,
)
from marivo.semantic.authoring import (
    ratio as ratio,
)
from marivo.semantic.authoring import (
    trailing as trailing,
)
from marivo.semantic.authoring import (
    weighted_mean as weighted_mean,
)
from marivo.semantic.authoring import (
    where as where,
)
from marivo.semantic.authoring import (
    zero_denominator as zero_denominator,
)
from marivo.semantic.catalog import (
    CalendarLevelDetails,
    CalendarPeriodPage,
    CatalogCollection,
    CatalogEntry,
    DatasourceDetails,
    DatasourceEntry,
    DomainDetails,
    DomainEntry,
    PeriodCalendarDetails,
    PeriodCalendarEntry,
    SemanticCatalog,
    TemporalOccurrencePage,
    TemporalSetDetails,
    TemporalSetEntry,
    WorkScheduleDetails,
    WorkScheduleEntry,
    load,
)
from marivo.semantic.dtos import PreviewBatchResult
from marivo.semantic.expressions import ExpressionContract
from marivo.semantic.ir import (
    JoinKey,
)
from marivo.semantic.readiness import (
    ReadinessReport,
)
from marivo.semantic.source_health import (
    SourceCheck,
    SourceHealthReport,
    source_check,
)

__all__ = [
    "CalendarLevelDetails",
    "CalendarPeriodPage",
    "CatalogCollection",
    "CatalogEntry",
    "DatasourceDetails",
    "DatasourceEntry",
    "DomainDetails",
    "DomainEntry",
    "ExpressionContract",
    "JoinKey",
    "PeriodCalendarDetails",
    "PeriodCalendarEntry",
    "PeriodCalendarKind",
    "PreviewBatchResult",
    "ReadinessReport",
    "Ref",
    "SemanticCatalog",
    "SemanticKind",
    "SourceCheck",
    "SourceHealthReport",
    "TemporalOccurrencePage",
    "TemporalSetDetails",
    "TemporalSetEntry",
    "TemporalSetKind",
    "WorkScheduleDetails",
    "WorkScheduleEntry",
    "WorkScheduleKind",
    "additive",
    "additive_all",
    "ai_context",
    "bind",
    "calendar_grain",
    "datetime",
    "dimension",
    "dimension_column",
    "domain",
    "entity",
    "errors",
    "finalize",
    "hour_prefix",
    "join_on",
    "load",
    "measure",
    "measure_column",
    "metric",
    "non_additive",
    "period_calendar",
    "period_correspondence",
    "ref",
    "relationship",
    "snapshot",
    "source_check",
    "strptime",
    "temporal_set",
    "time_dimension",
    "time_dimension_column",
    "timestamp",
    "typing",
    "validity",
    "work_schedule",
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
