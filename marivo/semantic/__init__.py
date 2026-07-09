"""marivo.semantic - Python-native semantic layer (v1.1).

Public surface::

    import marivo.datasource as md
    import marivo.semantic as ms

    catalog = ms.load()                # returns SemanticCatalog
    catalog = ms.load(domains=['sales'])  # filter to specific domains
    catalog.list("domain").show()
    catalog.list("metric").show()                                  # all metrics across domains
    catalog.list("metric", scope="domain.sales").show()            # metrics in one domain

    ms.domain(name="sales", owner="Mina Zhang", default=True)
    warehouse = md.ref("datasource.warehouse")
    orders = ms.entity(name="orders", datasource=warehouse, source=ms.table("orders"))
    amount = ms.measure_column(
        name="amount", entity=orders, column="amount",
        additivity="additive", unit="USD",
    )

    revenue = ms.aggregate(name="revenue", measure=amount, agg="sum")
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from marivo.datasource.scan import ScanScope
from marivo.refs import SemanticRef
from marivo.semantic import errors as errors
from marivo.semantic import typing as typing
from marivo.semantic.authoring import (
    DomainRef,
    aggregate,
    ai_context,
    count,
    csv,
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
    json,
    linear,
    measure,
    measure_column,
    metric,
    parquet,
    ratio,
    ref,
    relationship,
    semi_additive,
    snapshot,
    strptime,
    table,
    time_dimension,
    time_dimension_column,
    timestamp,
    trailing,
    validity,
    weighted_average,
)
from marivo.semantic.catalog import (
    DatasourceDetails,
    DerivedMetricDetails,
    DimensionDetails,
    DomainDetails,
    EntityDetails,
    MeasureDetails,
    MetricDetails,
    RelationshipDetails,
    SemanticCatalog,
    SemanticKind,
    SemanticObject,
    SemanticObjectDetails,
    SemanticObjectList,
    SimpleMetricDetails,
    TimeDimensionDetails,
    load,
)
from marivo.semantic.dtos import AuthoringQuestion, VerifyResult
from marivo.semantic.help import help, help_text
from marivo.semantic.ir import (
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


def verify_object(
    ref: SemanticRef,
    *,
    scope: ScanScope | None = None,
) -> VerifyResult:
    """Verify a single authored semantic object is reachable and valid.

    For domains, relationships, and dimensions this is a static-only check.
    For entities, a scoped preview confirms the datasource is reachable and
    the expression is valid. For time dimensions, metrics, and derived
    metrics, the check is static and validates the loaded semantic contract.

    Args:
        ref: SemanticRef returned by an authoring call, ``ms.ref(...)``, or
            ``catalog.get(...).ref``.
        scope: Scan scope controlling partition, max rows, and timeout.
            Defaults to ``ScanScope()``.

    Returns:
        VerifyResult with status, issues, and optional scan report.

    Example:
        >>> import marivo.semantic as ms
        >>> result = ms.verify_object(ms.ref("entity.sales.orders"))
        >>> result.status

    Constraints:
        Run after authoring each semantic object. Fix failed verification
        before advancing to dependent objects.
    """
    from marivo.semantic.reader import SemanticProject

    if not isinstance(ref, SemanticRef):
        errors._raise(
            errors.ErrorKind.INVALID_REF,
            "ms.verify_object(ref=...) requires a SemanticRef from an authoring call, "
            "ms.ref('<kind>.<semantic_id>'), or catalog.get('<kind>.<semantic_id>').ref.",
            cls=errors.SemanticRuntimeError,
            refs=(str(ref),),
        )

    project = SemanticProject()
    project.load()
    if scope is None:
        scope = ScanScope()
    return project.verify_object(ref, scope=scope)


def readiness(
    *,
    refs: Sequence[SemanticRef] | None = None,
) -> ReadinessReport:
    """Run structural readiness check for the given semantic refs.

    Performs pure in-memory checks without datasource connectivity.
    For runtime validation, use ``catalog.preview(...)``,
    ``ms.parity_check(...)``, and ``ms.richness()``.

    Args:
        refs: Semantic refs to check. Resolves the full dependency closure
            for each ref. None checks all loaded objects.

    Returns:
        ReadinessReport indicating whether analysis handoff is safe.

    Example:
        >>> import marivo.semantic as ms
        >>> report = ms.readiness()
        >>> if report.status == "blocked":
        ...     report.show()

    Constraints:
        This is the required semantic gate before passing refs to analysis APIs.
    """
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject()
    project.load()
    str_refs = [ref.id for ref in refs] if refs is not None else None
    return project.readiness(refs=str_refs)


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
        Advisory only — does not block readiness or analysis handoff.
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
    "AiContextValue",
    "AuthoringQuestion",
    "DatasourceDetails",
    "DerivedMetricDetails",
    "DimensionDetails",
    "DimensionRef",
    "DomainDetails",
    "DomainRef",
    "EntityDetails",
    "EntityRef",
    "JoinKey",
    "MeasureDetails",
    "MeasureRef",
    "MetricDetails",
    "MetricRef",
    "ParityResult",
    "ReadinessInputSummary",
    "ReadinessIssue",
    "ReadinessReport",
    "RelationshipDetails",
    "RelationshipRef",
    "RichnessReport",
    "SemanticCatalog",
    "SemanticKind",
    "SemanticObject",
    "SemanticObjectDetails",
    "SemanticObjectList",
    "SemanticRef",
    "SimpleMetricDetails",
    "SqlProvenance",
    "TimeDimensionDetails",
    "TimeDimensionRef",
    "VerifyResult",
    "aggregate",
    "ai_context",
    "count",
    "csv",
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
    "json",
    "linear",
    "load",
    "measure",
    "measure_column",
    "metric",
    "parity_check",
    "parquet",
    "ratio",
    "readiness",
    "ref",
    "relationship",
    "richness",
    "semi_additive",
    "snapshot",
    "strptime",
    "table",
    "time_dimension",
    "time_dimension_column",
    "timestamp",
    "trailing",
    "typing",
    "validity",
    "verify_object",
    "weighted_average",
]
