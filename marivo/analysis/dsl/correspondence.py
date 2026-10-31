"""Canonical axis-free correspondence factories."""

from __future__ import annotations

from typing_extensions import overload

from marivo._definition.correspondence import CalendarCorrespondence
from marivo.analysis.core.model import reject


def ordinal() -> CalendarCorrespondence:
    """Pair complete calendar buckets or one declared interval per side by position.

    Args: None.
    Returns: An axis-free calendar correspondence.
    Example: ``current.compare({'reference': mv.baseline(baseline, match=mv.ordinal())})``.
    Constraints: Calendars must be compatible and complete; interval directories
    must each declare one record with the same boundary timezone.
    """
    return CalendarCorrespondence("ordinal")


class _Omitted:
    """Distinguish an absent keyword from an explicitly invalid value."""

    def __repr__(self) -> str:
        return "<omitted>"


_OMITTED = _Omitted()


@overload
def offset(*, years: int) -> CalendarCorrespondence: ...


@overload
def offset(*, periods: int) -> CalendarCorrespondence: ...


def offset(
    *, years: int | _Omitted = _OMITTED, periods: int | _Omitted = _OMITTED
) -> CalendarCorrespondence:
    """Construct a civil-year or grain-period offset without binding an axis.

    Args:
        years: Non-boolean integer civil-year offset, mutually exclusive with periods.
        periods: Non-boolean integer steps in the consuming axis's grain, including
            its grain step. Day -7 means seven days; week -1 means one week, week
            -7 means seven weeks, and month -1 means one month. A negative offset
            seeks an earlier baseline; it does not count rows or fixed days.
    Returns: An axis-free calendar correspondence.
    Example: ``baseline = periods.shift(mv.offset(years=-1))``.
    Constraints: Supply exactly one keyword. Missing civil dates are not clamped.
    """
    if (years is _OMITTED) == (periods is _OMITTED):
        reject(
            "exactly one of years or periods",
            repr((years, periods)),
            "Pass offset(years=-1) or offset(periods=-1).",
            "offset",
        )
    value = periods if years is _OMITTED else years
    if type(value) is not int:
        reject("a non-boolean integer", repr(value), "Pass an integer offset.", "offset")
    return CalendarCorrespondence("periods" if years is _OMITTED else "years", value)
