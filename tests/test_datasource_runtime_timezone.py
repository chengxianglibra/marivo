from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo

import pytest

from marivo.datasource.runtime import DatasourceConnectionService
from marivo.datasource.timezone import probe_engine_timezone


@dataclass
class _SqlResult:
    value: object

    def execute(self) -> list[tuple[object]]:
        return [(self.value,)]


class _Backend:
    def __init__(self, *, name: str, value: object = "Asia/Shanghai", fails: bool = False) -> None:
        self.name = name
        self.value = value
        self.fails = fails
        self.sql_calls: list[str] = []

    def sql(self, query: str) -> _SqlResult:
        self.sql_calls.append(query)
        if self.fails:
            raise RuntimeError("probe failed")
        return _SqlResult(self.value)


def test_probe_engine_timezone_uses_duckdb_current_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "UTC")
    backend = _Backend(name="duckdb", value="Asia/Shanghai")

    resolved = probe_engine_timezone(backend)

    assert resolved.engine_timezone_name == "Asia/Shanghai"
    assert resolved.engine_timezone_tz == ZoneInfo("Asia/Shanghai")
    assert resolved.read_tz_resolution == "engine"
    assert backend.sql_calls == ["select current_setting('TimeZone') as timezone"]


def test_probe_engine_timezone_uses_system_fallback_for_bigquery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    backend = _Backend(name="bigquery", value="UTC")

    resolved = probe_engine_timezone(backend)

    assert resolved.engine_timezone_name == "Asia/Tokyo"
    assert resolved.read_tz_resolution == "system_fallback"
    assert backend.sql_calls == []


def test_probe_engine_timezone_uses_system_fallback_when_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TZ", "UTC")
    backend = _Backend(name="clickhouse", fails=True)

    resolved = probe_engine_timezone(backend)

    assert resolved.engine_timezone_name == "UTC"
    assert resolved.read_tz_resolution == "system_fallback"
    assert resolved.warning == "engine timezone probe failed: probe failed"


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
    assert backend.sql_calls == ["select current_setting('TimeZone') as timezone"]


def test_probe_engine_timezone_resolves_presto_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "UTC")
    backend = _Backend(name="presto", value="Asia/Shanghai")

    resolved = probe_engine_timezone(backend)

    assert resolved.engine_timezone_name == "Asia/Shanghai"
    assert resolved.read_tz_resolution == "engine"
    assert backend.sql_calls == ["select current_timezone() as timezone"]


def test_probe_engine_timezone_does_not_probe_snowflake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    backend = _Backend(name="snowflake", value="UTC")

    resolved = probe_engine_timezone(backend)

    assert resolved.engine_timezone_name == "Asia/Tokyo"
    assert resolved.read_tz_resolution == "system_fallback"
    assert backend.sql_calls == []


def test_probe_engine_timezone_skips_mysql_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    backend = _Backend(name="mysql", value="UTC")

    resolved = probe_engine_timezone(backend)

    assert resolved.engine_timezone_name == "Asia/Tokyo"
    assert resolved.read_tz_resolution == "system_fallback"
    assert backend.sql_calls == []
