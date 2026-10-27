"""Structured, input-redacted private compilation failures."""

from marivo.analysis.datasets.errors import DatasetConstructionError


class DatasetCompilationError(DatasetConstructionError):
    """The admitted Dataset cannot be lowered through its registered source recipe."""


def compilation_error(
    expected: str,
    received: str,
    *,
    repair: str = "Use the registered typed graph source recipe and exact declared source tables.",
) -> DatasetCompilationError:
    return DatasetCompilationError(
        expected=expected,
        received=received,
        repair=repair,
        location="dataset.compiler",
    )
