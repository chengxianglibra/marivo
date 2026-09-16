"""Pure admission for the implemented PostgreSQL scalar Metric closure."""

import re

from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.operators.group_a_support import supports as supports_group_a


def supported_type(value: str) -> bool:
    """Recognize the declared scalar types covered by the PostgreSQL adapter."""
    if value in {
        "boolean",
        "string",
        "int8",
        "int16",
        "int32",
        "int64",
        "float32",
        "float64",
        "date",
        "timestamp",
        "decimal",
    }:
        return True
    decimal = re.fullmatch(r"decimal\(([1-9][0-9]?),\s*([0-9]+)\)", value)
    return decimal is not None and 0 <= int(decimal[2]) <= int(decimal[1]) <= 38


def supports(dataset: LogicalDataset) -> bool:
    """Admit the PostgreSQL scalar closure without opening a datasource."""
    return supports_group_a(dataset, supported_type)
