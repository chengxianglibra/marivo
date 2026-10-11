"""Immutable complete-key alignment values for comparisons and ratios."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Literal, TypeAlias

from marivo._temporal import TimeScope
from marivo.analysis.core.model import reject
from marivo.analysis.core.time_alignment import (
    HolidayThenWeekdayTimeMapping,
    HolidayTimeMapping,
    OrdinalTimeMapping,
    TimeKeyMapping,
    WeekdayTimeMapping,
    capture_occurrence,
)
from marivo.render import _DEFAULT_MAX_OUTPUT_BYTES, Card


@dataclass(frozen=True, slots=True)
class OrdinalTimeKey:
    """Map each original grid bucket to its position within that complete grid.

    Args: None.
    Returns: An immutable ordinal time-key descriptor.
    Example: ``mv.exact(time=mv.ordinal())``.
    Constraints: Position correspondence does not establish calendar equivalence.
    """

    def __repr__(self) -> str:
        return "<OrdinalTimeKey; use .show()>"

    def show(self) -> None:
        """Print the time-key meaning without executing.

        Args: None.
        Returns: None; prints a bounded description.
        Example: ``mv.ordinal().show()``.
        Constraints: Requires complete original grids; each side starts at position 1.
        """
        print(
            "OrdinalTimeKey: string keys ordinal:1..ordinal:N; coverage is owned by the alignment."
        )


@dataclass(frozen=True, slots=True)
class WeekdayTimeKey:
    """Pair weekdays by their occurrence order within each complete daily grid.

    Args: None.
    Returns: An immutable weekday time-key descriptor.
    Example: ``mv.outer(time=mv.weekday())``.
    Constraints: Single-day grids only; coverage belongs to the alignment.
    """

    def __repr__(self) -> str:
        return "<WeekdayTimeKey; use .show()>"

    def show(self) -> None:
        """Print the weekday mapping.

        Args: None.
        Returns: None; prints bounded static guidance.
        Example: ``mv.weekday().show()``.
        Constraints: Does not query or execute.
        """
        print("WeekdayTimeKey: weekday:<ISO weekday 1..7>:<occurrence 1..N>; single-day grids.")


@dataclass(frozen=True, slots=True, kw_only=True)
class HolidayTimeKey:
    """Pair daily buckets by signed civil-day offset from certified occurrences.

    Args: current: Current occurrence TimeScope. baseline: Baseline occurrence TimeScope.
    Returns: An immutable holiday time-key descriptor.
    Example: ``mv.holiday(current=current_holiday, baseline=prior_holiday)``.
    Constraints: Certified whole-day occurrences; each grid uses its occurrence's timezone.
    """

    current: TimeScope
    baseline: TimeScope

    def __post_init__(self) -> None:
        capture_occurrence(self.current)
        capture_occurrence(self.baseline)

    def __repr__(self) -> str:
        identity = sha256(repr(_freeze_time(self)).encode()).hexdigest()[:12]
        return f"<HolidayTimeKey capture={identity}; use .show()>"

    def show(self, *, max_output_bytes: int | None = _DEFAULT_MAX_OUTPUT_BYTES) -> None:
        """Print the frozen holiday identity and mapping.

        Args: max_output_bytes: Positive byte budget, or None for complete output.
        Returns: None; prints bounded static guidance.
        Example: ``mv.holiday(current=current_holiday, baseline=prior_holiday).show()``.
        Constraints: Does not reload certification or execute.
        """
        print(
            Card(identity="HolidayTimeKey", available=(".current.show()", ".baseline.show()"))
            .field("key", "holiday:<signed day offset>")
            .field("current", repr(self.current))
            .field("baseline", repr(self.baseline))
            .render(max_output_bytes=max_output_bytes)
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class HolidayThenWeekdayTimeKey:
    """Pair holiday offsets inside occurrences and outward weekdays outside them.

    Args: current: Current occurrence TimeScope. baseline: Baseline occurrence TimeScope.
    Returns: An immutable holiday-then-weekday descriptor.
    Example: ``mv.holiday_then_weekday(current=current_holiday, baseline=prior_holiday)``.
    Constraints: Certified whole-day occurrences and single-day grids in their boundary timezones.
    """

    current: TimeScope
    baseline: TimeScope

    def __post_init__(self) -> None:
        capture_occurrence(self.current)
        capture_occurrence(self.baseline)

    def __repr__(self) -> str:
        identity = sha256(repr(_freeze_time(self)).encode()).hexdigest()[:12]
        return f"<HolidayThenWeekdayTimeKey capture={identity}; use .show()>"

    def show(self, *, max_output_bytes: int | None = _DEFAULT_MAX_OUTPUT_BYTES) -> None:
        """Print the hybrid mapping and bound occurrences.

        Args: max_output_bytes: Positive byte budget, or None for complete output.
        Returns: None; prints bounded static guidance.
        Example: ``mv.holiday_then_weekday(current=current_holiday, baseline=prior_holiday).show()``.
        Constraints: Outside counters start at the occurrence boundary, including dates outside the grid.
        """
        print(
            Card(
                identity="HolidayThenWeekdayTimeKey",
                available=(".current.show()", ".baseline.show()"),
            )
            .field(
                "key",
                "holiday offsets inside; before/after ISO weekday and outward occurrence outside",
            )
            .field("current", repr(self.current))
            .field("baseline", repr(self.baseline))
            .render(max_output_bytes=max_output_bytes)
        )


_TimeKey: TypeAlias = OrdinalTimeKey | WeekdayTimeKey | HolidayTimeKey | HolidayThenWeekdayTimeKey


def _freeze_time(time: _TimeKey) -> TimeKeyMapping:
    _validate_time(time)
    if isinstance(time, OrdinalTimeKey):
        return OrdinalTimeMapping()
    if isinstance(time, WeekdayTimeKey):
        return WeekdayTimeMapping()
    current, baseline = capture_occurrence(time.current), capture_occurrence(time.baseline)
    if isinstance(time, HolidayTimeKey):
        return HolidayTimeMapping(current, baseline)
    return HolidayThenWeekdayTimeMapping(current, baseline)


def _time_kind(time: _TimeKey | None) -> str:
    return "none" if time is None else _freeze_time(time).kind


def _validate_time(time: _TimeKey | None) -> None:
    if time is not None and type(time) not in (
        OrdinalTimeKey,
        WeekdayTimeKey,
        HolidayTimeKey,
        HolidayThenWeekdayTimeKey,
    ):
        reject(
            "an ordinal, weekday, holiday or holiday_then_weekday time key, or None",
            type(time).__name__,
            "Use mv.ordinal(), mv.weekday(), mv.holiday(...), or mv.holiday_then_weekday(...).",
            "analysis.comparison",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ExactAlignment:
    """Pair unique complete keys with equal images under checked verification.

    Args: time: Optional closed original-grid time-key mapping.
    Returns: An immutable exact alignment.
    Example: ``current.compare(baseline, align=mv.exact())``.
    Constraints: Unknown equality is checked; applicable frozen proofs remain trusted.
    """

    time: OrdinalTimeKey | WeekdayTimeKey | HolidayTimeKey | HolidayThenWeekdayTimeKey | None = None

    def __post_init__(self) -> None:
        _validate_time(self.time)

    def __repr__(self) -> str:
        return f"<ExactAlignment time={_time_kind(self.time)}; use .show()>"

    def show(self) -> None:
        """Print the exact alignment policy.

        Args: None.
        Returns: None; prints a bounded description.
        Example: ``mv.exact().show()``.
        Constraints: Does not inspect rows or execute a Run.
        """
        print(
            "ExactAlignment: unique complete keys with equal images; double-empty is valid; "
            f"verification=checked; time={_time_kind(self.time)}."
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class OuterAlignment:
    """Pair the complete-key union while preserving or finishing missing sides.

    Args:
        missing: Keep missing coordinates, or use proven original Metric empty finish.
        time: Optional closed original-grid time-key mapping.
    Returns: An immutable outer alignment.
    Example: ``current.compare(baseline, align=mv.outer())``.
    Constraints: Empty finish requires original complete coverage and cannot fill unobserved time keys.
    """

    missing: Literal["keep", "empty_value"] = "keep"
    time: OrdinalTimeKey | WeekdayTimeKey | HolidayTimeKey | HolidayThenWeekdayTimeKey | None = None

    def __post_init__(self) -> None:
        _validate_time(self.time)
        if self.missing not in ("keep", "empty_value"):
            reject(
                "keep or empty_value",
                str(self.missing),
                "Use mv.outer() to keep missing sides, or mv.outer(missing='empty_value').",
                "analysis.comparison",
            )

    def __repr__(self) -> str:
        return (
            f"<OuterAlignment missing={self.missing!r} time={_time_kind(self.time)}; use .show()>"
        )

    def show(self) -> None:
        """Print the outer alignment policy.

        Args: None.
        Returns: None; prints a bounded description.
        Example: ``mv.outer().show()``.
        Constraints: Does not inspect rows or execute a Run.
        """
        print(
            f"OuterAlignment: complete-key union; missing={self.missing}; time={_time_kind(self.time)}."
        )


def exact(
    *,
    time: OrdinalTimeKey
    | WeekdayTimeKey
    | HolidayTimeKey
    | HolidayThenWeekdayTimeKey
    | None = None,
) -> ExactAlignment:
    """Bind checked exact correspondence, optionally over original time-grid positions.

    Args: time: Optional ordinal, weekday, holiday or holiday_then_weekday descriptor.
    Returns: An immutable ExactAlignment.
    Example: ``current.compare(baseline, align=mv.exact(time=mv.ordinal()))``.
    Constraints: No assume option; unknown complete-key equality is checked.
    """
    return ExactAlignment(time=time)


def outer(
    *,
    missing: Literal["keep", "empty_value"] = "keep",
    time: OrdinalTimeKey
    | WeekdayTimeKey
    | HolidayTimeKey
    | HolidayThenWeekdayTimeKey
    | None = None,
) -> OuterAlignment:
    """Bind a complete-key union with an explicit optional empty-finish policy.

    Args:
        missing: Keep missing coordinates by default, or invoke original Metric empty finish.
        time: Optional ordinal, weekday, holiday or holiday_then_weekday descriptor.
    Returns: An immutable OuterAlignment.
    Example: ``current.compare(baseline, align=mv.outer())``.
    Constraints: Only compare accepts outer; keep permits unequal complete time grids, while empty_value rejects time keys outside the other window.
    """
    return OuterAlignment(missing=missing, time=time)


def ordinal() -> OrdinalTimeKey:
    """Select position correspondence within each complete original time grid.

    Args: None.
    Returns: An immutable OrdinalTimeKey.
    Example: ``mv.exact(time=mv.ordinal())``.
    Constraints: Uses aligned string keys ordinal:1..ordinal:N and retains both original bucket coordinates; does not imply weekday, holiday or financial-calendar equivalence.
    """
    return OrdinalTimeKey()


def weekday() -> WeekdayTimeKey:
    """Select ISO weekday and same-weekday occurrence within each original grid.

    Args: None.
    Returns: An immutable WeekdayTimeKey.
    Example: ``current.compare(baseline, align=mv.outer(time=mv.weekday()))``.
    Constraints: Single-day grids only; uses each frozen boundary timezone.
    """
    return WeekdayTimeKey()


def holiday(*, current: TimeScope, baseline: TimeScope) -> HolidayTimeKey:
    """Select signed civil-day offsets from two exact certified occurrences.

    Args: current: Current occurrence TimeScope. baseline: Baseline occurrence TimeScope.
    Returns: An immutable HolidayTimeKey with captured occurrence identities.
    Example: ``mv.holiday(current=holidays.occurrence('current'), baseline=holidays.occurrence('prior'))``.
    Constraints: Whole-day occurrences only; their boundary timezones must match their respective daily grids.
    """
    return HolidayTimeKey(current=current, baseline=baseline)


def holiday_then_weekday(*, current: TimeScope, baseline: TimeScope) -> HolidayThenWeekdayTimeKey:
    """Select holiday offsets inside and outward weekday correspondence outside.

    Args: current: Current occurrence TimeScope. baseline: Baseline occurrence TimeScope.
    Returns: An immutable HolidayThenWeekdayTimeKey with exact occurrence captures.
    Example: ``mv.holiday_then_weekday(current=holidays.occurrence('current'), baseline=holidays.occurrence('prior'))``.
    Constraints: Whole-day occurrences and daily grids in their boundary timezones; coverage remains owned by exact/outer/one_to_one.
    """
    return HolidayThenWeekdayTimeKey(current=current, baseline=baseline)
