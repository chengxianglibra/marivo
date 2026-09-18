"""Pure admission for the implemented postgres scalar method closure."""

from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.operators.scalar_support import supports_scalar_type, supports_timestamp
from marivo.analysis.operators.scalar_support import unsupported_reason as scalar_reason


def supported_type(value: str) -> bool:
    """Recognize the logical scalar types admitted by this backend."""
    return (value == "boolean" or supports_timestamp(value)) or supports_scalar_type(value)


def unsupported_reason(dataset: LogicalDataset) -> str | None:
    """Describe an unqualified source closure without source work."""
    return scalar_reason(
        dataset,
        supported_type,
        relationships=True,
        versions=True,
        date_buckets=True,
        timestamp_buckets=True,
    )
