"""Compose source buckets over the exact half-open periods bound to a report."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from itertools import pairwise
from typing import Literal

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir
from ibis.expr.builders import WindowBuilder

from marivo._definition.observations import WindowGroup
from marivo._definition.windows import AllHistory, Since, ToDate, TrailingDuration, TrailingPeriods
from marivo._kernel.timezones import normalize_timezone
from marivo.datasource.spi import calendar_data
from marivo.semantic.spi import NumericValue, force_cast, native_mean, numeric_binary

_ORDINAL = "__window_ordinal"
_RESET = "__window_reset"
_REPORT = "__window_report"
_ROWS = "__window_rows"
_VALID = "__window_valid"
_VALUE = "__window_value"
_WEIGHT = "__window_weight"
Reducer = Literal["entity_count", "count", "min", "max", "sum", "mean", "weighted_mean"]


@dataclass(frozen=True, slots=True)
class DailyWindowGrid:
    """Dense day ordinals, with the earlier cumulative prefix at ordinal zero."""

    first: date
    end: date
    start: date | None
    prefix: bool
    preceding: int | None
    rows: tuple[tuple[int, int, datetime | None], ...]
    periods: tuple[tuple[datetime, date, date | None], ...]
    unused: tuple[tuple[date, date], ...]


@dataclass(frozen=True, slots=True)
class WindowReduction:
    reducer: Reducer
    field: str | None
    value_slot: str
    check_slot: str | None
    weight: str | None = None
    sum_type: dt.Decimal | None = None


def _midnight(value: datetime, zone: tzinfo) -> date | None:
    if value.utcoffset() is None:
        return None
    local = value.astimezone(zone)
    return local.date() if local.time() == time.min else None


def _report_days(
    group: WindowGroup,
    timezone: str,
    zone: tzinfo,
) -> tuple[tuple[datetime, date, date | None], ...] | None:
    reports: list[tuple[datetime, date, date | None]] = []
    for endpoint in group.endpoint_grid:
        key, period = endpoint.key, endpoint.period
        if not isinstance(key, datetime) or key.utcoffset() is None or period.timezone != timezone:
            return None
        end = _midnight(period.end, zone)
        start = None if period.start is None else _midnight(period.start, zone)
        if end is None or end == date.min:
            return None
        day = end - timedelta(days=1)
        if (period.start is not None and (start is None or start > day)) or (
            reports and (day <= reports[-1][1] or key <= reports[-1][0])
        ):
            return None
        reports.append((key, day, start))
    return tuple(reports) if reports else None


def _reset_days(
    reports: tuple[tuple[datetime, date, date | None], ...],
) -> frozenset[date] | None:
    previous = reports[0][2]
    resets: set[date] = set()
    for (_, earlier, _), (_, day, begin) in pairwise(reports):
        if begin is None:
            return None
        if begin != previous:
            if not earlier < begin <= day:
                return None
            resets.add(begin)
            previous = begin
    return frozenset(resets) if reports[0][2] is not None else None


def daily_window_grid(group: WindowGroup) -> DailyWindowGrid | None:
    """Prove the bound periods match native frames on complete civil DATE cells."""
    calendar = group.report_calendar
    if calendar is None:
        return None
    zone = normalize_timezone(calendar.timezone).tz
    reports = _report_days(group, calendar.timezone, zone)
    if reports is None:
        return None
    rule = group.window_rule
    first_report = reports[0][1]
    start = reports[0][2]
    last = reports[-1][1]
    if isinstance(rule, (TrailingPeriods, TrailingDuration)):
        if start is None:
            return None
        preceding = (first_report - start).days
        if any(begin is None or (day - begin).days != preceding for _, day, begin in reports):
            return None
        if any(
            next_start is None or (next_start - day).days > 1
            for (_, day, _), (_, _, next_start) in pairwise(reports)
        ):
            return None
        return _dense_grid(reports, start, last, start, preceding, zone, prefix=False)
    if isinstance(rule, (AllHistory, Since)):
        if any(begin != start for _, _, begin in reports):
            return None
        return _dense_grid(reports, first_report, last, start, None, zone, prefix=True)
    if isinstance(rule, ToDate):
        resets = _reset_days(reports)
        if resets is not None:
            return _dense_grid(
                reports, first_report, last, start, None, zone, prefix=True, resets=resets
            )
    return None


def _unique_midnight(day: date, zone: tzinfo) -> bool:
    wall = datetime.combine(day, time.min, tzinfo=zone)
    # Both ambiguous and nonexistent midnights may map differently in native
    # time conversion. Neither can be replaced by the physical DATE order.
    return wall.utcoffset() == wall.replace(fold=1).utcoffset()


def _stable_cut(day: date, zone: tzinfo) -> bool:
    ordinal = day.toordinal()
    return all(
        _unique_midnight(date.fromordinal(value), zone)
        for value in range(max(1, ordinal - 2), min(date.max.toordinal(), ordinal + 2) + 1)
    )


def _dense_grid(
    reports: tuple[tuple[datetime, date, date | None], ...],
    first: date,
    last: date,
    start: date | None,
    preceding: int | None,
    zone: tzinfo,
    *,
    prefix: bool,
    resets: frozenset[date] = frozenset(),
) -> DailyWindowGrid | None:
    end = last + timedelta(days=1)
    # Native localization of nearby days can cross the first or last cut.
    # A compressed earlier prefix needs only the cut and its neighbors.
    cuts = (first, end) if start is None else (first, end, start)
    if not all(_stable_cut(day, zone) for day in cuts):
        return None
    positions = {day: key for key, day, _ in reports}
    rows: list[tuple[int, int, datetime | None]] = [(0, 0, None)] if prefix else []
    reset = 0
    for ordinal in range((last - first).days + 1):
        day = first + timedelta(days=ordinal)
        if not _unique_midnight(day, zone):
            return None
        if day in resets:
            reset += 1
        rows.append((ordinal + int(prefix), reset, positions.get(day)))
    unused = tuple(
        (prior + timedelta(days=1), current_start)
        for (_, prior, _), (_, _, current_start) in pairwise(reports)
        if current_start is not None and (current_start - prior).days > 1
    )
    return DailyWindowGrid(first, end, start, prefix, preceding, tuple(rows), reports, unused)


def _nullable_cell(value: ir.Value, kept: ir.BooleanValue) -> ir.Value:
    return kept.ifelse(value, ibis.null().cast(value.type().copy(nullable=True)))


def _bucket_metrics(table: ir.Table, spec: WindowReduction) -> dict[str, ir.Value]:
    metrics: dict[str, ir.Value] = {_ROWS: table.count()}
    if spec.field is None:
        return metrics
    value = table[spec.field]
    if spec.weight is not None:
        weight = table[spec.weight]
        paired = value.notnull() & weight.notnull()
        product = numeric_binary("multiply", NumericValue(value), NumericValue(weight)).value
        metrics[_VALID] = _nullable_cell(value, paired).count()
        metrics[_VALUE] = _nullable_cell(product, paired).sum()
        metrics[_WEIGHT] = _nullable_cell(weight, paired).sum()
        return metrics
    metrics[_VALID] = value.count()
    if spec.reducer == "sum":
        total = value.sum()
        metrics[_VALUE] = total if spec.sum_type is None else force_cast(total, spec.sum_type)
    elif spec.reducer == "max":
        metrics[_VALUE] = value.max()
    elif spec.reducer in ("min", "mean"):
        # Mean is admitted only when the complete source identity is retained
        # in the ordinary keys and at most one value can enter each frame.
        metrics[_VALUE] = value.min()
    return metrics


def _range_day_ordinals(source: ir.Table, clock: str, grid: DailyWindowGrid) -> ir.Table:
    """Use only the same DATE comparisons as the original window boundaries."""
    lower = "__window_lower_date"
    upper = "__window_upper_date"
    rows: list[tuple[int, date | None, date]] = [(0, grid.start, grid.first)] if grid.prefix else []
    for ordinal in range((grid.end - grid.first).days):
        begin = grid.first + timedelta(days=ordinal)
        rows.append((ordinal + int(grid.prefix), begin, begin + timedelta(days=1)))
    directory = calendar_data(
        ibis.schema({_ORDINAL: "!int64", lower: "date", upper: "!date"}), tuple(rows)
    )
    day = source[clock]
    return source.inner_join(
        directory, (directory[lower].isnull() | (day >= directory[lower]), day < directory[upper])
    ).select(*tuple(source[name] for name in source.columns), directory[_ORDINAL])


def _daily_states(
    table: ir.Table,
    clock: str,
    keys: tuple[str, ...],
    grid: DailyWindowGrid,
    spec: WindowReduction,
    *,
    range_ordinals: bool,
) -> ir.Table:
    clock_value = table[clock]
    assert isinstance(clock_value, ir.DateValue)
    admitted = table.filter(
        clock_value < ibis.literal(grid.end),
        *(() if grid.start is None else (clock_value >= ibis.literal(grid.start),)),
        *(
            (clock_value < ibis.literal(lo)) | (clock_value >= ibis.literal(hi))
            for lo, hi in grid.unused
        ),
    )
    if range_ordinals:
        indexed = _range_day_ordinals(admitted, clock, grid)
    else:
        indexed = _date_difference_ordinals(admitted, clock, grid)
    return indexed.group_by(*keys, _ORDINAL).aggregate(**_bucket_metrics(indexed, spec))


def _date_difference_ordinals(table: ir.Table, clock: str, grid: DailyWindowGrid) -> ir.Table:
    day = table[clock]
    assert isinstance(day, ir.DateValue)
    first = ibis.literal(grid.first)
    offset: ir.IntegerValue
    if grid.prefix:
        prefix = day < first
        bounded = prefix.ifelse(first, day)
        assert isinstance(bounded, ir.DateValue)
        offset = prefix.ifelse(0, bounded.delta(first, unit="day") + 1)
    else:
        offset = day.delta(first, unit="day")
    return table.mutate(**{_ORDINAL: offset.cast("int64")})


def _complete_days(states: ir.Table, keys: tuple[str, ...], grid: DailyWindowGrid) -> ir.Table:
    directory = calendar_data(
        ibis.schema(
            {_ORDINAL: "!int64", _RESET: "!int64", _REPORT: dt.Timestamp(timezone="UTC", scale=6)}
        ),
        grid.rows,
    )
    domain = directory.cross_join(states.select(*keys).distinct()) if keys else directory
    values = states.view()
    joined = domain.left_join(
        values,
        (
            domain[_ORDINAL] == values[_ORDINAL],
            *(domain[key].identical_to(values[key]) for key in keys),
        ),
    )
    return joined.select(
        *tuple(domain[name] for name in domain.columns),
        *tuple(values[name] for name in (_ROWS, _VALID, _VALUE, _WEIGHT) if name in values.columns),
    )


def _merge_window_values(
    days: ir.Table,
    window: WindowBuilder,
    spec: WindowReduction,
) -> ir.Value:
    cell = _nullable_cell(days[_VALUE], days[_VALID].fill_null(0) > 0)
    if spec.reducer == "min":
        return cell.min().over(window)
    if spec.reducer == "max":
        return cell.max().over(window)
    if spec.reducer == "mean":
        return native_mean(cell).over(window)
    numerator = cell.sum().over(window)
    if spec.weight is not None:
        weight = _nullable_cell(days[_WEIGHT], days[_VALID].fill_null(0) > 0)
        denominator = weight.sum().over(window)
        return numeric_binary(
            "divide",
            NumericValue(numerator.fill_null(0)),
            NumericValue(denominator.fill_null(0)),
        ).value
    return numerator if spec.sum_type is None else force_cast(numerator, spec.sum_type)


def lower_daily_window(
    source: ir.Table,
    clock: str,
    keys: tuple[str, ...],
    calendar_key: str,
    grid: DailyWindowGrid,
    spec: WindowReduction,
    *,
    range_ordinals: bool = False,
) -> ir.Table:
    """Aggregate each civil day once and merge only the original report intervals."""
    states = _daily_states(source, clock, keys, grid, spec, range_ordinals=range_ordinals)
    days = _complete_days(states, keys, grid)
    window = ibis.rows_window(
        preceding=grid.preceding,
        following=0,
        group_by=[days[name] for name in (*keys, _RESET)],
        order_by=days[_ORDINAL],
    )
    rows = days[_ROWS].fill_null(0).sum().over(window).cast("int64")
    projection = {name: days[name] for name in keys}
    projection[calendar_key] = days[_REPORT]
    value: ir.Value = rows
    if spec.field is not None:
        valid = days[_VALID].fill_null(0).sum().over(window).cast("int64")
        value = (
            valid
            if spec.reducer == "count"
            else _nullable_cell(_merge_window_values(days, window, spec), valid > 0)
        )
        if spec.check_slot is not None:
            projection[spec.check_slot] = rows != valid
    projection[spec.value_slot] = value
    result = days.select(**projection, **{_ROWS: rows})
    return result.filter(result[calendar_key].notnull(), result[_ROWS] > 0).drop(_ROWS)
