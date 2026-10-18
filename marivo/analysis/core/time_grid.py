"""Analysis error adaptation for the shared temporal instant kernel."""

from __future__ import annotations

from datetime import date, datetime

from marivo._temporal_grid import instant as _instant
from marivo.analysis.datasets.errors import DatasetConstructionError


def _invalid(received: str) -> DatasetConstructionError:
    return DatasetConstructionError(
        expected="finite half-open time cells with exact timezone and calendar authority",
        received=received,
        repair="Bind the Session report timezone and the exact certified calendar; choose wholly contained cells.",
        location="analysis.time_grid",
    )


def instant(value: date | datetime, zone: str) -> datetime:
    """Resolve a boundary through the shared pure kernel with Analysis repair."""
    try:
        return _instant(value, zone)
    except ValueError as error:
        raise _invalid(str(error)) from error
