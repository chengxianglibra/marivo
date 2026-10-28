"""Definition-owned aggregate intent and source-native repair guidance."""

from __future__ import annotations

from marivo._source_aggregates import native_aggregate
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
    """Check source algorithm availability without qualifying actual input types."""
    name = agg[0] if isinstance(agg, tuple) else agg
    if name not in DIRECT_ONLY_AGGREGATES:
        return None

    def supported(kind: AggKind) -> bool:
        operation = kind[0] if isinstance(kind, tuple) else kind
        return native_aggregate(backend, operation) is not None

    if supported(agg):
        return None
    approximate = approximate_definition(agg)
    if approximate is None:
        return f"Use a datasource with a qualified source-native implementation of agg={agg!r}."
    if supported(approximate):
        return f"If approximation is acceptable, redefine this Metric with agg={approximate!r}; otherwise use a datasource supporting agg={agg!r}. Execution never substitutes the definition."
    return f"The corresponding approximate definition is agg={approximate!r}, but {backend} does not support that source operation either. Use a datasource supporting the chosen definition; no local computation or automatic substitution is available."
