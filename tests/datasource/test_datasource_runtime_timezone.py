from __future__ import annotations

from datetime import datetime
from typing import cast
from zoneinfo import ZoneInfo

import pytest
from ibis.backends import BaseBackend

from marivo.datasource.adapters import SourceSession, provider_for
from marivo.datasource.capabilities import provider_statement_log
from marivo.datasource.errors import DatasourceConnectionError
from marivo.datasource.runtime import DatasourceConnectionService
from marivo.datasource.timezone import probe_engine_timezone, verify_source_timezone
from tests.datasource.source_cases import datasource


class _Backend:
    def __init__(self, *, name: str, value: object = "Asia/Shanghai", fails: bool = False) -> None:
        self.name = name
        if not fails:
            self._marivo_timezone_name = value


def test_probe_engine_timezone_uses_configured_duckdb_timezone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TZ", "UTC")
    backend = _Backend(name="duckdb", value="Asia/Shanghai")

    resolved = probe_engine_timezone(backend)

    assert resolved.engine_timezone_name == "Asia/Shanghai"
    assert resolved.engine_timezone_tz == ZoneInfo("Asia/Shanghai")
    assert resolved.read_tz_resolution == "engine"
    assert not hasattr(backend, "sql")


def test_probe_engine_timezone_uses_system_fallback_for_bigquery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    backend = _Backend(name="bigquery", value="UTC")

    resolved = probe_engine_timezone(backend)

    assert resolved.engine_timezone_name == "Asia/Tokyo"
    assert resolved.read_tz_resolution == "system_fallback"
    assert not hasattr(backend, "sql")


def test_probe_engine_timezone_uses_system_fallback_when_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TZ", "UTC")
    backend = _Backend(name="clickhouse", fails=True)

    from marivo.datasource.errors import DatasourceConnectionError

    with pytest.raises(DatasourceConnectionError) as failure:
        probe_engine_timezone(backend)
    assert failure.value.received == "clickhouse timezone unavailable"


def test_datasource_connection_service_caches_engine_timezone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TZ", "UTC")
    backend = _Backend(name="duckdb", value="Asia/Shanghai")
    service = DatasourceConnectionService(
        backends={"warehouse": lambda: backend},
        use_datasources=False,
    )

    first = service.engine_timezone("warehouse")
    second = service.engine_timezone("warehouse")

    assert first is second
    assert first.engine_timezone_name == "Asia/Shanghai"
    assert not hasattr(backend, "sql")


def test_probe_engine_timezone_resolves_presto_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "UTC")
    backend = _Backend(name="presto", value="Asia/Shanghai")

    resolved = probe_engine_timezone(backend)

    assert resolved.engine_timezone_name == "Asia/Shanghai"
    assert resolved.read_tz_resolution == "engine"
    assert not hasattr(backend, "sql")


def test_probe_engine_timezone_does_not_probe_snowflake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    backend = _Backend(name="snowflake", value="UTC")

    resolved = probe_engine_timezone(backend)

    assert resolved.engine_timezone_name == "Asia/Tokyo"
    assert resolved.read_tz_resolution == "system_fallback"
    assert not hasattr(backend, "sql")


def test_probe_engine_timezone_rejects_mysql_without_driver_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    backend = _Backend(name="mysql", fails=True)
    from marivo.datasource.errors import DatasourceConnectionError

    with pytest.raises(DatasourceConnectionError, match="timezone is unavailable"):
        probe_engine_timezone(backend)


def test_probe_clickhouse_timezone_uses_existing_driver_fact() -> None:
    from types import SimpleNamespace

    backend = SimpleNamespace(name="clickhouse", con=SimpleNamespace(server_tz=ZoneInfo("UTC")))
    resolved = probe_engine_timezone(backend)
    assert resolved.engine_timezone_name == "UTC"
    assert resolved.read_tz_resolution == "engine"


class _TimezoneCursor:
    description = (("converted", "datetime"),)

    def __init__(self, result: datetime | None) -> None:
        self.result = result
        self.closed = False

    def fetchall(self) -> list[tuple[datetime | None]]:
        return [(self.result,)]

    def close(self) -> None:
        self.closed = True


class _MySQLTimezoneBackend:
    name = "mysql"

    def __init__(self, result: datetime | None) -> None:
        self.result = result
        self.cursors: list[_TimezoneCursor] = []
        self.queries: list[str] = []

    def raw_sql(self, sql: str) -> _TimezoneCursor:
        self.queries.append(sql)
        cursor = _TimezoneCursor(self.result)
        self.cursors.append(cursor)
        return cursor

    def disconnect(self) -> None:
        return None


def test_mysql_named_source_timezone_requires_non_null_native_conversion() -> None:
    backend = _MySQLTimezoneBackend(None)

    with pytest.raises(DatasourceConnectionError) as failure:
        verify_source_timezone(cast("BaseBackend", backend), "America/New_York")

    assert failure.value.expected == (
        "a non-NULL native CONVERT_TZ result for the selected timezone"
    )
    assert failure.value.received == "America/New_York"
    assert failure.value.repair is not None
    assert "time zone tables" in failure.value.repair.action
    assert backend.cursors[0].closed
    assert backend.queries == [
        "SELECT CONVERT_TZ('2000-01-01 00:00:00', '+00:00', 'America/New_York') AS converted"
    ]


def test_source_session_caches_successful_timezone_verification_for_connection_lifetime() -> None:
    backend = _MySQLTimezoneBackend(datetime(1999, 12, 31, 19))

    def owner() -> SourceSession:
        return SourceSession(
            provider_for("mysql"),
            datasource("mysql", {}),
            cast("BaseBackend", backend),
            owns_backend=False,
        )

    session = owner()
    session.verify_source_timezones(("America/New_York",))
    session.verify_source_timezones(("America/New_York",))
    assert len(backend.queries) == 1
    assert provider_statement_log(cast("BaseBackend", backend))[0].purpose == "datasource.timezone"
    with owner() as other:
        other.verify_source_timezones(("America/New_York",))
        assert len(backend.queries) == 2
    session.verify_source_timezones(("America/New_York",))
    assert len(backend.queries) == 2
    session.close()
    with owner() as reopened:
        reopened.verify_source_timezones(("America/New_York",))
        assert len(backend.queries) == 3


def test_failed_timezone_verification_is_not_cached_or_replaced_with_fallback() -> None:
    backend = _MySQLTimezoneBackend(None)
    session = SourceSession(
        provider_for("mysql"),
        datasource("mysql", {}),
        cast("BaseBackend", backend),
        owns_backend=False,
    )
    with pytest.raises(DatasourceConnectionError):
        session.verify_source_timezones(("America/New_York",))
    backend.result = datetime(1999, 12, 31, 19)
    session.verify_source_timezones(("America/New_York",))
    assert len(backend.queries) == 2
    session.close()


@pytest.mark.parametrize("zone", ("UTC", "+08:00", "UTC-05:30"))
def test_mysql_utc_and_fixed_offset_sources_do_not_require_named_zone_tables(zone: str) -> None:
    backend = _MySQLTimezoneBackend(None)
    verify_source_timezone(cast("BaseBackend", backend), zone)
    assert backend.queries == []
