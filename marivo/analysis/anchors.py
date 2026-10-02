"""Exact elapsed values and closed relative Anchor windows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import ClassVar, Literal, NoReturn
from zoneinfo import ZoneInfo

from pydantic import ConfigDict


def _fail(constraint: str, received: str, repair: str) -> NoReturn:
    from marivo.analysis.core.domain_captures import DomainPreparationError

    raise DomainPreparationError(
        "r7." + constraint,
        "window",
        "an exact positive relative window with a uniquely representable deadline",
        received,
        repair,
    )


@dataclass(frozen=True, slots=True, repr=False)
class Duration:
    """Exact signed elapsed ticks; use duration() to author a named unit."""

    ticks: int
    unit: Literal["s", "ms", "us", "ns"]
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    def __post_init__(self) -> None:
        if (
            type(self.ticks) is not int
            or not -(2**63) <= self.ticks < 2**63
            or self.unit not in ("s", "ms", "us", "ns")
        ):
            _fail(
                "duration",
                repr((self.ticks, self.unit)),
                "Use one integer unit fitting int64 ticks.",
            )

    def __repr__(self) -> str:
        return f"<Duration ticks={self.ticks} unit={self.unit}>"


@dataclass(frozen=True, slots=True, repr=False)
class ElapsedWindow:
    """A positive exact elapsed interval, independent of calendar days."""

    duration: Duration
    kind: Literal["elapsed"] = "elapsed"
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    def __post_init__(self) -> None:
        if (
            type(self.duration) is not Duration
            or self.duration.ticks <= 0
            or self.kind != "elapsed"
        ):
            _fail(
                "elapsed", repr(self.duration), "Pass mv.duration(...) with positive integer ticks."
            )

    def __repr__(self) -> str:
        return f"<ElapsedWindow ticks={self.duration.ticks} unit={self.duration.unit}>"


@dataclass(frozen=True, slots=True, repr=False)
class CalendarWindow:
    """Positive whole local days in a frozen named IANA zone."""

    days: int
    timezone: str
    kind: Literal["calendar"] = "calendar"
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    def __post_init__(self) -> None:
        if type(self.days) is not int or self.days <= 0 or self.kind != "calendar":
            _fail(
                "calendar_days",
                repr(self.days),
                "Use a positive integer day count, excluding bool.",
            )
        try:
            ZoneInfo(self.timezone)
        except (ValueError, KeyError, TypeError):
            _fail("calendar_days", repr(self.timezone), "Pass a named IANA ZoneInfo zone.")

    def __repr__(self) -> str:
        return f"<CalendarWindow days={self.days} timezone={self.timezone}>"


def duration(
    *,
    hours: int | None = None,
    minutes: int | None = None,
    seconds: int | None = None,
    milliseconds: int | None = None,
    microseconds: int | None = None,
    nanoseconds: int | None = None,
) -> Duration:
    """Author exact elapsed ticks using exactly one named integer unit.

    Args:
        hours: Whole elapsed hours, converted to seconds.
        minutes: Whole elapsed minutes, converted to seconds.
        seconds: Exact second ticks.
        milliseconds: Exact millisecond ticks.
        microseconds: Exact microsecond ticks.
        nanoseconds: Exact nanosecond ticks.
    Returns: A signed Duration preserving its exact tick unit.
    Example: ``span = mv.duration(hours=168)``.
    Constraints: Exactly one argument; bool and int64 overflow reject.
    """
    values: tuple[tuple[int | None, int, Literal["s", "ms", "us", "ns"]], ...] = (
        (hours, 3600, "s"),
        (minutes, 60, "s"),
        (seconds, 1, "s"),
        (milliseconds, 1, "ms"),
        (microseconds, 1, "us"),
        (nanoseconds, 1, "ns"),
    )
    selected = tuple((value, factor, unit) for value, factor, unit in values if value is not None)
    if len(selected) != 1 or type(selected[0][0]) is not int:
        _fail("duration", repr(selected), "Specify exactly one named integer unit, excluding bool.")
    value, factor, unit = selected[0]
    assert isinstance(value, int)
    return Duration(value * factor, unit)


def elapsed(duration: Duration) -> ElapsedWindow:
    """Bind a positive exact elapsed relative window.

    Args: duration: An exact positive Duration.
    Returns: An ElapsedWindow.
    Example: ``window = mv.elapsed(mv.duration(hours=168))``.
    Constraints: No calendar coercion; deadline precision must be representable.
    """
    return ElapsedWindow(duration)


def calendar_days(days: int, timezone: ZoneInfo) -> CalendarWindow:
    """Bind whole local days in an explicit IANA timezone.

    Args: days: Positive whole local days. timezone: Named IANA ZoneInfo.
    Returns: A CalendarWindow preserving wall time across DST.
    Example: ``window = mv.calendar_days(7, ZoneInfo('America/New_York'))``.
    Constraints: Ambiguous or nonexistent local deadlines reject without repair by shifting.
    """
    if type(timezone) is not ZoneInfo or not timezone.key:
        _fail("calendar_days", repr(timezone), "Pass ZoneInfo with an explicit IANA key.")
    return CalendarWindow(days, timezone.key)


def deadline(anchor: datetime, window: ElapsedWindow | CalendarWindow) -> datetime:
    """Resolve an exact deadline against the actual captured microsecond carrier."""
    if anchor.utcoffset() is None:
        _fail("window_time", repr(anchor), "Use the captured aware Anchor instant.")
    try:
        if isinstance(window, ElapsedWindow):
            ticks = window.duration.ticks
            unit = window.duration.unit
            if unit == "ns" and ticks % 1000:
                _fail(
                    "window_precision",
                    repr(window),
                    "Use a duration exactly representable in captured microseconds.",
                )
            micros = ticks * {"s": 1_000_000, "ms": 1000, "us": 1, "ns": 1}[unit]
            if unit == "ns":
                micros //= 1000
            return anchor.astimezone(timezone.utc) + timedelta(microseconds=micros)
        zone = ZoneInfo(window.timezone)
        local = anchor.astimezone(zone).replace(tzinfo=None) + timedelta(days=window.days)
        candidates = {
            candidate.astimezone(timezone.utc)
            for fold in (0, 1)
            for candidate in (local.replace(tzinfo=zone, fold=fold),)
            if candidate.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == local
        }
        if len(candidates) != 1:
            _fail(
                "calendar_deadline",
                f"{local.isoformat()} in {window.timezone}: {len(candidates)} valid instants",
                "Use an explicit elapsed window or a business-approved unambiguous local boundary.",
            )
        return next(iter(candidates))
    except OverflowError:
        _fail(
            "window_overflow",
            repr(window),
            "Use a shorter window within the captured instant range.",
        )
    raise AssertionError("unreachable window deadline")
