"""Source-free ownership of explicit business completeness on an original grid."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone

from marivo._temporal import TimeScope
from marivo.analysis.core.model import ObservedQuantity
from marivo.analysis.core.time_grid import BoundTimeGrid, TimeCell
from marivo.analysis.datasets.errors import DatasetConstructionError

POLICY = "business_coverage@v1:"
REASON = "insufficient_business_coverage"
Windows = tuple[tuple[str, str], ...]


def invalid(received: str) -> DatasetConstructionError:
    return DatasetConstructionError(
        expected="bounded complete_during scopes with aware UTC-resolvable bounds on the original grid",
        received=received,
        repair="Pass up to 64 absolute time_scope values with aware datetime bounds; keep the complete original grid.",
        location="analysis.business_coverage",
        help_target="dsl.LogicalAnalysisDomain.observe",
    )


def normalize(scopes: tuple[TimeScope, ...], grid: BoundTimeGrid) -> Windows:
    if type(scopes) is not tuple or len(scopes) > 64:
        raise invalid("complete_during must be a tuple of at most 64 scopes")
    spans: list[tuple[datetime, datetime]] = []
    for scope in scopes:
        if (
            not isinstance(scope, TimeScope)
            or scope.kind != "absolute"
            or not isinstance(scope.start, datetime)
            or not isinstance(scope.end, datetime)
            or scope.start.tzinfo is None
            or scope.end.tzinfo is None
        ):
            raise invalid("a non-absolute scope or bounds without an explicit timezone")
        spans.append((scope.start.astimezone(timezone.utc), scope.end.astimezone(timezone.utc)))
    merged: list[tuple[datetime, datetime]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    windows = tuple((start.isoformat(), end.isoformat()) for start, end in merged)
    validate_windows(windows, grid)
    return windows


def validate_windows(windows: Windows, grid: BoundTimeGrid) -> None:
    if type(windows) is not tuple or len(windows) > 64 or not grid.cells:
        raise invalid("missing original grid or unbounded window tuple")
    prior: datetime | None = None
    for span in windows:
        try:
            start, end = (datetime.fromisoformat(value) for value in span)
        except (ValueError, TypeError) as error:
            raise invalid("invalid canonical window bounds") from error
        if (
            len(span) != 2
            or start.utcoffset() != timezone.utc.utcoffset(start)
            or end.utcoffset() != timezone.utc.utcoffset(end)
            or not grid.cells[0].original_start <= start < end <= grid.cells[-1].original_end
            or (prior is not None and start <= prior)
            or span != (start.isoformat(), end.isoformat())
        ):
            raise invalid("windows must be canonical, disjoint UTC spans within the original grid")
        prior = end


def complete(cell: TimeCell, windows: Windows) -> bool:
    return any(
        datetime.fromisoformat(start) <= cell.original_start
        and cell.original_end <= datetime.fromisoformat(end)
        for start, end in windows
    )


def identity(input_quantity: str, windows: Windows) -> str:
    return hashlib.sha256(
        json.dumps((POLICY, input_quantity, windows), separators=(",", ":")).encode()
    ).hexdigest()


def covered_quantity(quantity: ObservedQuantity, windows: Windows) -> ObservedQuantity:
    return replace(
        quantity,
        definition_id=identity(quantity.definition_id, windows),
        value_policy=POLICY + quantity.definition_id,
    )
