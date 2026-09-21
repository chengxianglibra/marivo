"""Pure admission for the implemented trino scalar method closure."""

from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.operators.scalar_support import supports_plain_timestamp, supports_scalar_type
from marivo.analysis.operators.scalar_support import unsupported_reason as scalar_reason


def supported_type(value: str) -> bool:
    """Recognize the logical scalar types admitted by this backend."""
    return (
        value == "boolean"
        or supports_plain_timestamp(value)
        or (value not in {"int8", "int16"} and supports_scalar_type(value))
    )


def unsupported_reason(dataset: LogicalDataset) -> str | None:
    """Describe an unqualified source closure without source work.

    Row expressions, Linear graphs, and every scalar status-time fold kind
    (first/last/mean/min/max) are qualified: the engine registers no
    arg_min/arg_max, but ibis compiles those folds to MIN_BY/MAX_BY and the
    live probe executed both exactly beside native AVG/MIN/MAX. Decimal keeps the
    conservative rejection: the live Trino probe measured ``AVG(DECIMAL)``
    staying at the input scale and rounding (10.005 -> 10.01), so the mean
    result scale is lossy and no composed decimal unit is a public contract.
    Exact distinct-membership and distribution state keep their empty
    qualification sets until a live probe evidence opens them.
    """
    return scalar_reason(
        dataset,
        supported_type,
        relationships=True,
        versions=True,
        date_buckets=True,
        timestamp_buckets=True,
        explicit_decimal_sources=True,
        row_expressions=True,
        linear_graphs=True,
        status_folds=frozenset({"first", "last", "mean", "min", "max"}),
        distinct_memberships=frozenset(),
        distributions=frozenset(),
    )
