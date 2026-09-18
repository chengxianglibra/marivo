"""Pure admission for the implemented mysql scalar method closure."""

from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.operators.scalar_support import supports_plain_timestamp, supports_scalar_type
from marivo.analysis.operators.scalar_support import unsupported_reason as scalar_reason


def supported_type(value: str) -> bool:
    """Recognize declared scalar types; physical constraints are checked at execution."""
    return (
        value in {"boolean", "uint8", "uint16", "uint32", "uint64"}
        or supports_plain_timestamp(value)
        or supports_scalar_type(value)
    )


def unsupported_reason(dataset: LogicalDataset) -> str | None:
    """Describe an unqualified source closure without source work."""
    return scalar_reason(
        dataset,
        supported_type,
        relationships=True,
        versions=True,
        date_buckets=True,
        explicit_decimal_sources=True,
        closed_open_null_validity=True,
    )
