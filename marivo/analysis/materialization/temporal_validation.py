"""Fail closed when source temporal interpretation differs from runtime ZoneInfo."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone

import ibis
import ibis.expr.types as ir

from marivo.analysis.compiler.source_time import localize, render
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.execution import ExecutionAdapter
from marivo.analysis.materialization.timezone_rules import OffsetInterval, offset_intervals
from marivo.analysis.observation.temporal import SourceTimeAuthority, TemporalExecution, time_zone
from marivo.semantic.validator import Registry, normalize_target_dimension


def _error(axis: str, run_ref: str) -> MaterializationError:
    return MaterializationError(
        expected="unambiguous timestamps and source conversion matching runtime ZoneInfo",
        received=f"temporal.local_time or timezone-rule disagreement: {axis}",
        repair="Use unambiguous source instants and align the source engine timezone data with runtime ZoneInfo before executing again.",
        stage="output_validation",
        run_ref=run_ref,
    )


def _datetime(value: object) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime):
        raise ValueError("invalid temporal range aggregate")
    return (
        value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo is not None else value
    )


def _matches(
    coordinate: ir.TimestampValue,
    converted: ir.TimestampValue,
    intervals: tuple[OffsetInterval, ...],
    *,
    local: bool,
) -> ir.BooleanValue:
    members: list[ir.BooleanValue] = []
    correct: ir.BooleanValue = ibis.literal(False)
    for interval in intervals:
        shift = timedelta(seconds=interval.seconds) if local else timedelta(0)
        member = (coordinate >= ibis.literal(interval.start + shift)) & (
            coordinate < ibis.literal(interval.end + shift)
        )
        expected = coordinate + ibis.interval(
            seconds=-interval.seconds if local else interval.seconds
        )
        members.append(member)
        correct = correct | (member & (converted == expected))
    count = members[0].cast("int32")
    for member in members[1:]:
        count = count + member.cast("int32")
    return ((count == 1) & correct).fill_null(False)


def _validate_axis(
    backend: ExecutionAdapter,
    table: ir.Table,
    value: ir.TimestampValue,
    authority: SourceTimeAuthority,
    *,
    run_ref: str,
) -> None:
    read = authority.read_timezone
    if read is None:
        return
    boundary = authority.boundary_timezone
    if all(time_zone(zone).utcoffset(None) is not None for zone in (read, boundary)):
        return
    utc = (
        render("UTC", value)
        if authority.kind == "instant"
        else render("UTC", localize(read, value))
    )
    coordinate = utc if authority.kind == "instant" else value
    bounds = backend.read_table(
        table.aggregate(low=coordinate.min(), high=coordinate.max()),
        role="engine_check.temporal_range",
    )
    low, high = bounds.column(0)[0].as_py(), bounds.column(1)[0].as_py()
    if low is None and high is None:
        return
    try:
        # A tzinfo offset is strictly less than one day. Padding covers UTC and
        # wall coordinates; only two aggregate cells cross the source boundary.
        start = _datetime(low) - timedelta(days=2)
        end = _datetime(high) + timedelta(days=2)
        valid: ir.BooleanValue = ibis.literal(True)
        if authority.kind == "localizable":
            valid = _matches(value, utc, offset_intervals(time_zone(read), start, end), local=True)
        valid = valid & _matches(
            utc,
            render(boundary, localize("UTC", utc)),
            offset_intervals(time_zone(boundary), start, end),
            local=False,
        )
    except (ValueError, OverflowError, OSError, ImportError, UnicodeError) as cause:
        raise _error(authority.axis, run_ref) from cause
    violations = backend.read_scalar(
        table.filter(value.notnull() & ~valid).count(),
        role="engine_check.temporal_rules",
    )
    if type(violations) is not int or violations != 0:
        raise _error(authority.axis, run_ref)


def validate_temporal_rules(
    backend: ExecutionAdapter,
    execution: TemporalExecution | None,
    tables: Mapping[str, ir.Table],
    registry: Registry,
    *,
    run_ref: str,
) -> None:
    if execution is None:
        return
    for authority in execution.axes:
        if authority.kind == "civil_date":
            continue
        axis = normalize_target_dimension(registry, authority.axis)
        table = tables[axis.entity_ref.path]
        value = table[axis.source_column]
        # C3a governs native timestamps; existing DuckDB string/composite parser
        # paths retain their separately owned validation.
        if isinstance(value, ir.TimestampValue):
            _validate_axis(backend, table, value, authority, run_ref=run_ref)
