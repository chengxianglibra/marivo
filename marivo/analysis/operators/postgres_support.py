"""Pure admission for the implemented postgres scalar method closure."""

from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.operators.association_contracts import CorrelatePayload
from marivo.analysis.operators.scalar_support import (
    entity_correlation_reason,
    supports_scalar_type,
    supports_timestamp,
)
from marivo.analysis.operators.scalar_support import unsupported_reason as scalar_reason


def supported_type(value: str) -> bool:
    """Recognize the logical scalar types admitted by this backend."""
    return (value == "boolean" or supports_timestamp(value)) or supports_scalar_type(value)


def unsupported_reason(dataset: LogicalDataset) -> str | None:
    """Describe an unqualified source closure without source work.

    Row expressions, Linear graphs, and every scalar status-time fold kind
    (first/last/mean/min/max) are qualified: live probes measured the fold
    lowering's argmin/argmax ARRAY_AGG translations executing exactly beside
    native AVG/MIN/MAX. Decimal stays restricted to the add/sub/mul/sum rule
    cells: PostgreSQL numeric division and AVG scales are server-defined and
    not a public publication contract, so the ``div`` and ``mean`` decimal
    units keep their blanket rejection. Percentile-tuple folds stay rejected
    with every backend until a quantile fold lowering is qualified. Exact
    direct-measure membership and exact linear-interpolation distribution are
    qualified by live source-private execution. Entity-key membership is also
    qualified through the native PostgreSQL struct key and retained rollup.
    """
    if isinstance(dataset._root, LogicalRootHandle) and isinstance(
        dataset._root.payload, CorrelatePayload
    ):
        return entity_correlation_reason(dataset)
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
        resolved_decimal_units=frozenset({"linear"}),
        status_folds=frozenset({"first", "last", "mean", "min", "max"}),
        distinct_memberships=frozenset({"measure", "entity"}),
        distributions=frozenset({"linear_interpolation"}),
        expanded_attribution=True,
    )
