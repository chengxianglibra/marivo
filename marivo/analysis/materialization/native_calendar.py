"""Evaluate output positions in the owner of the source time operation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass, replace
from datetime import datetime, timezone

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir
import pandas as pd

from marivo._definition.observations import ContributionBinding, ObservationPeriod
from marivo._definition.relations import LiteralPopulation, ObserveQuery
from marivo._definition.types import (
    CalendarCoordinate,
    CalendarOrigin,
    IntervalCoordinate,
    _merge_time_ranges,
)
from marivo._definition.windows import ObservationWindow, ToDate, TrailingPeriods
from marivo._errors import MarivoError
from marivo._temporal import anchor_from_datetime
from marivo._temporal_grid import TimeShiftAbsent, TimeShiftMatch
from marivo._timezones import normalize_timezone
from marivo.analysis.binding.windows import bind_windows
from marivo.analysis.compiler.calendar import builtin_key, builtin_wall_key
from marivo.analysis.core.artifact_data import (
    CalendarDataAxis,
    DataAxis,
    DataSchema,
    NamedCalendarData,
    OutputAttachment,
)
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.temporal_sql import lower_temporal
from marivo.datasource._calendar_relations import calendar_data, calendar_range
from marivo.datasource._temporal_expressions import localize, render, timestamp
from marivo.datasource.adapters import SourceSession
from marivo.semantic._compiled_state import CompiledSemanticState


def _point(value: object) -> datetime:
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    assert isinstance(value, datetime)
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _civil(value: object, point: datetime) -> str:
    local = _point(value).replace(tzinfo=None)
    offset = local - point.replace(tzinfo=None)
    return local.replace(tzinfo=timezone(offset)).isoformat(timespec="microseconds")


def _source_output_calendar(
    coordinate: CalendarCoordinate,
    source: SourceSession,
    windows: tuple[ObservationWindow, ...] = (),
) -> tuple[
    CalendarCoordinate, dict[tuple[ObservationWindow, datetime], datetime], dict[datetime, datetime]
]:
    """Generate S with a scalar range; this neither uploads nor scans facts."""
    if not coordinate.cells:
        return coordinate, {}, {}
    grain = coordinate.grain_spec
    selected_positions: dict[int, tuple[datetime, ...]]
    position_count = 1
    if grain.kind != "builtin":
        # A certified custom selection is one complete civil period.
        positions = ibis.literal(0).name("position").as_table()
        first = datetime.fromisoformat(coordinate.civil_bounds[0][0]).replace(tzinfo=None)
        last = datetime.fromisoformat(coordinate.civil_bounds[0][1]).replace(tzinfo=None)
        start, end = ibis.literal(first), ibis.literal(last)
        selected_positions = {0: tuple(cell[0] for cell in coordinate.cells)}
    else:
        first = datetime.fromisoformat(coordinate.civil_bounds[0][0]).replace(tzinfo=None)
        assert grain.unit is not None and grain.count is not None
        seconds = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800}
        if grain.unit in seconds:
            width = seconds[grain.unit] * grain.count
            interval = ibis.interval(seconds=width)
        else:
            width = {"month": 1, "quarter": 3, "year": 12}[grain.unit]
            interval = ibis.interval(months=width)
        selected_positions = {}
        for cell, civil_bounds in zip(coordinate.cells, coordinate.civil_bounds, strict=True):
            local = datetime.fromisoformat(civil_bounds[0]).replace(tzinfo=None)
            position = (
                int((local - first).total_seconds()) // width
                if grain.unit in seconds
                else ((local.year - first.year) * 12 + local.month - first.month) // width
            )
            selected_positions[position] = (*selected_positions.get(position, ()), cell[0])
        # A selected start survives even when its exclusive end repeats the
        # same civil clock. End-label subtraction would erase that selection.
        position_count = max(selected_positions) + 1
        positions = calendar_range(position_count)
        if coordinate.origin is not None and len(selected_positions) != position_count:
            positions = positions.filter(positions.position.isin(tuple(selected_positions)))
        # ClickHouse reads a zoneless literal in the server zone, so the civil clock
        # is carried under a UTC label there.
        clock_type = (
            dt.Timestamp(timezone="UTC", scale=6)
            if source.provider.name == "clickhouse"
            else dt.Timestamp(scale=6)
        )
        start = ibis.literal(first, type=clock_type) + positions.position * interval
    key = localize(coordinate.timezone, timestamp(start))
    if grain.kind == "builtin":
        key = timestamp(builtin_key(key, coordinate))
        # Keep the native key as a query-local column. The exact range bound
        # makes this LIMIT lossless and prevents repeated clock expressions
        # from being inlined through every endpoint/label on older engines.
        positions = positions.select(positions.position, native_key=key).limit(position_count)
        key = timestamp(positions.native_key)
        # Native gap interpretation can merge nominal candidates. Derive all
        # endpoint parameters from their resulting key, so merged positions
        # describe one window rather than whichever candidate arrived first.
        start = render(coordinate.timezone, key)
        end = start + interval
    finish = localize(coordinate.timezone, timestamp(end))
    requested_start = min(cell[2] for cell in coordinate.cells)
    requested_end = max(cell[3] for cell in coordinate.cells)
    lower = key if grain.kind == "semantic" else ibis.greatest(key, ibis.literal(requested_start))
    upper = finish if grain.kind == "semantic" else ibis.least(finish, ibis.literal(requested_end))
    window_columns: dict[str, ir.Value] = {}
    window_rules: dict[str, ObservationWindow] = {}
    for index, rule in enumerate(windows):
        wall_start: ir.Value | None = None
        if isinstance(rule, ToDate) and rule.grain.kind == "builtin":
            wall_start = builtin_wall_key(timestamp(start), rule.grain)
        elif isinstance(rule, TrailingPeriods) and grain.kind == "builtin":
            wall_start = start - (rule.count - 1) * interval
        if wall_start is not None:
            name = f"window_{index}"
            window_columns[name] = localize(coordinate.timezone, timestamp(wall_start))
            window_rules[name] = rule
    expression = positions.select(
        key=key,
        finish=finish,
        lower=lower,
        upper=upper,
        label=render(coordinate.timezone, key),
        finish_label=render(coordinate.timezone, finish),
        lower_label=render(coordinate.timezone, timestamp(lower)),
        upper_label=render(coordinate.timezone, timestamp(upper)),
        **window_columns,
        selection_position=positions.position,
    )
    expression = lower_temporal(expression, source.provider.name)
    rows = source.collect_bounded(
        expression,
        purpose="analysis.calendar_index",
        max_rows=position_count,
    )
    cells = []
    civil = []
    seen: dict[datetime, tuple[datetime, datetime, datetime]] = {}
    native_starts: dict[tuple[ObservationWindow, datetime], datetime] = {}
    key_mapping: dict[datetime, datetime] = {}
    for row in rows.itertuples(index=False, name=None):
        point, finish_point, low, high = tuple(_point(value) for value in row[:4])
        # A native candidate inside a civil gap need not have a Python-grid
        # counterpart. Only explicit correspondences consume this old-key map.
        for old_key in selected_positions.get(int(row[-1]), ()):
            key_mapping[old_key] = point
        if point in seen:
            if windows and seen[point] != (finish_point, low, high):
                from marivo.analysis.core.model import reject

                reject(
                    "unique native output keys and window parameters",
                    point.isoformat(),
                    "Choose output positions with a one-to-one native endpoint identity.",
                    "calendar.window",
                )
            continue
        seen[point] = (finish_point, low, high)
        for index, rule in enumerate(window_rules.values()):
            native_starts[(rule, point)] = _point(row[8 + index])
        cells.append(
            (
                anchor_from_datetime(point),
                anchor_from_datetime(finish_point),
                anchor_from_datetime(low),
                anchor_from_datetime(high),
            )
        )
        civil.append(
            (
                _civil(row[4], point),
                _civil(row[5], finish_point),
                _civil(row[6], low),
                _civil(row[7], high),
            )
        )
    order = sorted(range(len(cells)), key=lambda index: cells[index][0])
    origin = coordinate.origin
    if origin is not None:
        native_origin, _, origin_mapping = output_calendar(origin.coordinate, source)
        entries = tuple(
            TimeShiftMatch(
                origin_mapping[_point(datetime.fromisoformat(entry.origin_identity))].isoformat(),
                key_mapping[_point(datetime.fromisoformat(entry.target_identity))].isoformat(),
            )
            if isinstance(entry, TimeShiftMatch)
            else TimeShiftAbsent(
                origin_mapping[_point(datetime.fromisoformat(entry.origin_identity))].isoformat(),
                entry.reason,
            )
            for entry in origin.entries
        )
        origin = CalendarOrigin(native_origin, origin.kind, origin.by, entries)
    return (
        replace(
            coordinate,
            cells=tuple(cells[index] for index in order),
            civil_bounds=tuple(civil[index] for index in order),
            origin=origin,
            ranges=_merge_time_ranges(tuple((cell[2], cell[3]) for cell in cells))
            if origin is not None or grain.kind == "semantic"
            else coordinate.ranges,
        ),
        native_starts,
        key_mapping,
    )


def output_calendar(
    coordinate: CalendarCoordinate,
    source: SourceSession,
    windows: tuple[ObservationWindow, ...] = (),
) -> tuple[
    CalendarCoordinate, dict[tuple[ObservationWindow, datetime], datetime], dict[datetime, datetime]
]:
    try:
        return _source_output_calendar(coordinate, source, windows)
    except MarivoError:
        raise
    except Exception as error:
        raise MaterializationError(
            expected=f"{source.provider.name} native calendar evaluation",
            received=f"{type(error).__name__}: {error}",
            repair="Inspect the native cause and choose a range or grain supported by this executor. Retry the explicit observation; no result was completed or published.",
            stage="calendar_execution",
        ) from error


def _pandas_output_calendar(coordinate: CalendarCoordinate) -> CalendarCoordinate:
    """Use pandas for a question without any source time operation."""

    if not coordinate.cells:
        return coordinate
    grain = coordinate.grain_spec
    first = datetime.fromisoformat(coordinate.civil_bounds[0][0]).replace(tzinfo=None)
    if grain.kind == "builtin":
        last = datetime.fromisoformat(coordinate.civil_bounds[-1][0]).replace(tzinfo=None)
        unit, count = grain.unit, grain.count
        assert unit is not None and count is not None
        step = (
            pd.DateOffset(
                seconds={"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800}[
                    unit
                ]
                * count
            )
            if unit in ("second", "minute", "hour", "day", "week")
            else pd.DateOffset(months={"month": 1, "quarter": 3, "year": 12}[unit])
        )
        local = pd.date_range(first, last, freq=step, unit="us")
        if coordinate.origin is not None:
            selected = {
                datetime.fromisoformat(bounds[0]).replace(tzinfo=None)
                for bounds in coordinate.civil_bounds
            }
            local = local[local.isin(selected)]
        finishes = local + step
    else:
        last = datetime.fromisoformat(coordinate.civil_bounds[0][1]).replace(tzinfo=None)
        local = pd.DatetimeIndex([first])
        finishes = pd.DatetimeIndex([last])
    starts = local.tz_localize(normalize_timezone(coordinate.timezone).tz)
    ends = finishes.tz_localize(normalize_timezone(coordinate.timezone).tz)
    requested_start = min(cell[2] for cell in coordinate.cells)
    requested_end = max(cell[3] for cell in coordinate.cells)
    cells = []
    civil = []
    old_by_civil: dict[datetime, tuple[datetime, ...]] = {}
    for old, bounds in zip(coordinate.cells, coordinate.civil_bounds, strict=True):
        wall = datetime.fromisoformat(bounds[0]).replace(tzinfo=None)
        old_by_civil[wall] = (*old_by_civil.get(wall, ()), old[0])
    mapping: dict[datetime, datetime] = {}
    for start, end in zip(starts, ends, strict=True):
        key, finish = (
            anchor_from_datetime(start.to_pydatetime()),
            anchor_from_datetime(end.to_pydatetime()),
        )
        low = key if grain.kind == "semantic" else max(key, requested_start)
        high = finish if grain.kind == "semantic" else min(finish, requested_end)
        cells.append((key, finish, low, high))
        civil.append(
            tuple(
                pd.Timestamp(value)
                .tz_convert(normalize_timezone(coordinate.timezone).tz)
                .isoformat(timespec="microseconds")
                for value in (key, finish, low, high)
            )
        )
        for old_key in old_by_civil[start.tz_localize(None).to_pydatetime()]:
            mapping[old_key] = key
    origin = coordinate.origin
    if origin is not None:
        source = _pandas_output_calendar(origin.coordinate)
        origin_mapping = {
            old[0].isoformat(): new[0].isoformat()
            for old, new in zip(origin.coordinate.cells, source.cells, strict=True)
        }
        entries = tuple(
            TimeShiftMatch(
                origin_mapping[entry.origin_identity],
                mapping[datetime.fromisoformat(entry.target_identity)].isoformat(),
            )
            if isinstance(entry, TimeShiftMatch)
            else TimeShiftAbsent(origin_mapping[entry.origin_identity], entry.reason)
            for entry in origin.entries
        )
        origin = CalendarOrigin(source, origin.kind, origin.by, entries)
    return replace(
        coordinate,
        cells=tuple(cells),
        civil_bounds=tuple((row[0], row[1], row[2], row[3]) for row in civil),
        origin=origin,
        ranges=_merge_time_ranges(tuple((cell[2], cell[3]) for cell in cells))
        if origin is not None or grain.kind == "semantic"
        else coordinate.ranges,
    )


def bind_pandas_calendars(graph: BoundGraph) -> BoundGraph:
    for axis in graph.signature.axes:
        if not isinstance(axis, CalendarCoordinate):
            continue
        try:
            calendar = _pandas_output_calendar(axis)
        except MarivoError:
            raise
        except Exception as error:
            raise MaterializationError(
                expected="pandas native calendar evaluation",
                received=f"{type(error).__name__}: {error}",
                repair="Inspect the pandas cause and choose a range or grain supported by the native operation.",
                stage="calendar_execution",
            ) from error
        rebound = _replace_calendar(graph, axis, calendar)
        assert isinstance(rebound, BoundGraph)
        graph = rebound
    return graph


def _replace_calendar(
    value: object, original: CalendarCoordinate, replacement: CalendarCoordinate
) -> object:
    """Rebind closed graph facts, including CalendarKeys nested in Populations."""
    if isinstance(value, CalendarCoordinate):
        return replacement if value == original else value
    if isinstance(value, LiteralPopulation):
        return value  # Explicit physical keys keep their captured identity.
    if isinstance(value, tuple):
        items = tuple(_replace_calendar(item, original, replacement) for item in value)
        return value if all(a is b for a, b in zip(value, items, strict=True)) else items
    if is_dataclass(value) and not isinstance(value, type):
        changes: dict[str, object] = {}
        for field in fields(value):
            before: object = getattr(value, field.name)
            after = _replace_calendar(before, original, replacement)
            if before is not after:
                changes[field.name] = after
        return replace(value, **changes) if changes else value
    return value


def _interval_windows(
    graph: BoundGraph, state: CompiledSemanticState, sources: dict[str, SourceSession]
) -> BoundGraph:
    observation = graph.signature.observation
    assert observation is not None
    contributions = list(observation.contributions)
    by_owner: dict[SourceSession, list[int]] = {}
    for index, contribution in enumerate(contributions):
        if isinstance(contribution.window_rule, ToDate):
            owner = sources[state.registry.entities[contribution.root.ref.path].datasource]
            by_owner.setdefault(owner, []).append(index)
    for owner, indices in by_owner.items():
        values: dict[str, ir.Value] = {}
        for index in indices:
            contribution = contributions[index]
            rule, period = contribution.window_rule, contribution.period
            assert isinstance(rule, ToDate) and period is not None and period.start is not None
            if rule.grain.kind == "builtin":
                unit = rule.grain.unit
                assert unit is not None
                end = ibis.literal(period.end, type=dt.Timestamp(timezone="UTC", scale=6))
                wall = render(period.timezone, timestamp(end))
                reset = builtin_wall_key(wall, rule.grain)
                step = (
                    ibis.interval(
                        seconds={"second": 1, "minute": 60, "hour": 3600}[unit]
                        * (rule.grain.count or 1)
                    )
                    if unit in ("second", "minute", "hour")
                    else ibis.interval(days={"day": 1, "week": 7}[unit])
                    if unit in ("day", "week")
                    else ibis.interval(months={"month": 1, "quarter": 3, "year": 12}[unit])
                )
                start = (wall == reset).ifelse(reset - step, reset)
            else:
                start = ibis.literal(
                    period.start.astimezone(normalize_timezone(period.timezone).tz).replace(
                        tzinfo=None
                    )
                )
            values[f"window_{index}"] = localize(period.timezone, timestamp(start))
        expression = lower_temporal(
            ibis.literal(0).as_table().select(**values), owner.provider.name
        )
        rows = owner.collect_bounded(expression, purpose="analysis.window_endpoints", max_rows=1)
        for index in indices:
            contribution = contributions[index]
            assert contribution.period is not None
            contributions[index] = replace(
                contribution,
                period=replace(
                    contribution.period,
                    start=anchor_from_datetime(_point(rows[f"window_{index}"].iloc[0])),
                ),
            )
    return replace(
        graph,
        signature=replace(
            graph.signature, observation=replace(observation, contributions=tuple(contributions))
        ),
    )


def bind_native_calendars(
    graph: BoundGraph, state: CompiledSemanticState, sources: dict[str, SourceSession]
) -> BoundGraph:
    """Replace intent positions before binding any actual contribution window."""
    observation = graph.signature.observation
    if observation is None:
        return graph
    original = next(
        (axis for axis in graph.signature.axes if isinstance(axis, CalendarCoordinate)), None
    )
    if original is None:
        return _interval_windows(graph, state, sources)
    by_owner: dict[SourceSession, CalendarCoordinate] = {}
    native_starts_by_owner: dict[
        SourceSession, dict[tuple[ObservationWindow, datetime], datetime]
    ] = {}
    windows = tuple(
        dict.fromkeys(
            item.window_rule for item in observation.contributions if item.window_rule is not None
        )
    )
    snapshots = tuple(
        dict.fromkeys(
            rule.grain._snapshot
            for rule in windows
            if isinstance(rule, ToDate) and rule.grain._snapshot is not None
        )
    )
    for contribution in observation.contributions:
        name = state.registry.entities[contribution.root.ref.path].datasource
        owner = sources[name]
        if owner not in by_owner:
            by_owner[owner], native_starts_by_owner[owner], _ = output_calendar(
                original, owner, windows
            )
    if not by_owner:
        return graph
    positions = {
        cell[0]: (cell, civil)
        for coordinate in by_owner.values()
        for cell, civil in zip(coordinate.cells, coordinate.civil_bounds, strict=True)
    }
    ordered = sorted(positions)
    calendar = replace(
        original,
        cells=tuple(positions[key][0] for key in ordered),
        civil_bounds=tuple(positions[key][1] for key in ordered),
        ranges=_merge_time_ranges(
            tuple(bound for value in by_owner.values() for bound in value.ranges)
        ),
        origin=next(iter(by_owner.values())).origin,
    )
    rebound = _replace_calendar(graph, original, calendar)
    assert isinstance(rebound, BoundGraph)
    graph = rebound
    observation = graph.signature.observation
    assert observation is not None
    if calendar.grain_spec.kind == "semantic" and calendar.ranges:
        observation = replace(
            observation,
            period=ObservationPeriod(
                calendar.ranges[0][0], calendar.ranges[-1][1], calendar.timezone
            ),
        )
    axes = graph.signature.axes
    contributions: list[ContributionBinding] = []
    seen: set[tuple[int, ...]] = set()
    for item in observation.contributions:
        occurrence = item.logical_occurrence or item.occurrence
        if occurrence in seen:
            continue
        seen.add(occurrence)
        owner = sources[state.registry.entities[item.root.ref.path].datasource]
        coordinate = by_owner[owner]
        rebound_contribution = _replace_calendar(item, calendar, coordinate)
        assert isinstance(rebound_contribution, ContributionBinding)
        contribution = replace(
            rebound_contribution,
            occurrence=occurrence,
            logical_occurrence=None,
            window_key=None,
            window_rule=None,
            period=observation.period,
        )
        contributions.extend(
            bind_windows(
                (contribution,),
                graph.root.metrics,
                dict(graph.signature.declared_metrics),
                observation.period,
                coordinate,
                snapshots,
                native_starts=native_starts_by_owner[owner],
            )
            if isinstance(graph.root, ObserveQuery)
            else (contribution,)
        )
    bound = replace(
        observation,
        coordinates=replace(observation.coordinates, axes=axes),
        contributions=tuple(contributions),
    )
    return replace(
        graph,
        root=replace(graph.root, by=axes),
        signature=replace(graph.signature, axes=axes, observation=bound),
    )


def freeze_output_calendars(
    graph: BoundGraph,
    schema: DataSchema,
    rows: pd.DataFrame,
    source: SourceSession | None,
    key_owners: Mapping[str, Mapping[datetime, SourceSession]],
) -> tuple[DataSchema, tuple[OutputAttachment, ...]]:
    """Freeze S∪O after values finish; extra native keys retain their identity."""
    from marivo.analysis.core.frame_calendar import freeze_calendar, freeze_interval

    axes: list[DataAxis] = []
    attachments: list[OutputAttachment] = []
    for coordinate, axis in zip(graph.signature.axes, schema.axes, strict=True):
        if isinstance(coordinate, CalendarCoordinate):
            assert isinstance(axis, CalendarDataAxis) and axis.frozen is not None
            captured = {cell[0] for cell in coordinate.cells}
            extra = sorted({_point(value) for value in rows[axis.columns[0].slot_id]} - captured)
            if extra:
                if source is None:
                    labels = [
                        point.astimezone(normalize_timezone(coordinate.timezone).tz).isoformat(
                            timespec="microseconds"
                        )
                        for point in extra
                    ]
                else:
                    by_owner: dict[SourceSession, list[datetime]] = {}
                    for point in extra:
                        owner = key_owners.get(axis.token, {}).get(point, source)
                        by_owner.setdefault(owner, []).append(point)
                    by_key: dict[datetime, str] = {}
                    for owner, points in by_owner.items():
                        # These are observed mixed-time keys, never a generated S index.
                        keys = calendar_data(
                            ibis.schema({"key": "!timestamp('UTC', 6)"}),
                            tuple((point,) for point in points),
                        )
                        expression = lower_temporal(
                            keys.select(
                                key=keys.key, label=render(coordinate.timezone, timestamp(keys.key))
                            ),
                            owner.provider.name,
                        )
                        returned = owner.collect_bounded(
                            expression, purpose="analysis.calendar_labels", max_rows=len(points)
                        )
                        by_key.update(
                            {
                                _point(key): _civil(label, _point(key))
                                for key, label in returned.itertuples(index=False, name=None)
                            }
                        )
                    labels = [by_key[point] for point in extra]
                extra_cells = tuple((anchor_from_datetime(point),) * 4 for point in extra)
                coordinate = replace(
                    coordinate,
                    cells=(*coordinate.cells, *extra_cells),
                    civil_bounds=(
                        *coordinate.civil_bounds,
                        *((label, label, label, label) for label in labels),
                    ),
                )
            frozen = freeze_calendar(
                coordinate,
                token=axis.token,
                name=axis.name,
                key=axis.columns[0],
                calendar_ref=axis.frozen.definition.ref
                if isinstance(axis.frozen.definition, NamedCalendarData)
                else None,
            )
            axes.append(frozen.axis)
            attachments.extend(frozen.attachments)
        elif isinstance(coordinate, IntervalCoordinate):
            frozen_interval = freeze_interval(
                coordinate, token=axis.token, name=axis.name, key=axis.columns[0]
            )
            axes.append(frozen_interval.axis)
            attachments.append(frozen_interval.directory)
        else:
            axes.append(axis)
    return replace(schema, axes=tuple(axes)), tuple(attachments)
