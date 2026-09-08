"""Typed admission and arithmetic errors owned by comparison operators."""

from marivo.analysis.datasets.errors import DatasetConstructionError


class ComparisonError(DatasetConstructionError):
    """Comparison inputs violate one exact construction or numeric contract."""


def comparison_error(expected: str, received: str, *, repair: str | None = None) -> ComparisonError:
    return ComparisonError(
        expected=expected,
        received=received,
        repair=repair
        or "Select compatible single-Metric inputs with matching membership and coordinate contracts.",
        location="operators.compare",
    )


class RowValueError(DatasetConstructionError):
    """An exact retained row key or value violates its comparison contract."""


def row_value_error(expected: str, received: str) -> RowValueError:
    return RowValueError(
        expected=expected,
        received=received,
        repair="Select rows with complete keys and one consistent declared scalar type.",
        location="operators.row_values",
    )
