"""Shared runtime preflight for authored semantic filter literals."""

from __future__ import annotations

import ibis.expr.types as ir

from marivo.semantic.errors import ErrorKind, SemanticRuntimeError


def authored_filter_predicate(
    field: ir.Value,
    value: object,
    *,
    metric_id: str,
    dimension_id: str,
) -> ir.BooleanValue:
    """Build one validated equality or membership predicate without executing it."""
    values = value if isinstance(value, tuple) else (value,)
    try:
        for item in values:
            _ = field == item
        return field.isin(values) if isinstance(value, tuple) else field == value
    except Exception as exc:
        received_types = tuple(dict.fromkeys(type(item).__name__ for item in values))
        physical_dtype = str(field.type())
        raise SemanticRuntimeError(
            kind=ErrorKind.FILTER_VALUE_RUNTIME_INCOMPATIBLE,
            message=(
                f"Metric {metric_id!r} authored filter on dimension {dimension_id!r} "
                f"cannot be represented against runtime dtype {physical_dtype}. "
                "The authored business literals remain unchanged."
            ),
            refs=(metric_id, dimension_id),
            expected=f"authored values comparable with runtime dtype {physical_dtype}",
            received=", ".join(received_types),
            location_label="runtime filter preflight",
            details={
                "metric": metric_id,
                "dimension": dimension_id,
                "physical_dtype": physical_dtype,
                "received_value_types": received_types,
                "query_executed": False,
                "declaration_preserved": True,
                "cause_type": type(exc).__name__,
            },
        ) from exc
