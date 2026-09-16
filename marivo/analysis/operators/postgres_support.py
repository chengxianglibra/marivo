"""Pure admission for the implemented PostgreSQL scalar Metric closure."""

from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.operators.group_a_support import (
    supports as supports_group_a,
)
from marivo.analysis.operators.group_a_support import (
    supports_scalar_type,
)


def supported_type(value: str) -> bool:
    """Recognize the logical scalar types admitted by this backend."""
    return value in {"boolean", "timestamp"} or supports_scalar_type(value)


def supports(dataset: LogicalDataset) -> bool:
    """Check the complete source closure without opening a datasource."""
    return supports_group_a(dataset, supported_type)
