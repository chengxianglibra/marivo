"""Pure admission for the implemented mysql Group A closure."""

import re

from marivo.analysis.compiler.normalize import required_entities
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.operators.group_a_support import supports as supports_group_a


def supported_type(value: str) -> bool:
    """Recognize declared scalar types; physical constraints are checked at execution."""
    if value in {
        "string",
        "int8",
        "int16",
        "int32",
        "int64",
        "float32",
        "float64",
        "date",
        "decimal",
    }:
        return True
    decimal = re.fullmatch(r"decimal\(([1-9][0-9]?),\s*([0-9]+)\)", value)
    return decimal is not None and 0 <= int(decimal[2]) <= int(decimal[1]) <= 38


def supports(dataset: LogicalDataset) -> bool:
    """Check the complete source-only dependency closure without source work."""
    return supports_group_a(dataset, supported_type) and all(
        kind != "decimal" for entity in required_entities(dataset) for _, kind in entity.columns
    )
