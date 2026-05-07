# DEPRECATED: Pure computation extracted to app.core.semantic.calendar.

from __future__ import annotations

from datetime import date, timedelta

from app.analysis_core.calendar_policy import CalendarBaselineGenerationRule


def resolve_calendar_baseline_window(
    *,
    current_window: tuple[date, date],
    rule: CalendarBaselineGenerationRule,
) -> tuple[date, date]:
    current_start, current_end = current_window
    if rule.strategy == "previous_year":
        shift_years = -(rule.offset_value or 1)
        return (
            shift_calendar_date(current_start, years=shift_years),
            shift_calendar_date(current_end, years=shift_years),
        )
    if rule.strategy == "previous_period":
        if rule.offset_unit == "week":
            shift_days = 7 * (rule.offset_value or 1)
            delta = timedelta(days=shift_days)
            return current_start - delta, current_end - delta
        # previous_period is the fixed "previous adjacent equal-length window"
        # baseline used elsewhere in the compiler; it does not mean "previous
        # named calendar month/quarter" in v1.
        period_days = current_end - current_start
        return current_start - period_days, current_end - period_days
    raise ValueError(f"Unsupported calendar baseline strategy '{rule.strategy}'")


def shift_calendar_date(d: date, *, months: int = 0, years: int = 0) -> date:
    from calendar import monthrange

    target_month = d.month + months
    target_year = d.year + years + (target_month - 1) // 12
    target_month = (target_month - 1) % 12 + 1
    target_day = min(d.day, monthrange(target_year, target_month)[1])
    return date(target_year, target_month, target_day)
