"""marivo.semantic - Python-native semantic layer (v1.1).

Public surface::

    import marivo.datasource as md
    import marivo.semantic as ms

    catalog = ms.load()                # returns SemanticCatalog
    catalog = ms.load(domains=['sales'])  # filter to specific domains
    catalog.search(kind="domain").show()
    catalog.search(kind="metric").show()  # metric entries across loaded domains

    ms.domain(name="sales", owner="Mina Zhang", default=True)
    warehouse = md.duckdb("warehouse").ref
    orders = ms.entity(name="orders", datasource=warehouse, source=md.table("orders"))
    amount = ms.measure_column(
        name="amount", entity=orders, column="amount",
        additivity=ms.additive_all(), unit="USD",
    )

    @ms.metric()
    def revenue():
        return amount.sum()
"""

from __future__ import annotations

from marivo._symbolic import symbol_factory as ref
from marivo.refs import (
    PeriodCalendarKind,
    SemanticKind,
    TemporalSetKind,
)
from marivo.semantic import errors as errors
from marivo.semantic import typing as typing
from marivo.semantic.authoring import (
    additive_all,
    additive_except,
    additive_over,
    ai_context,
    bind,
    calendar_grain,
    dimension,
    dimension_column,
    domain,
    entity,
    hour_prefix,
    join_on,
    measure,
    measure_column,
    metric,
    non_additive,
    period_calendar,
    relationship,
    rescale,
    snapshot,
    strptime,
    temporal_set,
    time_dimension,
    time_dimension_column,
    timestamp,
    validity,
)
from marivo.semantic.authoring import (
    aggregate as aggregate,
)
from marivo.semantic.authoring import (
    count as count,
)
from marivo.semantic.authoring import (
    empty as empty,
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
    weighted_mean as weighted_mean,
)
from marivo.semantic.authoring import (
    where as where,
)
from marivo.semantic.catalog import (
    CalendarPeriodPage,
    CatalogEntry,
    DatasourceEntry,
    DimensionEntry,
    DomainEntry,
    EntityEntry,
    MeasureEntry,
    MetricEntry,
    PeriodCalendarEntry,
    RelationshipEntry,
    SemanticCatalog,
    TemporalOccurrencePage,
    TemporalSetEntry,
    TimeDimensionEntry,
    load,
)
from marivo.semantic.catalog import (
    SemanticEntry as SemanticEntry,
)
from marivo.semantic.catalog_entries import (
    CatalogFilters,
    CatalogPage,
    CatalogScope,
    DatasourceDefinition,
    DomainDefinition,
    EntityDefinition,
    EntryProvenance,
    FieldDefinition,
    MetricDefinition,
    MetricTimeBinding,
    PeriodCalendarDefinition,
    RelationshipDefinition,
    TemporalSetDefinition,
)
from marivo.semantic.dtos import PreviewBatchResult
from marivo.semantic.expressions import ExpressionContract
from marivo.semantic.ir import (
    JoinKey,
)
from marivo.semantic.readiness import (
    ReadinessReport,
)
from marivo.semantic.validation import (
    SourceCheck,
    ValidationReport,
    source_check,
)

__all__ = [
    "CalendarPeriodPage",
    "CatalogEntry",
    "CatalogFilters",
    "CatalogPage",
    "CatalogScope",
    "DatasourceDefinition",
    "DatasourceEntry",
    "DimensionEntry",
    "DomainDefinition",
    "DomainEntry",
    "EntityDefinition",
    "EntityEntry",
    "EntryProvenance",
    "ExpressionContract",
    "FieldDefinition",
    "JoinKey",
    "MeasureEntry",
    "MetricDefinition",
    "MetricEntry",
    "MetricTimeBinding",
    "PeriodCalendarDefinition",
    "PeriodCalendarEntry",
    "PeriodCalendarKind",
    "PreviewBatchResult",
    "ReadinessReport",
    "RelationshipDefinition",
    "RelationshipEntry",
    "SemanticCatalog",
    "SemanticKind",
    "SourceCheck",
    "TemporalOccurrencePage",
    "TemporalSetDefinition",
    "TemporalSetEntry",
    "TemporalSetKind",
    "TimeDimensionEntry",
    "ValidationReport",
    "additive_all",
    "additive_except",
    "additive_over",
    "ai_context",
    "bind",
    "calendar_grain",
    "dimension",
    "dimension_column",
    "domain",
    "entity",
    "errors",
    "hour_prefix",
    "join_on",
    "load",
    "measure",
    "measure_column",
    "metric",
    "non_additive",
    "period_calendar",
    "ref",
    "relationship",
    "rescale",
    "snapshot",
    "source_check",
    "strptime",
    "temporal_set",
    "time_dimension",
    "time_dimension_column",
    "timestamp",
    "typing",
    "validity",
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
