"""Exact member version selection shared by construction and Ibis lowering."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import ibis
import ibis.expr.types as ir

from marivo._temporal import BeforeEndBoundary
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
    at: datetime | BeforeEndBoundary | None,
    report_timezone: str,
) -> TargetSnapshotSelection | TargetValiditySelection | None:
    """Resolve declared version facts without querying available source rows."""
    if (contract.version is None) != (at is None):
        raise DatasetConstructionError(
            expected="an explicit anchor for versioned Entities and no anchor otherwise",
            received=f"{contract.ref.path}: {at!r}",
            repair="Supply at for this versioned Entity, or remove it for an unversioned Entity.",
            location="analysis.members.version",
        )
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


def select_version(
    table: ir.Table,
    version: TargetSnapshotVersion | TargetValidityVersion | None,
    selected: TargetSnapshotSelection | TargetValiditySelection | None,
) -> ir.Table:
    """Apply exact snapshot or validity comparisons through governed Ibis."""
    if isinstance(version, TargetSnapshotVersion) and isinstance(selected, TargetSnapshotSelection):
        column = table[version.source_column]
        period = date.fromisoformat(selected.period)
        if column.type().is_date():
            return table.filter(column == ibis.literal(period))
        if column.type().is_timestamp():
            from datetime import timedelta

            zone = ZoneInfo(version.timezone or "UTC")
            start = datetime.combine(period, time(), zone)
            end = datetime.combine(period + timedelta(days=1), time(), zone)
            return table.filter((column >= _bound(column, start)) & (column < _bound(column, end)))
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
        return table.filter(left & right)
    if version is None and selected is None:
        return table
    raise DatasetConstructionError(
        expected="matching declared version and exact selection",
        received=f"{type(version).__name__}/{type(selected).__name__}",
        repair="Rebuild the member graph with the declared version's explicit anchor.",
        location="analysis.members.version",
    )
