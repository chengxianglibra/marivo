"""Assign captured report and sample coordinates after scalar field projection."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from itertools import pairwise

import ibis
import ibis.expr.types as ir

from marivo._definition.expressions import BuiltinSampleCoordinate
from marivo._definition.observation_time import ObservationPeriod
from marivo._definition.observations import SampleIntervalRanges
from marivo._definition.types import (
    CalendarCoordinate,
    FieldRead,
    IntervalCoordinate,
    field_read_key,
)
from marivo.analysis.binding.windows import validate_date_contribution
from marivo.analysis.compiler.calendar import bind_calendar
from marivo.analysis.compiler.observation import calendar_field
from marivo.analysis.compiler.source_time import TimePredicateUse, entity_engine
from marivo.analysis.materialization.date_day_keys import deferred_day_key
from marivo.analysis.materialization.population_condition import (
    membership_time_axes,
    population_time_ranges,
)
from marivo.analysis.materialization.query_field_scope import QueryFieldScope
from marivo.analysis.model.time_authority import SourceTimeAuthority
from marivo.analysis.model.time_facts import capture_source_times
from marivo.datasource.spi import calendar_range, localize, timestamp
from marivo.semantic.spi import (
    StrptimeParse,
    TargetDimensionContract,
    normalize_target_entity,
)


@dataclass(slots=True)
class QueryCalendarProjection:
    scope: QueryFieldScope
    fixed_time_slots: dict[str, int | datetime]
    sample_slots: set[str]
    sample_intervals: dict[str, SampleIntervalRanges]
    selected: tuple[CalendarCoordinate | IntervalCoordinate, ...]
    selected_owns_period: bool
    pending_calendars: list[tuple[str, str, str, CalendarCoordinate]] = field(default_factory=list)
    pending_intervals: list[tuple[str, tuple[tuple[int, str], ...], bool]] = field(
        default_factory=list
    )
    day_clocks: dict[str, tuple[ir.Value, TargetDimensionContract, SourceTimeAuthority]] = field(
        default_factory=dict
    )

    @classmethod
    def capture(cls, scope: QueryFieldScope) -> QueryCalendarProjection:
        contribution = scope.contribution
        fixed_time_slots = dict(contribution.window_coordinates)
        # Internal time selection qualifies sample rows without removing parent keys.
        sample_time_slots = set(contribution.sample_time_slots) - fixed_time_slots.keys()
        sample_intervals = dict(contribution.sample_interval_ranges)
        # Calendar membership is independent of the enclosing source read period.
        # Window bindings own their contribution period instead of the output cells.
        selected_calendars = tuple(
            calendar
            for key, calendar in contribution.calendars
            if key not in fixed_time_slots
            and key not in sample_time_slots
            and not isinstance(calendar, BuiltinSampleCoordinate)
            and not (isinstance(calendar, CalendarCoordinate) and calendar.actual_only)
            and (contribution.window_key is None or calendar in contribution.sample_axes)
        )
        selected_owns_period = contribution.period is not None and any(
            all(
                (contribution.period.start is None or contribution.period.start <= start)
                and end <= contribution.period.end
                for start, end in (
                    calendar.ranges
                    if isinstance(calendar, CalendarCoordinate)
                    else ((calendar.start, calendar.end),)
                )
            )
            for calendar in selected_calendars
        )
        return cls(
            scope,
            fixed_time_slots,
            sample_time_slots,
            sample_intervals,
            selected_calendars,
            selected_owns_period,
        )

    def filter_root(self, table: ir.Table) -> ir.Table:
        contribution = self.scope.contribution
        if (
            self.selected
            and contribution.time is not None
            and not contribution.time.path
            and contribution.time.at is None
        ):
            time = self.scope.value(contribution.time.field.ref.path, table)
            table = table.filter(self._selected_time(time, use="filter"))
        if (
            contribution.period is not None
            and not self.selected_owns_period
            and contribution.time is not None
            and not contribution.time.path
            and contribution.time.at is None
        ):
            time = self.scope.value(contribution.time.field.ref.path, table)
            table = table.filter(
                self.scope.period_condition(
                    time,
                    contribution.period.start,
                    contribution.period.end,
                    self.scope.zone,
                    use="filter",
                )
            )
        return table

    def filter_routed_clock(
        self, table: ir.Table, read: FieldRead, columns: dict[str, str]
    ) -> ir.Table:
        if not read.path and read.at is None:
            return table
        if self.selected:
            table = table.filter(table[columns["__time_selected"]])
        if self.scope.contribution.period is not None and not self.selected_owns_period:
            table = table.filter(table[columns["__time_period"]])
        return table

    def _selected_time(self, time: ir.Value, *, use: TimePredicateUse = "value") -> ir.BooleanValue:
        # Boolean literal seeds can produce incompatible Bool columns on older ClickHouse builds.
        condition: ir.BooleanValue | None = None
        for calendar in self.selected:
            intervals = (
                calendar.ranges
                if isinstance(calendar, CalendarCoordinate)
                else ((calendar.start, calendar.end),)
            )
            member: ir.BooleanValue | None = None
            for left, right in intervals:
                predicate = self.scope.period_condition(
                    time, left, right, calendar.timezone, use=use
                )
                member = predicate if member is None else member | predicate
            if member is None:
                return ibis.literal(False)
            condition = member if condition is None else condition & member
        return ibis.literal(True) if condition is None else condition

    def project_clock(
        self, read: FieldRead, read_key: str, expression: ir.Value
    ) -> tuple[dict[str, ir.Value], dict[str, str]]:
        """Emit the markers needed to assign and filter a bound contribution clock."""
        contribution = self.scope.contribution
        self._validate_membership_dates(read)
        extras: dict[str, ir.Value] = {}
        extra_keys: dict[str, str] = {}
        for key, calendar in contribution.calendars:
            if isinstance(calendar, BuiltinSampleCoordinate):
                continue
            name = self.scope.unique("__bucket_" + key.replace(".", "_"))
            if key in self.fixed_time_slots:
                point = self.fixed_time_slots[key]
                extras[name] = ibis.literal(
                    point, type="int64" if isinstance(point, int) else "timestamp('UTC', 6)"
                )
            elif isinstance(calendar, CalendarCoordinate):
                extras[name] = ibis.null().cast("timestamp('UTC', 6)")
                self.pending_calendars.append((key, name, read_key, calendar))
                encoding = self.scope.time_encodings.get(read.field.ref.path)
                if (
                    encoding is not None
                    and encoding[2].kind != "instant"
                    and (
                        encoding[0].type().is_date()
                        or (
                            isinstance(encoding[1].parse, StrptimeParse)
                            and encoding[1].parse.format in ("%Y%m%d", "%Y-%m-%d")
                        )
                    )
                ):
                    self.day_clocks[name] = encoding[:3]
            elif key in self.sample_intervals:
                ranges = self.sample_intervals[key]
                flags = []
                for ordinal, start, end in ranges:
                    flag = self.scope.unique(f"{name}_contains_{ordinal}")
                    extras[flag] = self.scope.period_condition(
                        expression, start, end, calendar.timezone
                    ).fill_null(False)
                    flags.append((ordinal, flag))
                if flags:
                    ordered = sorted((start, end) for _, start, end in ranges)
                    overlaps = any(
                        end > next_start for (_, end), (next_start, _) in pairwise(ordered)
                    )
                    self.pending_intervals.append((name, tuple(flags), overlaps))
                extras[name] = ibis.null().cast("int64")
            else:
                extras[name] = self.scope.period_condition(
                    expression, calendar.start, calendar.end, calendar.timezone
                ).ifelse(ibis.literal(calendar.report_key).cast("!int64"), ibis.null())
            extra_keys[name] = calendar_field(key)
        if self.selected:
            name = self.scope.unique("__time_selected")
            extras[name] = self._selected_time(expression)
            extra_keys[name] = "__time_selected"
        if contribution.period is not None:
            name = self.scope.unique("__time_period")
            extras[name] = self.scope.period_condition(
                expression, contribution.period.start, contribution.period.end, self.scope.zone
            )
            extra_keys[name] = "__time_period"
        return extras, extra_keys

    def _validate_membership_dates(self, read: FieldRead) -> None:
        contribution = self.scope.contribution
        axes = membership_time_axes(contribution)
        encoding = self.scope.time_encodings.get(read.field.ref.path)
        if axes and encoding is not None and encoding[2].kind == "civil_date":
            for axis in axes:
                scopes = (
                    tuple(
                        replace(
                            contribution,
                            calendars=(),
                            period=ObservationPeriod(start, end, axis.timezone),
                        )
                        for _, start, end in population_time_ranges(axis)
                    )
                    if isinstance(axis, IntervalCoordinate)
                    else (replace(contribution, calendars=(("membership", axis),), period=None),)
                )
                for scope in scopes:
                    validate_date_contribution(scope, read.field.ref.path, self.scope.registry)

    def bind(
        self, table: ir.Table, columns: list[tuple[str, str]], originals: list[tuple[str, ir.Value]]
    ) -> ir.Table:
        table = self._bind_report(table, dict(columns))
        table = self._bind_intervals(table)
        return self._bind_samples(table, columns, originals)

    def _bind_report(self, table: ir.Table, columns: dict[str, str]) -> ir.Table:
        contribution, registry = self.scope.contribution, self.scope.registry
        for key, name, time_key, calendar in self.pending_calendars:
            day_clock = self.day_clocks.get(name)
            day_timezone = (
                day_clock[2].timezone
                if day_clock is not None and day_clock[2].kind != "instant"
                else None
            )
            date_timezone = (
                day_timezone if day_clock is not None and day_clock[0].type().is_date() else None
            )
            if contribution.time is not None:
                encoding = self.scope.time_encodings.get(contribution.time.field.ref.path)
                if encoding is not None:
                    self.scope.source_times.extend(
                        capture_source_times((encoding[2],), consumption_timezone=calendar.timezone)
                    )
            clock = columns[time_key]
            if (
                day_timezone is not None
                and contribution.time is not None
                and not contribution.time.path
                and contribution.time.at is None
                and contribution.window_key is None
                and contribution.window_rule is None
                and not contribution.sample_axes
                and not contribution.memberships
                and not contribution.population_reads
                and len(contribution.calendars) == 1
                and calendar in self.selected
            ):
                assert day_clock is not None
                deferred = deferred_day_key(
                    table,
                    day_clock[0],
                    calendar,
                    day_timezone,
                    entity_engine(
                        registry, normalize_target_entity(registry, contribution.root.ref.path)
                    ),
                    encoded=(day_clock[1], day_clock[2])
                    if isinstance(day_clock[1].parse, StrptimeParse)
                    else None,
                )
                if deferred is not None:
                    table = table.mutate(**{name: deferred})
                    continue
            table = bind_calendar(
                table,
                table[clock],
                calendar,
                name,
                date_timezone=date_timezone,
                engine=entity_engine(
                    registry, normalize_target_entity(registry, contribution.root.ref.path)
                ),
            )
            if key not in self.sample_slots and (calendar in self.selected or calendar.actual_only):
                table = table.filter(table[name].notnull())
        return table

    def _bind_intervals(self, table: ir.Table) -> ir.Table:
        for name, flags, overlaps in self.pending_intervals:
            if not overlaps:
                table = table.mutate(
                    **{
                        name: ibis.cases(
                            *(
                                (table[flag], ibis.literal(key, type="int64"))
                                for key, flag in flags
                            ),
                            else_=ibis.null().cast("int64"),
                        )
                    }
                )
                continue
            # Overlapping intervals receive each matching source occurrence.
            # The extra position preserves the enclosing key when no interval matches.
            position = self.scope.unique(name + "_position")
            table = table.cross_join(calendar_range(len(flags) + 1).rename({position: "position"}))
            ordinal = table[position]
            masks = tuple(table[flag].fill_null(False) for _, flag in flags)
            matched = ibis.or_(*(mask & (ordinal == i) for i, mask in enumerate(masks)))
            unmatched = (ordinal == len(flags)) & ~ibis.or_(*masks)
            table = table.filter(matched | unmatched)
            ordinal = table[position]
            table = table.mutate(
                **{
                    name: ibis.cases(
                        *(
                            (ordinal == i, ibis.literal(key, type="int64"))
                            for i, (key, _) in enumerate(flags)
                        ),
                        else_=ibis.null().cast("int64"),
                    )
                }
            )
        return table

    def _bind_samples(
        self, table: ir.Table, columns: list[tuple[str, str]], originals: list[tuple[str, ir.Value]]
    ) -> ir.Table:
        contribution, registry = self.scope.contribution, self.scope.registry
        prepared_columns = dict(columns)
        for slot in contribution.sample_time_slots:
            if slot in self.sample_slots:
                position = slot.removeprefix("key.")
                name = self.scope.unique("__sample_time_" + position)
                table = table.mutate(
                    **{name: table[prepared_columns[calendar_field(slot)]].notnull()}
                )
                columns.append((f"__sample_assigned_{position}", name))
                originals.append((name, table[name]))
        sample_calendars = dict(contribution.sample_calendar_reads)
        for key, sample_calendar in contribution.calendars:
            if not isinstance(sample_calendar, BuiltinSampleCoordinate):
                continue
            read = sample_calendars[key]
            _, axis, authority, _, _ = self.scope.time_encodings[read.field.ref.path]
            self.scope.source_times.extend(
                capture_source_times((authority,), consumption_timezone=sample_calendar.timezone)
            )
            name = self.scope.unique("__sample_bucket_" + key.replace(".", "_"))
            clock = table[prepared_columns[field_read_key(read)]]
            if authority.kind == "civil_date" and clock.type().is_date():
                clock = localize(authority.timezone, timestamp(clock))
            table = bind_calendar(
                table,
                clock,
                sample_calendar,
                name,
                engine=entity_engine(
                    registry, normalize_target_entity(registry, axis.entity_ref.path)
                ),
            )
            originals.append((name, table[name]))
            columns.append((calendar_field(key), name))
        return table
