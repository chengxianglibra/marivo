"""Datasource engine timezone probing for runtime read-timezone defaults."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone, tzinfo
from typing import Literal
from zoneinfo import ZoneInfoNotFoundError

from ibis.backends import BaseBackend

from marivo._timezones import (
    fixed_offset_timezone_candidate,
    system_timezone_candidate,
)
from marivo._timezones import (
    parse_timezone as parse_timezone,
)
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
    candidate = system_timezone_candidate(allow_fixed_offset=True)
    if candidate is None:
        return _fixed_offset_fallback()
    name, tz, resolution = candidate
    return DatasourceEngineTimezone(
        engine_timezone_name=name,
        engine_timezone_tz=tz,
        engine_timezone_resolution=resolution,
        read_tz_resolution="system_fallback",
    )


def _fixed_offset_fallback() -> DatasourceEngineTimezone:
    name, local_tz, resolution = fixed_offset_timezone_candidate()
    return DatasourceEngineTimezone(
        engine_timezone_name=name,
        engine_timezone_tz=local_tz,
        engine_timezone_resolution=resolution,
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


def probe_engine_timezone(backend: object) -> DatasourceEngineTimezone:
    """Read a configured or driver-reported timezone without submitting SQL."""
    profile = profile_for_backend(backend)
    if profile.name == "generic":
        return _fallback()
    name = getattr(backend, "_marivo_timezone_name", None)
    if profile.name == "postgres":
        connection = getattr(backend, "con", None)
        info = getattr(connection, "info", None)
        parameter_status = getattr(info, "parameter_status", None)
        name = parameter_status("TimeZone") if callable(parameter_status) else None
    elif profile.name == "clickhouse" and name is None:
        reported = getattr(getattr(backend, "con", None), "server_tz", None)
        if isinstance(reported, tzinfo):
            name = (
                getattr(reported, "key", None) or getattr(reported, "zone", None) or str(reported)
            )
    if not isinstance(name, str) or not name:
        raise DatasourceConnectionError(
            message="The reader timezone is unavailable from the driver.",
            expected="a configured or driver-reported IANA timezone",
            received=f"{profile.name} timezone unavailable",
            repair=repair(
                kind="configure",
                canonical_id="datasource_catalog.test",
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
                canonical_id="datasource_catalog.test",
                action="Configure a valid reader timezone.",
            ),
        ) from exc
    return DatasourceEngineTimezone(
        engine_timezone_name=normalized,
        engine_timezone_tz=zone,
        engine_timezone_resolution="fixed_offset" if isinstance(zone, timezone) else "iana",
        read_tz_resolution="engine",
    )


def verify_source_timezone(backend: BaseBackend, name: str) -> None:
    """Verify a selected native projection timezone where the provider requires it."""
    profile = profile_for_backend(backend)
    if profile.name == "mysql":
        from marivo.datasource.engines.mysql import verify_source_timezone as verify_mysql

        normalized, zone = parse_timezone(name)
        # Fixed offsets use clock arithmetic and UTC is lowered to +00:00;
        # neither path depends on MySQL's optional named time zone tables.
        if normalized != "UTC" and not isinstance(zone, timezone):
            verify_mysql(backend, normalized)


def system_timezone_name() -> str:
    """Return the system timezone IANA name (or UTC offset fallback)."""
    return _resolve_system_timezone().engine_timezone_name
