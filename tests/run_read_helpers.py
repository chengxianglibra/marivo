"""Small black-box helpers for assertions over public Run values."""

from __future__ import annotations

from marivo.analysis import FailedRun, IncompleteRun, SucceededRun

PublicRun = IncompleteRun | SucceededRun | FailedRun


def run_arguments(run: PublicRun) -> dict[str, object]:
    """Return the public immutable argument tuple as an assertion-friendly mapping."""
    return {argument.name: argument.value for argument in run.arguments}


def run_queries(run: PublicRun) -> list[object]:
    """Return sanitized query projections carried by a public Run."""
    value = run_arguments(run).get("__queries", [])
    assert isinstance(value, list)
    return value
