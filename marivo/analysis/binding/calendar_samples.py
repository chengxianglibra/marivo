"""Bind internal sample clocks independently of observation selection."""

from __future__ import annotations

from marivo._definition.expressions import BuiltinSampleCoordinate, FieldCalendarSample
from marivo._definition.observations import CalendarSample, SampleAxis
from marivo._definition.types import CalendarCoordinate, IntervalCoordinate
from marivo._temporal import Grain, PeriodCalendarSnapshotV2
from marivo._timezones import same_timezone
from marivo.analysis.binding.windows import resolve_calendar_grain
from marivo.analysis.core.model import reject


def _builtin_nests(inner: Grain, outer: Grain) -> bool:
    widths = {"second": 1, "minute": 60, "hour": 3600}
    if inner.unit in widths:
        if outer.unit not in widths:
            return True
        assert inner.count is not None and outer.count is not None
        return (widths[outer.unit] * outer.count) % (widths[inner.unit] * inner.count) == 0
    return inner == outer or (inner.unit, outer.unit) in {
        ("day", "week"),
        ("day", "month"),
        ("day", "quarter"),
        ("day", "year"),
        ("month", "quarter"),
        ("month", "year"),
        ("quarter", "year"),
    }


def validate_sample_parent(
    inner: BuiltinSampleCoordinate, outer: BuiltinSampleCoordinate | CalendarCoordinate
) -> None:
    """Qualify same-field hierarchies after each contribution's clocks are known."""
    if not same_timezone(inner.timezone, outer.timezone):
        reject(
            "matching timezones for calendar layers on the same field",
            f"Session {inner.timezone!r}, parent axis {outer.timezone!r}",
            "Align the CalendarAxis with the Session report timezone, or use TimeScope "
            "to select an observation period without an enclosing calendar axis.",
            "Metric.per",
            canonical_id="session.observe",
        )
    if outer.grain_spec.kind != "builtin" or not _builtin_nests(inner.grain_spec, outer.grain_spec):
        reject(
            "a supported static child-to-parent calendar grain combination on the same field",
            f"{inner.grain_spec._to_token()} within {outer.grain_spec._to_token()}",
            'Choose m.per(mv.grain("day")) or a finer grain nested in the enclosing calendar.',
            "Metric.per",
            canonical_id="session.observe",
        )


def bind_calendar_samples(
    sample: Grain | FieldCalendarSample,
    timezone: str,
    observation_axis: CalendarCoordinate | IntervalCoordinate | None,
    ancestors: tuple[SampleAxis, ...],
    calendar_snapshots: tuple[PeriodCalendarSnapshotV2, ...] = (),
) -> CalendarSample:
    """Freeze builtin sample bucketing or qualify one certified custom period."""
    if isinstance(sample, FieldCalendarSample) or sample.kind == "builtin":
        return BuiltinSampleCoordinate(sample, timezone)

    if isinstance(observation_axis, CalendarCoordinate) or any(
        isinstance(axis, (CalendarCoordinate, BuiltinSampleCoordinate)) for axis in ancestors
    ):
        reject(
            "a supported static child-to-parent calendar grain combination",
            f"{sample!r}: custom calendar sample hierarchies are unsupported",
            "Use a single certified period and omit enclosing calendar sample layers.",
            "Metric.per",
            canonical_id="session.observe",
        )
    if observation_axis is None:
        reject(
            "one finite observation TimeScope selecting a complete custom period",
            repr(sample),
            "Use a single certified period and omit enclosing calendar sample layers.",
            "Metric.per",
            canonical_id="session.observe",
        )
    from marivo._temporal import calendar_axis, session_time_scope

    grain = resolve_calendar_grain(sample, calendar_snapshots, location="Metric.per")
    try:
        calendar = calendar_axis(
            session_time_scope(
                observation_axis.start, observation_axis.end, observation_axis.timezone
            ),
            grain,
        )._binding
    except ValueError as error:
        reject(
            "one complete custom period",
            str(error),
            "Use a TimeScope matching one period in the certified calendar.",
            "Metric.per",
            canonical_id="session.observe",
        )
    if len(calendar.cells) != 1 or calendar.cells[0][:2] != calendar.cells[0][2:]:
        reject(
            "one complete custom calendar period",
            repr(grain),
            "Use a TimeScope matching one period in the certified calendar.",
            "Metric.per",
            canonical_id="session.observe",
        )
    return calendar
