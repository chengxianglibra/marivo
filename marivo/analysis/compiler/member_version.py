"""Exact member version selection shared by construction and Ibis lowering."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import ibis
import ibis.expr.types as ir

from marivo._temporal import BeforeEndBoundary
from marivo.analysis.core.time_grid import GridPoint, GridVersionSelection
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.semantic.ir import (
    TargetEntityContract,
    TargetSnapshotSelection,
    TargetSnapshotVersion,
    TargetValiditySelection,
    TargetValidityVersion,
)
from marivo.semantic.validator import normalize_target_version_selection


def selection(
    contract: TargetEntityContract,
    at: datetime | BeforeEndBoundary | GridPoint | None,
    report_timezone: str,
) -> TargetSnapshotSelection | TargetValiditySelection | GridVersionSelection | None:
    """Resolve declared version facts without querying available source rows."""
    if (contract.version is None) != (at is None):
        raise DatasetConstructionError(
            expected="an explicit anchor for versioned Entities and no anchor otherwise",
            received=f"{contract.ref.path}: {at!r}",
            repair="Supply at for this versioned Entity, or remove it for an unversioned Entity.",
            location="analysis.members.version",
        )
    if isinstance(at, GridPoint):
        selections: list[tuple[str, TargetSnapshotSelection | TargetValiditySelection]] = []
        for cell in at.grid.cells:
            anchor = selection(
                contract,
                BeforeEndBoundary(cell.end)
                if at.side == "before_end"
                else cell.start
                if at.side == "start"
                else cell.end,
                report_timezone,
            )
            assert isinstance(anchor, (TargetSnapshotSelection, TargetValiditySelection))
            selections.append((cell.identity, anchor))
        return GridVersionSelection(at.grid.identity, tuple(selections))
    if at is None:
        return None
    if not isinstance(at, (datetime, BeforeEndBoundary)):
        raise DatasetConstructionError(
            expected="datetime or TimeScope.before_end",
            received=type(at).__name__,
            repair="Pass an exact datetime or a scope's typed before_end boundary.",
            location="analysis.members.version",
        )
    if isinstance(at, datetime) and at.tzinfo is None:
        raise DatasetConstructionError(
            expected="an aware datetime instant",
            received=at.isoformat(),
            repair="Attach the intended timezone or use a civil TimeScope.before_end boundary.",
            location="analysis.members.version",
        )
    boundary = at.end if isinstance(at, BeforeEndBoundary) else at
    if not isinstance(boundary, datetime):
        boundary = datetime.combine(boundary, time())
    if boundary.tzinfo is None:
        zone = (
            ZoneInfo(at.boundary_timezone or report_timezone)
            if isinstance(at, BeforeEndBoundary)
            else ZoneInfo(report_timezone)
        )
        localized = boundary.replace(tzinfo=zone)
        from datetime import timezone

        if (
            localized.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != boundary
            or localized.utcoffset() != boundary.replace(tzinfo=zone, fold=1).utcoffset()
        ):
            raise DatasetConstructionError(
                expected="an unambiguous report-local instant",
                received=boundary.isoformat(),
                repair="Supply an aware datetime for a DST fold and correct a nonexistent civil time.",
                location="analysis.members.version",
            )
        boundary = localized
    return normalize_target_version_selection(
        contract,
        boundary=boundary,
        interpretation="before_endpoint" if isinstance(at, BeforeEndBoundary) else "instant",
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


def select_version(
    table: ir.Table,
    version: TargetSnapshotVersion | TargetValidityVersion | None,
    selected: TargetSnapshotSelection | TargetValiditySelection | GridVersionSelection | None,
) -> ir.Table:
    """Read the exact selected version or union of grid-selected versions once."""
    if isinstance(selected, GridVersionSelection):
        from functools import reduce
        from operator import or_

        return table.filter(
            reduce(
                or_,
                (version_predicate(table, version, anchor) for _, anchor in selected.selections),
            )
        )
    predicate = version_predicate(table, version, selected)
    return table.filter(predicate) if version is not None else table
