"""Temporal authority failures and precision use independent bounded expectations."""

from datetime import timedelta

import ibis.expr.types as ir
import pytest
from ibis.backends.duckdb import Backend

from marivo.datasource.errors import DatasourceConnectionError
from marivo.datasource.timezone import probe_engine_timezone


def bucket_start_expr(value: ir.Value, grain) -> ir.Value:
    seconds = grain.count * {"hour": 3600, "minute": 60}[grain.unit]
    offset = ((value.hour() * 3600 + value.minute() * 60 + value.second()) // seconds) * seconds
    return value.truncate("D") + offset.as_interval("s")


@pytest.mark.parametrize("name", ["UTC", "America/New_York", "+05:45", "UTC-03:30"])
def test_engine_timezone_preserves_exact_fact(name: str) -> None:
    backend = Backend()
    backend._marivo_timezone_name = name
    result = probe_engine_timezone(backend)
    assert result.read_tz_resolution == "engine"
    assert result.engine_timezone_name == ("UTC" + name if name.startswith("+") else name)
    if name == "+05:45":
        assert result.engine_timezone_tz.utcoffset(None) == timedelta(hours=5, minutes=45)


@pytest.mark.parametrize("value", [None, "Invalid/Timezone", "+25:00", "+01:70"])
def test_invalid_engine_fact_never_uses_system_timezone(
    value: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TZ", "UTC")
    with pytest.raises(DatasourceConnectionError) as failure:
        backend = Backend()
        backend._marivo_timezone_name = value
        probe_engine_timezone(backend)
    assert failure.value.received in ("invalid_engine_timezone", "duckdb timezone unavailable")
    assert failure.value.repair is not None
