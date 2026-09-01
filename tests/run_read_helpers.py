"""Small black-box helpers for assertions over public Run values."""

from __future__ import annotations

from typing import Any

from marivo.analysis import FailedRun, IncompleteRun, SucceededRun

PublicRun = IncompleteRun | SucceededRun | FailedRun


def run_arguments(run: PublicRun) -> dict[str, object]:
    """Return the public immutable argument tuple as an assertion-friendly mapping."""
    return {argument.name: argument.value for argument in run.arguments}


def run_queries(
    session: Any,
    *,
    output_ref: str,
) -> tuple[Any, ...]:
    """Return canonical queries for the newest Run yielding one Artifact."""
    cursor: str | None = None
    while True:
        page = session.runs(limit=100, cursor=cursor)
        for run in page.items:
            if isinstance(run, SucceededRun) and run.output_artifact_ref == output_ref:
                return run.queries
        if not page.has_more:
            break
        cursor = page.next_cursor
    raise AssertionError(f"no succeeded Run for output {output_ref!r}")
