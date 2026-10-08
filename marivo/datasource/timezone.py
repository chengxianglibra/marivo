"""Datasource engine timezone probing for runtime read-timezone defaults."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from marivo.datasource.engines import profile_for_backend
from marivo.datasource.errors import DatasourceConnectionError, repair

ReadTimezoneResolution = Literal["engine", "system_fallback"]


@dataclass(frozen=True)
class DatasourceEngineTimezone:
    """Resolved datasource read timezone and provenance."""

    engine_timezone_name: str
    engine_timezone_tz: tzinfo
    engine_timezone_resolution: str
    read_tz_resolution: ReadTimezoneResolution
    warning: str | None = None


def _resolve_system_timezone() -> DatasourceEngineTimezone:
    env_tz = os.environ.get("TZ")
    if env_tz:
        try:
            name, tz = parse_timezone(env_tz)
            return DatasourceEngineTimezone(
                engine_timezone_name=name,
                engine_timezone_tz=tz,
                engine_timezone_resolution="fixed_offset" if isinstance(tz, timezone) else "iana",
                read_tz_resolution="system_fallback",
            )
        except (ZoneInfoNotFoundError, ValueError):
            return _fixed_offset_fallback()

    localtime = Path("/etc/localtime")
    if localtime.is_symlink():
        target = str(localtime.resolve())
        marker = "/zoneinfo/"
        if marker in target:
            candidate = target.split(marker, 1)[1]
            try:
                return DatasourceEngineTimezone(
                    engine_timezone_name=candidate,
                    engine_timezone_tz=ZoneInfo(candidate),
                    engine_timezone_resolution="iana",
                    read_tz_resolution="system_fallback",
                )
            except ZoneInfoNotFoundError:
                return _fixed_offset_fallback()

    return _fixed_offset_fallback()


def _fixed_offset_fallback() -> DatasourceEngineTimezone:
    from datetime import datetime

    local_tz = datetime.now().astimezone().tzinfo
    if local_tz is None:
        local_tz = ZoneInfo("UTC")
    return DatasourceEngineTimezone(
        engine_timezone_name=str(timezone(local_tz.utcoffset(None) or timedelta(0))),
        engine_timezone_tz=local_tz,
        engine_timezone_resolution="fixed_offset",
        read_tz_resolution="system_fallback",
        warning="system timezone could not be resolved as IANA; fixed offset fallback is in use",
    )


def _fallback(warning: str | None = None) -> DatasourceEngineTimezone:
    system_tz = _resolve_system_timezone()
    if warning is not None:
        return DatasourceEngineTimezone(
            engine_timezone_name=system_tz.engine_timezone_name,
            engine_timezone_tz=system_tz.engine_timezone_tz,
            engine_timezone_resolution=system_tz.engine_timezone_resolution,
            read_tz_resolution="system_fallback",
            warning=warning,
        )
    return system_tz


def parse_timezone(value: str) -> tuple[str, tzinfo]:
    """Normalize IANA or explicit offsets without guessing timezone abbreviations."""
    offset = value[3:] if value.startswith("UTC") else value
    if re.fullmatch(r"[+-]\d{2}:\d{2}(?::\d{2})?", offset):
        hours, minutes, *seconds = (int(part) for part in offset[1:].split(":"))
        if hours >= 24 or minutes >= 60 or (seconds and seconds[0] >= 60):
            raise ValueError("invalid fixed timezone offset")
        zone = datetime.fromisoformat("2000-01-01T00:00:00" + offset).tzinfo
        if zone is None:
            raise ValueError("missing fixed timezone offset")
        return "UTC" + offset, zone
    return value, ZoneInfo(value)


def probe_engine_timezone(backend: object) -> DatasourceEngineTimezone:
    """Read a configured or driver-reported timezone without submitting SQL."""
    profile = profile_for_backend(backend)
    if profile.name in {"sqlite", "generic"}:
        return _fallback()
    name = getattr(backend, "_marivo_timezone_name", None)
    if profile.name == "postgres":
        connection = getattr(backend, "con", None)
        info = getattr(connection, "info", None)
        parameter_status = getattr(info, "parameter_status", None)
        name = parameter_status("TimeZone") if callable(parameter_status) else None
    if not isinstance(name, str) or not name:
        raise DatasourceConnectionError(
            message="The reader timezone is unavailable from the driver.",
            expected="a configured or driver-reported IANA timezone",
            received=f"{profile.name} timezone unavailable",
            repair=repair(
                kind="configure",
                canonical_id="test",
                action="Use a backend connection with a verifiable timezone before time-sensitive analysis.",
            ),
        )
    try:
        normalized, zone = parse_timezone(name)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise DatasourceConnectionError(
            message="The reader reported an invalid timezone.",
            expected="a valid IANA timezone or UTC offset",
            received="invalid_engine_timezone",
            repair=repair(
                kind="configure",
                canonical_id="test",
                action="Configure a valid reader timezone.",
            ),
        ) from exc
    return DatasourceEngineTimezone(
        engine_timezone_name=normalized,
        engine_timezone_tz=zone,
        engine_timezone_resolution="fixed_offset" if isinstance(zone, timezone) else "iana",
        read_tz_resolution="engine",
    )


def system_timezone_name() -> str:
    """Return the system timezone IANA name (or UTC offset fallback)."""
    return _resolve_system_timezone().engine_timezone_name
