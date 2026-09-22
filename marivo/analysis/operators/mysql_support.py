"""Pure admission for the implemented mysql scalar method closure."""

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

    Row expressions and Linear graphs are qualified, including the sum-level
    ``linear`` decimal cell. The mean/min/max status-time folds are qualified
    on native AVG/MIN/MAX; the first/last folds stay rejected because the live
    probe measured ibis compiling MySQL ArgMin/ArgMax to nothing
    (OperationNotDefinedError), and source-row-order emulation is not a
    substitute for status-ordered argmin/argmax. The ``mean`` and ``div`` decimal cells stay
    rejected even though the live MySQL probe measured engine AVG as exact at
    the declared ``s+4`` scale with ROUND_HALF_UP (the decimal-exact 5-tie
    0.3128125 returned 0.312813): the mean pipeline still publishes
    ``sum/count`` through a float-labeled division that the value-exact
    transport rule refuses, and the mandated bit-exact ROUND_HALF_EVEN
    quantization is unmeetable because the engine rounds half-up. No
    decimal-rooted ratio is constructible while engine division inference
    labels results float64. Both cells open only when the mean equation
    publishes exact values end to end under the declared rounding contract.
    Direct-measure membership and exact linear-interpolation distribution are
    qualified by live source-private execution. Entity membership lowers its
    typed identity fields to scalar SQL and reconstructs the private Arrow struct.
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
        status_folds=frozenset({"mean", "min", "max"}),
        distinct_memberships=frozenset({"measure", "entity"}),
        distributions=frozenset({"linear_interpolation"}),
    )
