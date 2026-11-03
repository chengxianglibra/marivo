from __future__ import annotations

from datetime import datetime
from typing import cast

import pytest
from ibis.backends import BaseBackend

from marivo.datasource.adapters import SourceSession, provider_for
from marivo.datasource.capabilities import provider_statement_log
from marivo.datasource.errors import DatasourceConnectionError
from marivo.datasource.timezone import verify_source_timezone
from tests.datasource.source_cases import datasource


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
