"""Canonical axis-free correspondence factories."""

from __future__ import annotations

from marivo._definition.correspondence import CalendarCorrespondence
from marivo.analysis.core.model import reject


def ordinal() -> CalendarCorrespondence:
    """Pair complete compatible calendar buckets by their original position.

    Args: None.
    Returns: An axis-free calendar correspondence.
    Example: ``current.compare(baseline, match=mv.ordinal())``.
    Constraints: Both endpoints must have one compatible complete calendar.
    """
    return CalendarCorrespondence("ordinal")


def period_offset(by: int) -> CalendarCorrespondence:
    """Pair each current period with the period at a calendar offset.

    Args: by: Non-boolean integer; -1 selects the previous period.
    Returns: An axis-free calendar correspondence.
    Example: ``today.compare(yesterday, match=mv.period_offset(-1))``.
    Constraints: The consuming calendars fix the grain and exact authority.
    """
    if type(by) is not int:
        reject("a non-boolean integer", repr(by), "Pass an integer period offset.", "period_offset")
    return CalendarCorrespondence("period_offset", by)


def previous_year() -> CalendarCorrespondence:
    """Pair same-name builtin day, month, quarter or year periods last year.

    Args: None.
    Returns: An axis-free calendar correspondence.
    Example: ``current.compare(baseline, match=mv.previous_year())``.
    Constraints: Leap days have no counterpart; exact rejects missing sides.
    """
    return CalendarCorrespondence("previous_year")
