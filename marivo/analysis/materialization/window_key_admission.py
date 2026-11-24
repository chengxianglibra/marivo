"""Prove a native calendar branch retains its captured physical DATE period."""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from operator import itemgetter

import ibis.expr.operations as ops

from marivo._definition.types import CalendarCoordinate
from marivo.analysis.compiler.query_stages import PeriodFilter, ReductionRead, SourceReadStage
from marivo.analysis.materialization.date_day_keys import physical_day_key
from marivo.datasource.spi import localize, profile_for_backend_type, timestamp


def _origin(value: ops.Value) -> ops.Value:
    while isinstance(value, ops.Field):
        relation = value.rel
        if isinstance(relation, (ops.Reference, ops.Filter)):
            value = ops.Field(relation.parent, value.name)
        elif isinstance(relation, ops.Project):
            value = relation.values[value.name]
        else:
            break
    return value


@dataclass(frozen=True, eq=False, slots=True)
class _DateWindow:
    field: ops.Field
    first: date
    last: date


def _physical_period(predicate: ops.Filter, period: PeriodFilter) -> _DateWindow | None:
    if (
        period.start is None
        or period.start.utcoffset() is None
        or period.end.utcoffset() is None
        or period.timezone != "UTC"
        or len(predicate.predicates) != 2
    ):
        return None
    bounds = (period.start.astimezone(timezone.utc), period.end.astimezone(timezone.utc))
    if any(bound.time() != time.min for bound in bounds) or bounds[0] >= bounds[1]:
        return None
    lower, upper = predicate.predicates
    if not isinstance(lower, ops.GreaterEqual) or not isinstance(upper, ops.Less):
        return None
    clock = _origin(lower.left)
    if not (
        isinstance(clock, ops.Field)
        and isinstance(clock.rel, ops.DatabaseTable)
        and clock.dtype.is_date()
        and upper.left == lower.left
        and isinstance(lower.right, ops.Literal)
        and isinstance(upper.right, ops.Literal)
        and lower.right.dtype.is_date()
        and upper.right.dtype.is_date()
        and (lower.right.value, upper.right.value) == tuple(bound.date() for bound in bounds)
    ):
        return None
    if not isinstance(lower.left, ops.Field) or lower.left.rel != predicate.parent:
        return None
    return _DateWindow(ops.Field(predicate, lower.left.name), bounds[0].date(), bounds[1].date())


def _matching_range(expression: ops.Value, clock: ops.Value, first: date, last: date) -> bool:
    if not isinstance(expression, ops.And):
        return False
    lower, upper = expression.left, expression.right
    return (
        isinstance(lower, ops.GreaterEqual)
        and isinstance(upper, ops.Less)
        and _origin(lower.left) == clock
        and _origin(upper.left) == clock
        and isinstance(lower.right, ops.Literal)
        and isinstance(upper.right, ops.Literal)
        and lower.right.value == first
        and upper.right.value == last
        and lower.right.dtype == upper.right.dtype == clock.dtype
    )


def _period_projection(
    value: ops.Value,
    interval: _DateWindow,
    clock: ops.Value,
    calendar: CalendarCoordinate,
    endpoint: ops.Value,
) -> bool:
    if isinstance(value, ops.Field) or value in (endpoint, clock):
        return True
    if isinstance(value, ops.Cast):
        return (
            isinstance(value.arg, ops.Literal)
            and value.arg.value is None
            and value.to == clock.dtype
        )
    if isinstance(value, ops.And):
        return _matching_range(value, _origin(interval.field), interval.first, interval.last)
    if isinstance(value, ops.IfElse) and len(calendar.ranges) == 1:
        first, last = calendar.ranges[0]
        return (
            _origin(value.true_expr) == clock
            and isinstance(value.false_null_expr, ops.Literal)
            and value.false_null_expr.value is None
            and _matching_range(value.bool_expr, clock, first, last)
        )
    return False


def _window_endpoint(value: ops.Value, calendar: CalendarCoordinate, point: datetime) -> bool:
    cells = calendar.cells
    position = bisect_left(cells, point, key=itemgetter(0))
    return (
        position < len(cells)
        and cells[position][0] == point
        and isinstance(value, ops.Literal)
        and value.dtype.is_timestamp()
        and value.dtype.timezone == "UTC"
        and value.dtype.scale == 6
        and value.value == point
    )


def qualified_calendar_group_slot(aggregate: ops.Aggregate, stage: SourceReadStage) -> str | None:
    """Identify an exact UTC day or window endpoint over its stored DATE period."""
    if not isinstance(stage.program, ReductionRead) or stage.period is None:
        return None
    reduction = stage.program.reduction
    if len(reduction.calendars) != 1:
        return None
    slot, calendar = reduction.calendars[0]
    if not (
        isinstance(calendar, CalendarCoordinate)
        and calendar.timezone == "UTC"
        and not reduction.window_coordinates
        and not reduction.sampled
        and not reduction.sample_calendar_reads
        and isinstance(group := aggregate.groups.get(slot), ops.Field)
    ):
        return None
    endpoint = _origin(group)
    filters = aggregate.parent.find(ops.Filter)
    if len(filters) != 1 or (interval := _physical_period(filters[0], stage.period)) is None:
        return None
    # The only DATE-to-timestamp conversion is directly above the proved
    # filter. A native DATE outside the timestamp range cannot reach it.
    profile = profile_for_backend_type(reduction.provider)
    if profile is None:
        return None
    first_year, last_year = profile.dialect.timezone_endpoint_years
    if not first_year <= interval.first.year <= interval.last.year <= last_year:
        return None
    clock = localize("UTC", timestamp(interval.field.to_expr())).op()
    if not (
        reduction.time_column is not None
        and dict(stage.field_columns).get(reduction.time_column) == stage.period.column
        and stage.period.column in aggregate.parent.schema
        and _origin(ops.Field(aggregate.parent, stage.period.column)) == clock
    ):
        return None
    if isinstance(reduction.window_key, datetime) and reduction.calendar == calendar:
        if not _window_endpoint(endpoint, calendar, reduction.window_key):
            return None
    elif reduction.window_key is None and calendar.grain == "day":
        raw = physical_day_key(endpoint, reduction.provider)
        if raw is None or _origin(raw) != _origin(interval.field):
            return None
    else:
        return None
    for relation in aggregate.parent.find(ops.Relation):
        if isinstance(relation, (ops.Reference, ops.Filter, ops.DatabaseTable)):
            continue
        if not isinstance(relation, ops.Project) or any(
            not _period_projection(value, interval, clock, calendar, endpoint)
            for value in relation.values.values()
        ):
            return None
    return slot
