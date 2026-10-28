"""Closed native source aggregate policy, independent of Ibis compilation success."""

from collections.abc import Mapping
from typing import Literal, TypeAlias

import ibis.expr.datatypes as dt

AggregateOperation: TypeAlias = Literal[
    "count_distinct", "approx_nunique", "quantile", "approx_quantile"
]
NativeAggregate: TypeAlias = Literal[
    "count_distinct",
    "approx_count_distinct",
    "approx_distinct",
    "uniqExact",
    "uniqHLL12",
    "quantile_cont",
    "percentile_cont",
    "quantileExactInclusive",
    "approx_quantile",
    "approx_percentile",
    "quantileTDigest",
]

_NATIVE: Mapping[str, Mapping[AggregateOperation, NativeAggregate]] = {
    "duckdb": {
        "count_distinct": "count_distinct",
        "approx_nunique": "approx_count_distinct",
        "quantile": "quantile_cont",
        "approx_quantile": "approx_quantile",
    },
    "postgres": {"count_distinct": "count_distinct", "quantile": "percentile_cont"},
    "mysql": {"count_distinct": "count_distinct"},
    "trino": {
        "count_distinct": "count_distinct",
        "approx_nunique": "approx_distinct",
        "approx_quantile": "approx_percentile",
    },
    "clickhouse": {
        "count_distinct": "uniqExact",
        "approx_nunique": "uniqHLL12",
        "quantile": "quantileExactInclusive",
        "approx_quantile": "quantileTDigest",
    },
}


def aggregate_operation(reducer: str) -> AggregateOperation | None:
    """Normalize median and the internal declaration vocabulary to four operations."""
    if reducer == "count_distinct":
        return "count_distinct"
    if reducer in ("approx_nunique", "approx_count_distinct"):
        return "approx_nunique"
    if reducer in ("median", "quantile", "percentile"):
        return "quantile"
    if reducer in ("approx_median", "approx_quantile", "approx_percentile"):
        return "approx_quantile"
    return None


def native_aggregate(
    backend: str, reducer: str, dtype: dt.DataType | None = None, *, duration: bool = False
) -> NativeAggregate | None:
    """Select only a native algorithm preserving the declared accuracy and input type."""
    operation = aggregate_operation(reducer)
    if operation is None:
        return None
    algorithm = _NATIVE.get(backend, {}).get(operation)
    if operation in ("quantile", "approx_quantile"):
        if duration or (dtype is not None and not dtype.is_numeric()):
            return None
        if (
            backend == "clickhouse"
            and operation == "quantile"
            and dtype is not None
            and not (dtype.is_integer() or dtype.is_floating())
        ):
            return None
    return algorithm


def aggregate_repair_action(
    backend: str, reducer: str, dtype: dt.DataType, *, duration: bool = False
) -> str:
    """Build a repair from the actual admitted native routes, never a substitution."""
    candidates = tuple(
        name
        for name in sorted(_NATIVE)
        if native_aggregate(name, reducer, dtype, duration=duration) is not None
    )
    route = (
        f"Use a datasource supporting this definition and type: {', '.join(candidates)}."
        if candidates
        else "Use a natively supported input type for the declared reduction."
    )
    input_type = f"source Duration ({dtype} ticks)" if duration else str(dtype)
    return (
        f"{route} {backend} cannot compile {reducer}({input_type}) through a qualified native "
        "algorithm. Change the definition only if that matches the intended metric. "
        "Exact and approximate algorithms are never substituted. No business row was read."
    )
