"""Pure admission for the implemented sqlite Group A closure."""

from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.operators.group_a_support import supports as supports_group_a


def supported_type(value: str) -> bool:
    """Recognize declared scalar types; physical constraints are checked at execution."""
    return value in {"string", "int64", "float64", "date"}


def supports(dataset: LogicalDataset) -> bool:
    """Check the complete source-only dependency closure without source work."""
    return supports_group_a(dataset, supported_type)
