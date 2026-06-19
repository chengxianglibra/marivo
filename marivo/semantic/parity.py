"""SQL parity checking and status propagation for marivo.semantic v1.1.

Implements parity_check and the derived-metric parity status propagation
algorithm.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from marivo.datasource.ir import TableSourceIR, qualify_provenance_sql
from marivo.semantic.errors import ErrorKind, SemanticParityError, _raise
from marivo.semantic.ir import MetricIR, ParityStatus

if TYPE_CHECKING:
    from marivo.semantic.catalog import SemanticCatalog
    from marivo.semantic.reader import SemanticProject

__all__ = [
    "ParityResult",
    "parity_check",
    "propagated_parity_status",
]


@dataclass(frozen=True)
class ParityResult:
    """Result of a single metric parity check."""

    ok: bool
    expected: float | int | None = None
    actual: float | int | None = None
    rel_tol: float | None = None
    abs_tol: float | None = None
    error: SemanticParityError | None = None


def _extract_scalar(
    result: Any,
    metric_id: str,
    label: str,
) -> float:
    """Extract a single scalar float from an ibis to_pandas() result.

    Scalar expressions return a plain number; table-like results return
    a DataFrame.  Raises SemanticParityError if the result is not scalar.
    """
    if isinstance(result, (int, float)):
        return float(result)
    # DataFrame-like result — use duck-typing to avoid importing pandas
    if hasattr(result, "iloc") and hasattr(result, "columns"):
        if len(result) != 1 or len(result.columns) != 1:
            _raise(
                ErrorKind.PARITY_NOT_SCALAR,
                f"{label} for metric {metric_id!r} did not produce a single scalar value.",
                cls=SemanticParityError,
                refs=(metric_id,),
            )
        return float(result.iloc[0, 0])
    try:
        return float(result)
    except (TypeError, ValueError):
        _raise(
            ErrorKind.PARITY_NOT_SCALAR,
            f"{label} for metric {metric_id!r} did not produce a scalar value: {type(result).__name__}",
            cls=SemanticParityError,
            refs=(metric_id,),
        )


def _get_metric_or_raise(project: SemanticProject, metric_id: str) -> MetricIR:
    """Look up a metric by ID or raise METRIC_NOT_FOUND."""
    reg = project._registry
    if reg is None:
        _raise(
            ErrorKind.METRIC_NOT_FOUND,
            f"Metric {metric_id!r} not found: project is not loaded.",
            cls=SemanticParityError,
            refs=(metric_id,),
        )
    metric_ir = reg.metrics.get(metric_id)
    if metric_ir is None:
        _raise(
            ErrorKind.METRIC_NOT_FOUND,
            f"Metric {metric_id!r} not found in registry.",
            cls=SemanticParityError,
            refs=(metric_id,),
        )
    return metric_ir


def parity_check(
    project: SemanticProject | SemanticCatalog,
    metric_id: str,
    *,
    rel_tol: float | None = None,
    abs_tol: float | None = None,
    force: bool = False,
) -> ParityResult:
    """Run parity check for a base metric against its source SQL.

    Raises SemanticParityError for pre-condition violations:
    - Metric not found
    - Derived metric (not supported for direct SQL parity)
    - Missing sql_parity verification mode, provenance SQL, or dialect
    - Dialect mismatch with datasource backend_type
    - Cross-datasource metric

    Returns ParityResult on success or value mismatch.
    """
    from marivo.semantic.catalog import SemanticCatalog, SemanticKind
    from marivo.semantic.refs import make_ref

    catalog = project if isinstance(project, SemanticCatalog) else SemanticCatalog(project)
    cache_project = catalog._project

    # Check cache first
    cached = cache_project._parity_results.get(metric_id)
    if cached is not None and not force:
        return cached

    metric_ir = _get_metric_or_raise(cache_project, metric_id)

    # Derived metrics don't support direct SQL parity
    if metric_ir.metric_type == "derived":
        _raise(
            ErrorKind.PROVENANCE_DIALECT_MISSING,
            f"Derived metric {metric_id!r} does not support direct SQL parity check. "
            f"Check component metrics instead.",
            cls=SemanticParityError,
            refs=(metric_id,),
        )

    # Must have provenance SQL (enables parity verification)
    if metric_ir.provenance is None or not metric_ir.provenance.sql:
        _raise(
            ErrorKind.PROVENANCE_DIALECT_MISSING,
            f"Metric {metric_id!r} has no provenance SQL. "
            f"Add provenance=ms.from_sql(...) to the decorator before running parity checks.",
            cls=SemanticParityError,
            refs=(metric_id,),
        )

    # Must have dialect
    assert metric_ir.provenance is not None
    if not metric_ir.provenance.dialect:
        _raise(
            ErrorKind.PROVENANCE_DIALECT_MISSING,
            f"Metric {metric_id!r} has no provenance dialect. "
            f"Add dialect= to ms.from_sql(...) before running parity checks.",
            cls=SemanticParityError,
            refs=(metric_id,),
        )

    # Validate single datasource
    reg = cache_project._registry
    assert reg is not None  # Already validated above

    datasource_ids: set[str] = set()
    for ds_ref in metric_ir.entities:
        ds_ir = reg.entities.get(ds_ref)
        if ds_ir is not None:
            datasource_ids.add(ds_ir.datasource)

    if len(datasource_ids) > 1:
        _raise(
            ErrorKind.CROSS_DATASOURCE_NOT_SUPPORTED,
            f"Metric {metric_id!r} references entities from "
            f"multiple datasources: {datasource_ids}. "
            f"All entities in a metric must share the same datasource.",
            cls=SemanticParityError,
            refs=(metric_id,),
        )

    # Determine the single datasource
    if not datasource_ids:
        _raise(
            ErrorKind.PROVENANCE_DIALECT_MISSING,
            f"Metric {metric_id!r} has no entities; cannot determine datasource.",
            cls=SemanticParityError,
            refs=(metric_id,),
        )

    datasource_id = next(iter(datasource_ids))

    # Execute the ibis metric -> single scalar
    try:
        resolver = catalog._resolver()
        metric_expr = resolver.metric(make_ref(metric_id, SemanticKind.METRIC))
        actual_result = metric_expr.to_pandas()
        actual_val = _extract_scalar(actual_result, metric_id, "Metric")
    except SemanticParityError:
        raise
    except Exception as exc:
        return ParityResult(
            ok=False,
            expected=None,
            actual=None,
            rel_tol=rel_tol,
            abs_tol=abs_tol,
            error=SemanticParityError(
                kind=ErrorKind.MATERIALIZE_FAILED,
                message=f"Failed to materialize metric {metric_id!r}: {exc}",
                refs=(metric_id,),
            ),
        )

    # Build table qualifiers from entity sources for automatic qualification.
    # When source.database is set on the entity, use it directly.
    # When source.database is absent, fall back to the datasource's database
    # field (e.g. MySQL/ClickHouse datasources declare database at the
    # connection level).
    table_qualifiers: dict[str, str] = {}
    for ds_ref in metric_ir.entities:
        entity_ir = reg.entities.get(ds_ref)
        if entity_ir is None:
            continue
        source = entity_ir.source
        if not isinstance(source, TableSourceIR):
            continue
        db: str | tuple[str, ...] | None = source.database
        if db is None:
            # Fall back to the datasource's database field.
            datasource_ir = reg.datasources.get(entity_ir.datasource)
            if datasource_ir is not None:
                ds_db = datasource_ir.fields.get("database")
                if isinstance(ds_db, str):
                    db = ds_db
        if db is not None:
            if isinstance(db, tuple):
                db = ".".join(db)
            table_qualifiers[source.table] = f"{db}.{source.table}"

    qualified_sql = qualify_provenance_sql(
        metric_ir.provenance.sql,
        table_qualifiers,
        dialect=metric_ir.provenance.dialect,
    )

    # Execute the source SQL -> single scalar
    try:
        service = cache_project._connection_service()
        with service.use_backend(datasource_id) as backend:
            sql_result = backend.sql(qualified_sql)
            sql_pandas = sql_result.to_pandas()
        expected_val = _extract_scalar(sql_pandas, metric_id, "Source SQL")
    except SemanticParityError:
        raise
    except Exception as exc:
        return ParityResult(
            ok=False,
            expected=None,
            actual=actual_val,
            rel_tol=rel_tol,
            abs_tol=abs_tol,
            error=SemanticParityError(
                kind=ErrorKind.COMPILE_ERROR,
                message=f"Failed to execute source SQL for metric {metric_id!r}: {exc}",
                refs=(metric_id,),
            ),
        )

    # Compare values
    ok = _values_match(actual_val, expected_val, rel_tol=rel_tol, abs_tol=abs_tol)

    result = ParityResult(
        ok=ok,
        expected=expected_val,
        actual=actual_val,
        rel_tol=rel_tol,
        abs_tol=abs_tol,
    )

    # Cache the result
    cache_project._parity_results[metric_id] = result

    return result


def _values_match(
    actual: float,
    expected: float,
    *,
    rel_tol: float | None = None,
    abs_tol: float | None = None,
) -> bool:
    """Compare two numeric values with optional tolerances.

    If both rel_tol and abs_tol are None, requires exact match.
    Otherwise uses math.isclose with the provided tolerances.
    """
    if rel_tol is None and abs_tol is None:
        return actual == expected

    rt = rel_tol if rel_tol is not None else 0.0
    at = abs_tol if abs_tol is not None else 0.0
    return math.isclose(actual, expected, rel_tol=rt, abs_tol=at)


def compute_self_status(
    project: Any,
    metric_id: str,
) -> ParityStatus:
    """Compute the self-status of a metric based on provenance and parity results.

    This is Step 1 of the two-step status computation.

    | provenance_sql | last parity_check | self status |
    |----------------|-------------------|-------------|
    | absent         | any               | VERIFIED    |
    | present    | no / not run      | UNVERIFIED  |
    | present    | ok=True           | VERIFIED    |
    | present    | ok=False          | DRIFTED     |

    Derived metrics do not have a self verification mode. Their status is
    determined by propagated component statuses.
    """
    metric_ir = _get_metric_or_raise(project, metric_id)
    prov = metric_ir.provenance

    if metric_ir.metric_type == "derived":
        return ParityStatus.UNVERIFIED

    if prov is None:
        return ParityStatus.VERIFIED

    # Metrics with SQL provenance compute status from the latest in-memory parity result.
    parity_result = project._parity_results.get(metric_id)

    if parity_result is None:
        # No parity check has been run
        return ParityStatus.UNVERIFIED

    if parity_result.ok:
        return ParityStatus.VERIFIED
    else:
        return ParityStatus.DRIFTED


def propagated_parity_status(
    project: Any,
    metric_id: str,
) -> ParityStatus:
    """Compute the effective parity status for a metric, including propagation.

    Step 1: Compute self-status from provenance and parity results.
    Step 2: For derived metrics, propagate from component statuses.

    Derived metrics cannot be directly SQL-parity-checked; their own
    UNVERIFIED self-status is ignored and component statuses determine the
    effective status.

    Propagation rules (derived metrics only):
    - If any status is DRIFTED -> DRIFTED
    - If any component is UNVERIFIED -> UNVERIFIED
    - If all components are VERIFIED -> VERIFIED
    """
    metric_ir = _get_metric_or_raise(project, metric_id)
    self_status = compute_self_status(project, metric_id)

    # Base metrics: just return self status
    if metric_ir.metric_type != "derived":
        return self_status

    # Derived metrics: collect component statuses
    component_statuses: list[ParityStatus] = []
    if metric_ir.composition is not None:
        from marivo.semantic.ir import composition_components

        for comp_id in composition_components(metric_ir.composition).values():
            comp_status = propagated_parity_status(project, comp_id)
            component_statuses.append(comp_status)

    if not component_statuses:
        return ParityStatus.UNVERIFIED

    if any(s == ParityStatus.DRIFTED for s in component_statuses):
        return ParityStatus.DRIFTED
    if any(s == ParityStatus.UNVERIFIED for s in component_statuses):
        return ParityStatus.UNVERIFIED
    if all(s == ParityStatus.VERIFIED for s in component_statuses):
        return ParityStatus.VERIFIED
    return ParityStatus.UNVERIFIED
