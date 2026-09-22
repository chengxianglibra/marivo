"""Pure admission for the implemented clickhouse scalar method closure."""

from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.operators.scalar_support import supports_scalar_type, supports_timestamp
from marivo.analysis.operators.scalar_support import unsupported_reason as scalar_reason


def supported_type(value: str) -> bool:
    """Recognize declared scalar types; physical constraints are checked at execution."""
    return (
        value in {"boolean", "uint8", "uint16", "uint32", "uint64"}
        or supports_timestamp(value)
        or supports_scalar_type(value)
    )


def unsupported_reason(dataset: LogicalDataset) -> str | None:
    """Describe an unqualified source closure without source work.

    Row expressions, Linear graphs, and every scalar status-time fold kind
    (first/last/mean/min/max) are qualified: the live probe measured native
    argMin/argMax exact beside AVG/MIN/MAX. Only the ``linear``
    decimal unit resolves: ClickHouse decimal division truncates scale and AVG
    returns Float64, so the wider internal accumulation is never claimed as a
    wider public precision and ``div``/``mean`` stay rejected. Exact
    direct-measure and Entity-key membership and exact linear-interpolation
    distribution are qualified by live source-private execution. Window
    aggregate casts are lowered outside ClickHouse window functions.
    """
    return scalar_reason(
        dataset,
        supported_type,
        relationships=True,
        versions=True,
        date_buckets=True,
        timestamp_buckets=True,
        parsed_time_axes=True,
        explicit_decimal_sources=True,
        row_expressions=True,
        linear_graphs=True,
        resolved_decimal_units=frozenset({"linear"}),
        status_folds=frozenset({"first", "last", "mean", "min", "max"}),
        distinct_memberships=frozenset({"measure", "entity"}),
        distributions=frozenset({"linear_interpolation"}),
    )
