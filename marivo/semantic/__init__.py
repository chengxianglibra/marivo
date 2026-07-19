"""marivo.semantic - Python-native semantic layer (v1.1).

Public surface::

    import marivo.datasource as md
    import marivo.semantic as ms

    catalog = ms.load()                # returns SemanticCatalog
    catalog = ms.load(domains=['sales'])  # filter to specific domains
    catalog.domains.show()
    catalog.metrics.show()                                  # all metrics across domains

    ms.domain(name="sales", owner="Mina Zhang", default=True)
    warehouse = md.ref("datasource.warehouse")
    orders = ms.entity(name="orders", datasource=warehouse, source=md.table("orders"))
    amount = ms.measure_column(
        name="amount", entity=orders, column="amount",
        additivity="additive", unit="USD",
    )

    revenue = ms.aggregate(name="revenue", measure=amount, agg="sum")
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from marivo.refs import SemanticRef
from marivo.semantic import errors as errors
from marivo.semantic import typing as typing
from marivo.semantic.authoring import (
    DomainRef,
    aggregate,
    ai_context,
    count,
    cumulative,
    datetime,
    dimension,
    dimension_column,
    domain,
    entity,
    from_sql,
    grain_to_date,
    hour_prefix,
    join_on,
    linear,
    measure,
    measure_column,
    metric,
    ratio,
    ref,
    relationship,
    semi_additive,
    snapshot,
    strptime,
    time_dimension,
    time_dimension_column,
    timestamp,
    trailing,
    validity,
    weighted_average,
    where,
)
from marivo.semantic.catalog import (
    CatalogCollection,
    CatalogObject,
    Datasource,
    DatasourceDetails,
    DerivedMetricDetails,
    Dimension,
    DimensionDetails,
    Domain,
    DomainDetails,
    Entity,
    EntityDetails,
    Measure,
    MeasureDetails,
    Metric,
    MetricDetails,
    Relationship,
    RelationshipDetails,
    SemanticCatalog,
    SemanticKind,
    SimpleMetricDetails,
    TimeDimension,
    TimeDimensionDetails,
    load,
)
from marivo.semantic.dtos import PreviewBatchResult, VerifyResult
from marivo.semantic.help import help, help_text
from marivo.semantic.ir import (
    AggregateFoldInput,
    AggregateFoldValue,
    JoinKey,
    SqlProvenance,
)
from marivo.semantic.parity import ParityResult
from marivo.semantic.readiness import (
    ReadinessInputSummary,
    ReadinessIssue,
    ReadinessReport,
)
from marivo.semantic.refs import (
    DimensionRef,
    EntityRef,
    MeasureRef,
    MetricRef,
    RelationshipRef,
    TimeDimensionRef,
)
from marivo.semantic.richness import RichnessReport
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


def parity_check(
    name: str,
    *,
    rel_tol: float | None = None,
    abs_tol: float | None = None,
    force: bool = False,
) -> ParityResult:
    """Run parity check for a metric against its source SQL.

    Datasource backends are resolved internally via the connection service.

    Args:
        name: Fully qualified metric ref (e.g. ``"sales.revenue"``).
        rel_tol: Relative tolerance for numeric comparison. None uses default.
        abs_tol: Absolute tolerance for numeric comparison. None uses default.
        force: If True, re-runs parity even if cached results exist.

    Returns:
        ParityResult with comparison details and pass/fail status.

    Example:
        >>> import marivo.semantic as ms
        >>> result = ms.parity_check("sales.revenue")
        >>> result.show()

    Constraints:
        Requires the metric to declare ``provenance=ms.from_sql(sql=..., dialect=...)``.
        Raises ``SemanticRuntimeError`` if the metric has no provenance.
    """
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject()
    project.load()
    return project.parity_check(name, rel_tol=rel_tol, abs_tol=abs_tol, force=force)


__all__ = [
    "AggregateFoldInput",
    "AggregateFoldValue",
    "AiContextValue",
    "CatalogCollection",
    "CatalogObject",
    "Datasource",
    "DatasourceDetails",
    "DerivedMetricDetails",
    "Dimension",
    "DimensionDetails",
    "DimensionRef",
    "Domain",
    "DomainDetails",
    "DomainRef",
    "Entity",
    "EntityDetails",
    "EntityRef",
    "JoinKey",
    "Measure",
    "MeasureDetails",
    "MeasureRef",
    "Metric",
    "MetricDetails",
    "MetricRef",
    "ParityResult",
    "PreviewBatchResult",
    "ReadinessInputSummary",
    "ReadinessIssue",
    "ReadinessReport",
    "Relationship",
    "RelationshipDetails",
    "RelationshipRef",
    "RichnessReport",
    "SemanticCatalog",
    "SemanticKind",
    "SemanticRef",
    "SimpleMetricDetails",
    "SqlProvenance",
    "TimeDimension",
    "TimeDimensionDetails",
    "TimeDimensionRef",
    "VerifyResult",
    "aggregate",
    "ai_context",
    "count",
    "cumulative",
    "datetime",
    "dimension",
    "dimension_column",
    "domain",
    "entity",
    "errors",
    "from_sql",
    "grain_to_date",
    "help",
    "help_text",
    "hour_prefix",
    "join_on",
    "linear",
    "load",
    "measure",
    "measure_column",
    "metric",
    "parity_check",
    "ratio",
    "ref",
    "relationship",
    "richness",
    "semi_additive",
    "snapshot",
    "strptime",
    "time_dimension",
    "time_dimension_column",
    "timestamp",
    "trailing",
    "typing",
    "validity",
    "weighted_average",
    "where",
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
