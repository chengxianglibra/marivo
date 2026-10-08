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


class AttributionError(DatasetConstructionError):
    """Attribution lacks exact partition authority or complete reconciled values."""


def attribution_error(
    expected: str, received: str, *, repair: str | None = None
) -> AttributionError:
    return AttributionError(
        expected=expected,
        received=received,
        repair=repair
        or "Use a single Metric with exact additive component and partition contracts for the requested axes.",
        location="operators.attribute",
    )


class ForecastError(DatasetConstructionError):
    """A named forecast lacks certified coordinates or finite numerical authority."""


def forecast_error(expected: str, received: str) -> ForecastError:
    return ForecastError(
        expected=expected,
        received=received,
        repair="Use a positive periods horizon and a supported forecast model; inspect the NumericRelation contract for complete history and future-grid requirements.",
        location="numeric.forecast",
    )
