"""Analysis-domain timezone validation and strict creation-time host resolution."""

from __future__ import annotations

from zoneinfo import ZoneInfoNotFoundError

from marivo._timezones import NormalizedTimezone, SystemTimezoneUnresolvedError, normalize_timezone
from marivo._timezones import resolve_system_timezone as _resolve_system_timezone
from marivo.analysis.errors import AnalysisRepair, ReportTimezoneUnresolved, TimezoneInvalidError
from marivo.introspection.live.model import LiveHelpTarget


def resolve_timezone(value: str) -> NormalizedTimezone:
    """Validate an explicit timezone using the shared name and offset contract."""
    try:
        return normalize_timezone(value)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise TimezoneInvalidError(
            message="The report timezone is not a valid IANA name or explicit UTC offset.",
            expected="a loadable IANA timezone name or explicit UTC offset",
            received=repr(value),
            location="mv.session.get_or_create(timezone=...)",
            context={"kind": "TimezoneInvalid", "tz": repr(value)},
            repair=AnalysisRepair(
                kind="retry",
                action="Pass a valid timezone without surrounding whitespace.",
                help_target=LiveHelpTarget(
                    surface="analysis", canonical_id="session.get_or_create"
                ),
            ),
        ) from exc


def resolve_system_timezone() -> NormalizedTimezone:
    """Freeze a strict system timezone or raise a structured explicit-input repair."""
    try:
        return _resolve_system_timezone()
    except SystemTimezoneUnresolvedError as exc:
        raise ReportTimezoneUnresolved(source=exc.source, received=exc.received) from exc
