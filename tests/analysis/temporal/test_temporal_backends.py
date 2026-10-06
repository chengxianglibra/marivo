"""Temporal authority failures and precision use independent bounded expectations."""

from datetime import datetime, timedelta
from pathlib import Path

import ibis
import ibis.expr.types as ir
import pyarrow as pa
import pytest
from ibis.backends.duckdb import Backend

from marivo._temporal import builtin_grain
from marivo.analysis.materialization.temporal_sql import (
    _initialize_sqlite_functions,
    lower_temporal,
    sqlite_localize,
    sqlite_render,
    sqlite_shift,
)
from marivo.datasource.adapters import PhysicalRequirement, SourceSession, provider_for
from marivo.datasource.errors import DatasourceConnectionError
from marivo.datasource.ir import AiContextIR, DatasourceIR, DatasourceSourceLocation, TableSourceIR
from marivo.datasource.timezone import probe_engine_timezone


def bucket_start_expr(value: ir.Value, grain) -> ir.Value:
    seconds = grain.count * {"hour": 3600, "minute": 60}[grain.unit]
    offset = ((value.hour() * 3600 + value.minute() * 60 + value.second()) // seconds) * seconds
    return value.truncate("D") + offset.as_interval("s")


@pytest.mark.parametrize(
    "name", ["UTC", "Asia/Shanghai", "America/New_York", "Asia/Kathmandu", "+05:45", "UTC-03:30"]
)
def test_engine_timezone_preserves_exact_fact(name: str) -> None:
    backend = Backend()
    backend._marivo_timezone_name = name
    result = probe_engine_timezone(backend)
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
        backend = Backend()
        backend._marivo_timezone_name = value
        probe_engine_timezone(backend)
    assert failure.value.received in ("invalid_engine_timezone", "duckdb timezone unavailable")
    assert failure.value.repair is not None


def test_sqlite_native_functions_keep_microseconds_and_nulls() -> None:
    wall = "2026-07-02 00:00:00.000001"
    assert sqlite_localize(wall, "Asia/Shanghai") == "2026-07-01 16:00:00.000001"
    assert sqlite_render("2026-07-01 16:00:00.000001", "Asia/Shanghai") == wall
    assert sqlite_shift(wall, -86400) == "2026-07-01 00:00:00.000001"
    assert sqlite_render(None, "UTC") is None
    assert sqlite_localize(None, "UTC") is None
    assert datetime.fromisoformat(wall).microsecond == 1


# Hand-computed civil-midnight grids.  Each row is a wall clock with a
# non-zero fractional second and the grid point its own day's midnight places
# it on, derived from ``//`` on that wall clock's second of day.  A grid point
# is midnight plus a whole number of seconds, so its fraction is always zero.
_SQLITE_BUCKETS = (
    ("hour", 6, "2026-06-03 13:00:00.123456", datetime(2026, 6, 3, 12, 0, 0)),
    ("hour", 6, "2026-06-03 06:59:59.999999", datetime(2026, 6, 3, 6, 0, 0)),
    ("hour", 12, "2026-06-03 13:00:00.123456", datetime(2026, 6, 3, 12, 0, 0)),
    ("hour", 12, "2026-06-03 11:59:59.999999", datetime(2026, 6, 3, 0, 0, 0)),
    ("minute", 30, "2026-06-03 13:29:59.999999", datetime(2026, 6, 3, 13, 0, 0)),
    ("minute", 30, "2026-06-03 00:29:59.123456", datetime(2026, 6, 3, 0, 0, 0)),
)


@pytest.mark.parametrize("unit,count,wall,expected", _SQLITE_BUCKETS)
def test_sqlite_multi_unit_bucket_publishes_a_canonical_grid_point(
    unit: str, count: int, wall: str, expected: datetime, tmp_path: Path
) -> None:
    """A count above one must publish the canonical six-digit grid point.

    The bucket start is its own day's midnight plus a whole number of seconds,
    so the runtime requires the canonical ``YYYY-MM-DD HH:MM:SS.ffffff`` text
    the contract names.  SQLite's ``DATETIME(..., 'subsec')`` renders only
    three fractional digits, so an interval addition lowered through it
    truncates the fraction, and the runtime rejects the row with
    ``invalid timestamp representation`` instead of publishing a bucket.
    """
    backend = ibis.sqlite.connect(tmp_path / "buckets.sqlite")
    backend.con.execute("CREATE TABLE probe (ts TIMESTAMP(6))")
    backend.con.execute("INSERT INTO probe VALUES (?)", (wall,))
    backend.con.commit()
    _initialize_sqlite_functions(backend.con)
    datasource = DatasourceIR(
        "temporal",
        "temporal",
        "sqlite",
        {"path": "unused"},
        {},
        AiContextIR(),
        "temporal",
        DatasourceSourceLocation("temporal.py", 1),
    )
    with SourceSession(provider_for("sqlite"), datasource, backend) as session:
        bound = session.bind(TableSourceIR("probe"), source_identity="probe")
        qualified = session.qualify(
            bound, PhysicalRequirement("bucket", 1, frozenset({"scan", "project"}))
        )
        table = bound.relation
        expression = bucket_start_expr(table["ts"], builtin_grain(unit, count=count))
        projected = lower_temporal(table.select(b=expression), "sqlite")
        assert isinstance(projected, ir.Table)
        read = session.compile(
            qualified, projected, purpose="bucket", expected_schema=projected.schema().to_pyarrow()
        )
        stream = session.batches(read, chunk_size=1)
        try:
            actual = pa.Table.from_batches(stream, schema=stream.schema)
            assert actual.column(0).to_pylist() == [expected]
        finally:
            stream.close()


def test_sqlite_integer_hour_interval_keeps_the_canonical_fraction(tmp_path: Path) -> None:
    """The composite hour axis adds an integer interval the same exact way.

    ``source_time`` composes a civil date with ``hour.as_interval("h")``, so this
    is the second shape the shift branch owns.  Hand-computed: midnight plus
    fifteen hours, with the base's fraction and the six digits both intact.
    """
    backend = ibis.sqlite.connect(tmp_path / "prefix.sqlite")
    backend.con.execute("CREATE TABLE probe (ts TIMESTAMP(6), hour INTEGER)")
    backend.con.execute("INSERT INTO probe VALUES ('2026-07-01 00:00:00.123456', 15)")
    backend.con.commit()
    _initialize_sqlite_functions(backend.con)
    datasource = DatasourceIR(
        "temporal",
        "temporal",
        "sqlite",
        {"path": "unused"},
        {},
        AiContextIR(),
        "temporal",
        DatasourceSourceLocation("temporal.py", 1),
    )
    with SourceSession(provider_for("sqlite"), datasource, backend) as session:
        bound = session.bind(TableSourceIR("probe"), source_identity="probe")
        qualified = session.qualify(
            bound, PhysicalRequirement("shift", 1, frozenset({"scan", "project"}))
        )
        table = bound.relation
        expression = table["ts"] + table["hour"].as_interval("h")
        projected = lower_temporal(table.select(b=expression), "sqlite")
        assert isinstance(projected, ir.Table)
        read = session.compile(
            qualified, projected, purpose="shift", expected_schema=projected.schema().to_pyarrow()
        )
        stream = session.batches(read, chunk_size=1)
        try:
            actual = pa.Table.from_batches(stream, schema=stream.schema)
            assert actual.column(0).to_pylist() == [datetime(2026, 7, 1, 15, 0, 0, 123456)]
        finally:
            stream.close()
