from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest

from marivo.datasource.runtime import DatasourceConnectionService
from marivo.datasource.timezone import probe_engine_timezone


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
