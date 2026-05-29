"""SQL parity checking and status propagation for marivo.semantic v1.1.

Implements parity_check and the derived-metric parity status propagation
algorithm.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from marivo.semantic.errors import ErrorKind, SemanticParityError, _raise
from marivo.semantic.ir import MetricIR, ParityStatus
from marivo.semantic.materializer import IbisBackend

if TYPE_CHECKING:
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
    reg = project.registry()
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
    project: SemanticProject,
    metric_id: str,
    *,
    backend_factory: Callable[[str], IbisBackend],
    rel_tol: float | None = None,
    abs_tol: float | None = None,
) -> ParityResult:
    """Run parity check for a base metric against its source SQL.

    Raises SemanticParityError for pre-condition violations:
    - Metric not found
    - Derived metric (not supported for direct SQL parity)
    - Missing source_sql or source_dialect
    - Dialect mismatch with datasource backend_type
    - Cross-datasource metric

    Returns ParityResult on success or value mismatch.
    """
    # Check cache first
    cached = project._parity_results.get(metric_id)
    if cached is not None:
        return cached

    metric_ir = _get_metric_or_raise(project, metric_id)

    # Derived metrics don't support direct SQL parity
    if metric_ir.is_derived:
        _raise(
            ErrorKind.SOURCE_SQL_MISSING,
            f"Derived metric {metric_id!r} does not support direct SQL parity check. "
            f"Check component metrics instead.",
            cls=SemanticParityError,
            refs=(metric_id,),
        )

    # Must have source_sql
    if not metric_ir.provenance.source_sql:
        _raise(
            ErrorKind.SOURCE_SQL_MISSING,
            f"Metric {metric_id!r} has no source_sql. "
            f"Add source_sql to the decorator before running parity checks.",
            cls=SemanticParityError,
            refs=(metric_id,),
        )

    # Must have source_dialect
    if not metric_ir.provenance.source_dialect:
        _raise(
            ErrorKind.SOURCE_SQL_MISSING,
            f"Metric {metric_id!r} has no source_dialect. "
            f"Add source_dialect to the decorator before running parity checks.",
            cls=SemanticParityError,
            refs=(metric_id,),
        )

    # Validate single datasource
    reg = project.registry()
    assert reg is not None  # Already validated above

    datasource_ids: set[str] = set()
    for ds_ref in metric_ir.datasets:
        ds_ir = reg.datasets.get(ds_ref)
        if ds_ir is not None:
            datasource_ids.add(ds_ir.datasource)

    if len(datasource_ids) > 1:
        _raise(
            ErrorKind.CROSS_DATASOURCE_NOT_SUPPORTED,
            f"Metric {metric_id!r} references datasets from "
            f"multiple datasources: {datasource_ids}. "
            f"All datasets in a metric must share the same datasource.",
            cls=SemanticParityError,
            refs=(metric_id,),
        )

    # Determine the single datasource
    if not datasource_ids:
        _raise(
            ErrorKind.SOURCE_SQL_MISSING,
            f"Metric {metric_id!r} has no datasets; cannot determine datasource.",
            cls=SemanticParityError,
            refs=(metric_id,),
        )

    datasource_id = next(iter(datasource_ids))

    # Execute the ibis metric -> single scalar
    try:
        metric_expr = project.materialize_metric(metric_id, backend_factory=backend_factory)
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

    # Execute the source SQL -> single scalar
    try:
        backend = backend_factory(datasource_id)
        sql_result = backend.sql(metric_ir.provenance.source_sql)
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
    project._parity_results[metric_id] = result

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

    | declared_status    | source_sql   | last parity_check | self status    |
    |--------------------|--------------|-------------------|----------------|
    | "python_native"    | any          | any               | PYTHON_NATIVE  |
    | "unverified"       | any          | any               | UNVERIFIED     |
    | None               | absent       | no / not run      | PYTHON_NATIVE  |
    | None               | present      | no / not run      | UNVERIFIED     |
    | None               | any          | ok=True           | VERIFIED       |
    | None               | any          | ok=False          | DRIFTED        |

    When declared_status is None and no parity check has been run:
    - Metrics without source_sql default to PYTHON_NATIVE (no SQL oracle exists).
    - Metrics with source_sql default to UNVERIFIED (SQL oracle exists but has
      not been confirmed).
    For derived metrics, the propagated_parity_status function uses component
    statuses to determine the effective status when the self-status is not
    actionable.
    """
    metric_ir = _get_metric_or_raise(project, metric_id)
    prov = metric_ir.provenance

    # Declared statuses override everything
    if prov.declared_status == "python_native":
        return ParityStatus.PYTHON_NATIVE
    if prov.declared_status == "unverified":
        return ParityStatus.UNVERIFIED

    # No declared status — compute from parity check result
    parity_result = project._parity_results.get(metric_id)

    if parity_result is None:
        # No parity check has been run
        if not prov.source_sql:
            return ParityStatus.PYTHON_NATIVE
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

    For derived metrics without source_sql, the self-status is PYTHON_NATIVE
    (since there is no SQL oracle to verify against). This PYTHON_NATIVE
    self-status propagates upward normally.

    For derived metrics with source_sql but no parity check, the self-status
    is UNVERIFIED. Since derived metrics cannot be directly parity-checked,
    this UNVERIFIED is excluded from propagation -- the derived metric relies
    on component statuses instead. An explicitly declared "unverified" also
    produces UNVERIFIED self-status and is excluded from propagation for the
    same reason (derived metrics cannot be parity-checked).

    Propagation rules (derived metrics only):
    - If any status is DRIFTED -> DRIFTED
    - If any component is UNVERIFIED -> UNVERIFIED
    - If all components are VERIFIED -> VERIFIED
    - If all components are PYTHON_NATIVE -> PYTHON_NATIVE
    - Mix of VERIFIED + PYTHON_NATIVE -> PYTHON_NATIVE
    """
    metric_ir = _get_metric_or_raise(project, metric_id)
    self_status = compute_self_status(project, metric_id)

    # Base metrics: just return self status
    if not metric_ir.is_derived:
        return self_status

    # Derived metrics: collect component statuses
    component_statuses: list[ParityStatus] = []
    for comp_id in metric_ir.decomposition.components.values():
        comp_status = propagated_parity_status(project, comp_id)
        component_statuses.append(comp_status)

    # For derived metrics, the self_status from "no parity check" is
    # UNVERIFIED, but since derived metrics can't be directly checked,
    # that UNVERIFIED should not propagate. Only PYTHON_NATIVE and DRIFTED
    # self-statuses are meaningful for propagation.
    effective_self: ParityStatus | None = None
    if self_status == ParityStatus.PYTHON_NATIVE:
        effective_self = ParityStatus.PYTHON_NATIVE
    elif self_status == ParityStatus.DRIFTED:
        effective_self = ParityStatus.DRIFTED
    elif self_status == ParityStatus.VERIFIED:
        effective_self = ParityStatus.VERIFIED
    # UNVERIFIED self-status from "no check" is excluded —
    # derived metrics rely on component propagation.

    all_statuses: list[ParityStatus] = []
    if effective_self is not None:
        all_statuses.append(effective_self)
    all_statuses.extend(component_statuses)

    if not all_statuses:
        return ParityStatus.UNVERIFIED

    if any(s == ParityStatus.DRIFTED for s in all_statuses):
        return ParityStatus.DRIFTED
    if any(s == ParityStatus.UNVERIFIED for s in all_statuses):
        return ParityStatus.UNVERIFIED
    if all(s == ParityStatus.VERIFIED for s in all_statuses):
        return ParityStatus.VERIFIED
    # Remaining case: mix of VERIFIED and PYTHON_NATIVE (no DRIFTED or UNVERIFIED)
    return ParityStatus.PYTHON_NATIVE
