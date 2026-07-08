"""Datasource engine timezone probing for runtime read-timezone defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import tzinfo
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from marivo.datasource.engines import profile_for_backend

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
            tz = ZoneInfo(env_tz)
            return DatasourceEngineTimezone(
                engine_timezone_name=env_tz,
                engine_timezone_tz=tz,
                engine_timezone_resolution="iana",
                read_tz_resolution="system_fallback",
            )
        except ZoneInfoNotFoundError:
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
        engine_timezone_name=str(local_tz),
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


def _scalar_from_result(result: Any) -> object:
    if hasattr(result, "iloc"):
        return result.iloc[0, 0]
    if isinstance(result, list | tuple):
        first = result[0]
        if isinstance(first, list | tuple):
            return first[0]
        return first
    if isinstance(result, dict):
        return next(iter(result.values()))
    return result


def _execute_scalar(backend: Any, query: str) -> object:
    sql = getattr(backend, "sql", None)
    if not callable(sql):
        raise RuntimeError("backend does not expose sql(query)")
    expr = sql(query)
    execute = getattr(expr, "execute", None)
    if not callable(execute):
        raise RuntimeError("backend sql(query) did not return an executable expression")
    return _scalar_from_result(execute())


def probe_engine_timezone(backend: object) -> DatasourceEngineTimezone:
    """Probe the backend's default timezone, falling back to system timezone."""

    profile = profile_for_backend(backend)
    query = profile.timezone_probe_sql
    if query is None:
        return _fallback()
    try:
        raw_value = _execute_scalar(backend, query)
        name = str(raw_value)
        tz = ZoneInfo(name)
    except Exception as exc:
        return _fallback(f"engine timezone probe failed: {exc}")
    return DatasourceEngineTimezone(
        engine_timezone_name=name,
        engine_timezone_tz=tz,
        engine_timezone_resolution="iana",
        read_tz_resolution="engine",
    )


def system_timezone_name() -> str:
    """Return the system timezone IANA name (or UTC offset fallback)."""
    return _resolve_system_timezone().engine_timezone_name
