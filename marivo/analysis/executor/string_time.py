"""Strptime directive classification and string/integer temporal parsing."""

from __future__ import annotations

import re
from typing import Any

from marivo.analysis.errors import WindowInvalidError
from marivo.datasource.engines.base import GENERIC_PROFILE, EngineProfile

_DATE_DIRECTIVES = frozenset({"%Y", "%y", "%m", "%d", "%j", "%U", "%W"})
_HOUR_DIRECTIVES = frozenset({"%H", "%I", "%k", "%l"})
_MINUTE_DIRECTIVES = frozenset({"%M"})
_SECOND_DIRECTIVES = frozenset({"%S"})
_SUBSECOND_DIRECTIVES = frozenset({"%f"})
_AMPM_DIRECTIVES = frozenset({"%p", "%P"})


def _classify_strptime_format(fmt: str) -> str:
    """Classify a strptime format string by its temporal granularity."""
    tokens = re.findall(r"%[a-zA-Z]", fmt)
    has_date = bool(_DATE_DIRECTIVES & set(tokens))
    has_hour = bool((_HOUR_DIRECTIVES | _AMPM_DIRECTIVES) & set(tokens))
    has_minute = bool(_MINUTE_DIRECTIVES & set(tokens))
    has_second = bool(_SECOND_DIRECTIVES & set(tokens))
    has_subsecond = bool(_SUBSECOND_DIRECTIVES & set(tokens))

    if has_subsecond or has_second:
        return "sub_hour"
    if has_minute:
        if not has_date:
            return "hour_only_minute"
        return "minute"
    if has_hour:
        if not has_date:
            return "hour_only"
        return "hour"
    if has_date:
        return "day"
    return "hour_only"


def _parse_string_column(
    field_expr: Any, time_meta: Any, *, profile: EngineProfile = GENERIC_PROFILE
) -> Any:
    """Parse a string/integer column into a temporal type using as_date/as_timestamp."""
    data_type = time_meta.data_type
    fmt = time_meta.format
    if fmt is None or not fmt.startswith("%"):
        raise WindowInvalidError(
            message=f"cannot parse string column without a strptime format "
            f"(data_type={data_type!r}, format={fmt!r})",
        )
    if data_type == "integer":
        string_expr = field_expr.cast("string")
    elif data_type == "string":
        string_expr = field_expr
    else:
        raise WindowInvalidError(
            message=f"_parse_string_column only supports string/integer, got {data_type!r}",
        )
    # Classify on the authored Python strptime format (directives like %M/%S are
    # Python-specific); the emitted format may be translated to MySQL below.
    classification = _classify_strptime_format(fmt)
    emit_fmt = fmt
    try:
        emit_fmt = profile.translate_strptime_format(fmt)
    except ValueError as exc:
        raise WindowInvalidError(
            message=(
                f"time field strptime format {time_meta.format!r} cannot be "
                f"translated for the {profile.name!r} backend's date parser: {exc}. "
                f"Restrict the format to supported directives "
                f"(year/month/day/hour/minute/second) or use a native temporal column."
            ),
        ) from exc
    if classification == "day":
        return string_expr.as_date(emit_fmt)
    return string_expr.as_timestamp(emit_fmt)
