"""Definition-owned aggregate intent and source-native repair guidance."""

from __future__ import annotations

import ibis
from ibis.common.exceptions import OperationNotDefinedError, UnsupportedOperationError

from marivo.datasource.engines import require_profile_for_backend_type
from marivo.semantic.ir import DIRECT_ONLY_AGGREGATES, AggKind


def approximate_definition(agg: AggKind) -> AggKind | None:
    """Return the corresponding explicit approximate declaration, preserving q."""
    if agg == "count_distinct":
        return "approx_count_distinct"
    if agg == "median":
        return "approx_median"
    if isinstance(agg, tuple) and agg[0] == "percentile":
        return ("approx_percentile", agg[1])
    return None


def aggregate_repair(agg: AggKind, backend: str) -> str | None:
    """Check installed Ibis translation and provider accuracy without reading rows."""
    name = agg[0] if isinstance(agg, tuple) else agg
    if name not in DIRECT_ONLY_AGGREGATES:
        return None
    profile = require_profile_for_backend_type(backend)
    column = ibis.table({"value": "float64"}, name="aggregate_capability").value

    def supported(kind: AggKind) -> bool:
        operation = kind[0] if isinstance(kind, tuple) else kind
        if operation == "count_distinct" and not profile.exact_count_distinct:
            return False
        if operation in ("median", "percentile") and not profile.exact_quantile:
            return False
        q = kind[1] if isinstance(kind, tuple) else 0.5
        expression = (
            column.nunique()
            if operation == "count_distinct"
            else column.approx_nunique()
            if operation == "approx_count_distinct"
            else column.approx_quantile(q)
            if operation in ("approx_median", "approx_percentile")
            else column.quantile(q)
        )
        try:
            ibis.to_sql(expression, dialect=profile.name)
        except (OperationNotDefinedError, UnsupportedOperationError):
            return False
        return True

    if supported(agg):
        return None
    approximate = approximate_definition(agg)
    if approximate is None:
        return f"Use a datasource with a qualified source-native implementation of agg={agg!r}."
    if supported(approximate):
        return f"If approximation is acceptable, redefine this Metric with agg={approximate!r}; otherwise use a datasource supporting agg={agg!r}. Execution never substitutes the definition."
    return f"The corresponding approximate definition is agg={approximate!r}, but {profile.name} does not support that source operation either. Use a datasource supporting the chosen definition; no local computation or automatic substitution is available."
