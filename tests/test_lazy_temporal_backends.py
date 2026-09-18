"""Temporal authority failures and precision use independent bounded expectations."""

from datetime import datetime, timedelta

import pytest

from marivo.analysis.materialization.temporal_sql import (
    sqlite_localize,
    sqlite_render,
    sqlite_shift,
)
from marivo.datasource.errors import DatasourceConnectionError
from marivo.datasource.timezone import resolve_engine_timezone


@pytest.mark.parametrize(
    "name", ["UTC", "Asia/Shanghai", "America/New_York", "Asia/Kathmandu", "+05:45", "UTC-03:30"]
)
def test_engine_timezone_preserves_exact_fact(name: str) -> None:
    result = resolve_engine_timezone("probe", lambda query: name)
    assert result.read_tz_resolution == "engine"
    assert result.engine_timezone_name == ("UTC" + name if name.startswith("+") else name)
    if name == "+05:45":
        assert result.engine_timezone_tz.utcoffset(None) == timedelta(hours=5, minutes=45)


@pytest.mark.parametrize("value", [None, "", "Invalid/Timezone", "+25:00", "+01:70", 0])
def test_invalid_engine_fact_never_uses_system_timezone(
    value: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TZ", "UTC")
    with pytest.raises(DatasourceConnectionError) as failure:
        resolve_engine_timezone("probe", lambda query: value)
    assert failure.value.received == "invalid_engine_timezone"
    assert failure.value.repair is not None


def test_absent_probe_is_distinct_from_failed_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "Asia/Kathmandu")
    cause = RuntimeError("do not expose this message")

    def fail(query: str) -> object:
        raise cause

    assert resolve_engine_timezone(None, fail).read_tz_resolution == "system_fallback"
    with pytest.raises(DatasourceConnectionError) as failure:
        resolve_engine_timezone("probe", fail)
    assert failure.value.__cause__ is cause
    assert "do not expose" not in str(failure.value)


def test_sqlite_native_functions_keep_microseconds_and_nulls() -> None:
    wall = "2026-07-02 00:00:00.000001"
    assert sqlite_localize(wall, "Asia/Shanghai") == "2026-07-01 16:00:00.000001"
    assert sqlite_render("2026-07-01 16:00:00.000001", "Asia/Shanghai") == wall
    assert sqlite_shift(wall, -86400) == "2026-07-01 00:00:00.000001"
    assert sqlite_render(None, "UTC") is None
    assert sqlite_localize(None, "UTC") is None
    assert datetime.fromisoformat(wall).microsecond == 1
