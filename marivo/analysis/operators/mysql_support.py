"""Pure admission for the implemented mysql Group A closure."""

from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.operators.group_a_support import (
    supports_explicit_decimal_sources as supports_group_a,
)
from marivo.analysis.operators.group_a_support import (
    supports_scalar_type,
)


def supported_type(value: str) -> bool:
    """Recognize the logical scalar types admitted by this backend."""
    return supports_scalar_type(value)


def supports(dataset: LogicalDataset) -> bool:
    """Check the complete source closure without opening a datasource."""
    return supports_group_a(dataset, supported_type)
