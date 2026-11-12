"""Source-clock repairs from the receiving captured semantic declarations."""

from __future__ import annotations

from marivo.semantic.ir import HourPrefixParse
from marivo.semantic.validator import Registry

REPORT_CLOCK_GUIDANCE = (
    "Report grain never replaces a Metric's contribution clock; "
    "scope.by(grain) only supplies report buckets. "
)


def partition_hour_candidates(registry: Registry, path: str) -> tuple[str, ...]:
    """Return bounded exact hour-prefix choices for a date or hour field."""
    field = registry.dimensions.get(path)
    if field is None or not field.is_time_dimension:
        return ()
    if isinstance(field.parse, HourPrefixParse):
        return (f"time_dimension:{path}",)
    return tuple(
        f"time_dimension:{candidate}"
        for candidate, dimension in sorted(registry.dimensions.items())
        if dimension.is_time_dimension
        and dimension.entity == field.entity
        and isinstance(dimension.parse, HourPrefixParse)
        and dimension.parse.prefix == path
    )[:3]


def partition_hour_guidance(candidates: tuple[str, ...]) -> str:
    """Describe explicit alternatives without rebinding any Metric."""
    if not candidates:
        return ""
    return (
        "Declared hour_prefix partition clocks are listed below. Use "
        "by=(scope.by(mv.grain('hour')),); if the Metric already uses the intended hour clock, "
        "keep it. Otherwise, for example, load "
        f"hour = session.catalog.get({candidates[0]!r}) and explicitly author or rebuild "
        "the intended source reduction with time=hour. Preserve its reducer, filters and "
        "formula branches. Changing clocks can change membership: NULL hours have no "
        "time membership; different timezones, "
        "ranges or event timestamps can change totals. Reconcile before replacing a Metric. "
        "To keep the original DATE clock and its rows, declare a separate ordinary hour-label "
        "Dimension and group by day plus that label, including its NULL group; "
        "these labels are not an hourly CalendarAxis."
    )
