"""Lower exact member version predicates through Ibis."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import ibis
import ibis.expr.types as ir

from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.semantic.ir import (
    TargetSnapshotSelection,
    TargetSnapshotVersion,
    TargetValiditySelection,
    TargetValidityVersion,
)


def _bound(column: ir.Value, boundary: datetime) -> ir.Value:
    dtype = column.type()
    if dtype.is_date():
        if boundary.timetz().replace(tzinfo=None) != time():
            return ibis.literal(boundary.replace(tzinfo=None))
        return ibis.literal(boundary.date())
    if dtype.is_timestamp():
        if dtype.timezone is None:
            boundary = boundary.replace(tzinfo=None)
        return ibis.literal(boundary).cast(dtype)
    raise DatasetConstructionError(
        expected="a native date or timestamp version axis",
        received=str(dtype),
        repair="Declare a native temporal version axis with explicit timezone authority.",
        location="analysis.members.version",
    )


def version_predicate(
    table: ir.Table,
    version: TargetSnapshotVersion | TargetValidityVersion | None,
    selected: TargetSnapshotSelection | TargetValiditySelection | None,
) -> ir.BooleanValue:
    """Build exact snapshot or symbolic validity predicates through Ibis."""
    if isinstance(version, TargetSnapshotVersion) and isinstance(selected, TargetSnapshotSelection):
        column = table[version.source_column]
        period = date.fromisoformat(selected.period)
        if column.type().is_date():
            return column == ibis.literal(period)
        if column.type().is_timestamp():
            from datetime import timedelta

            zone = ZoneInfo(version.timezone or "UTC")
            start = datetime.combine(period, time(), zone)
            end = datetime.combine(period + timedelta(days=1), time(), zone)
            return (column >= _bound(column, start)) & (column < _bound(column, end))
        raise DatasetConstructionError(
            expected="a native temporal snapshot coordinate",
            received=str(column.type()),
            repair="Use a native date or timestamp snapshot coordinate.",
            location="analysis.members.version",
        )
    if isinstance(version, TargetValidityVersion) and isinstance(selected, TargetValiditySelection):
        start, end = table[version.valid_from_column], table[version.valid_to_column]
        boundary = datetime.fromisoformat(selected.boundary)
        left = (
            start < _bound(start, boundary)
            if selected.start_operator == "lt"
            else start <= _bound(start, boundary)
        )
        right = (
            end > _bound(end, boundary)
            if selected.end_operator == "gt"
            else end >= _bound(end, boundary)
        )
        for sentinel in selected.open_end:
            right = right | (
                end.isnull() if sentinel is None else end == ibis.literal(sentinel).cast(end.type())
            )
        return left & right
    if version is None and selected is None:
        return ibis.literal(True)
    raise DatasetConstructionError(
        expected="matching declared version and exact selection",
        received=f"{type(version).__name__}/{type(selected).__name__}",
        repair="Rebuild the member graph with the declared version's explicit anchor.",
        location="analysis.members.version",
    )
