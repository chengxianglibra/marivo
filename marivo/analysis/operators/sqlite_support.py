"""Pure admission for the implemented sqlite scalar method closure."""

from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.operators.scalar_support import unsupported_reason as scalar_reason


def supported_type(value: str) -> bool:
    """Recognize declared scalar types; physical constraints are checked at execution."""
    return value in {"string", "int64", "float64", "date", "boolean", "timestamp", "timestamp(6)"}


def unsupported_reason(dataset: LogicalDataset) -> str | None:
    """Describe an unqualified source closure without source work.

    Row expressions, Linear graphs, and every scalar status-time fold kind
    (first/last/mean/min/max) are qualified: the live probe executed the
    lowering's argmax lowering through SQLite's canonical-text
    ``JSON_EXTRACT(JSON_ARRAY(value, MAX(status)), '$[0]')`` form exactly
    beside native AVG/MIN/MAX. SQLite has no decimal storage, so no composed
    decimal unit is resolved. Percentile-tuple folds stay rejected with every
    backend until a quantile fold lowering is qualified. Exact
    distinct-membership and distribution state keep their empty qualification
    sets until a live probe evidence opens them.
    """
    return scalar_reason(
        dataset,
        supported_type,
        relationships=True,
        versions=True,
        date_buckets=True,
        timestamp_buckets=True,
        parsed_time_axes=True,
        row_expressions=True,
        linear_graphs=True,
        status_folds=frozenset({"first", "last", "mean", "min", "max"}),
        distinct_memberships=frozenset(),
        distributions=frozenset(),
    )
