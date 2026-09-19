"""Temporal authority failures and precision use independent bounded expectations."""

import re
from datetime import date, datetime, timedelta
from pathlib import Path

import ibis
import pytest

from marivo._temporal import builtin_grain
from marivo.analysis.compiler.lowering import _hour_range_violations
from marivo.analysis.compiler.temporal import bucket as bucket_start_expr
from marivo.analysis.materialization.sqlite_execution import SQLiteExecutionAdapter
from marivo.analysis.materialization.temporal_sql import (
    sqlite_localize,
    sqlite_render,
    sqlite_shift,
)
from marivo.datasource.errors import DatasourceConnectionError
from marivo.datasource.timezone import resolve_engine_timezone

DIALECTS = ("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse")

_HOUR_LITERAL = re.compile(r"^([01]?[0-9]|2[0-3])$")
_PADDING = re.compile(r"\s")
_HOUR_CELLS = (
    "15",
    "0",
    "00",
    "23",
    "99",
    "oops",
    "",
    " 15",
    "15 ",
    "15\n",
    "-3",
    "007",
    "1.5",
)


def _oracle(cell: str | None) -> bool:
    """Independent Python verdict for the 0-23 integer-literal hour contract.

    The declared pattern is the whole domain, so ``'0'``, ``'00'`` and ``'23'``
    are accepted while padded, signed or out-of-range text is not.
    """
    if cell is None:
        return True
    return _PADDING.search(cell) is not None or _HOUR_LITERAL.match(cell) is None


@pytest.mark.parametrize(
    "declared_type",
    ["string", "int64", "float64", "boolean", "date", "geospatial:geometry"],
)
def test_hour_range_predicate_compiles_on_every_supported_dialect(declared_type: str) -> None:
    """Every admitted column kind compiles without an engine-specific comparison error.

    ``date`` pins the branch ordering contract: it is judged through its text
    rendering, so reordering the branches must not fall through to a numeric
    comparison. ``boolean`` and ``geospatial:geometry`` subclass ``NumericValue``
    and must be handled before that branch.
    """
    table = ibis.table({"hour": declared_type}, name="orders")
    expression = _hour_range_violations(table, "hour")
    assert tuple(expression.columns) == ("hour",)
    for dialect in DIALECTS:
        sql = ibis.to_sql(expression.select(expr=ibis.literal(1)), dialect=dialect)
        assert sql


@pytest.mark.parametrize("engine", ["duckdb", "sqlite"])
def test_boolean_hour_cells_are_all_violations(engine: str) -> None:
    """A flag is never a 0-23 integer literal, so no cell may be published."""
    connection = ibis.duckdb.connect() if engine == "duckdb" else ibis.sqlite.connect(":memory:")
    try:
        cells: list[bool | None] = [True, False, None]
        table = connection.create_table(
            "flag_probe", ibis.memtable({"hour": cells}, schema={"hour": "boolean"})
        )
        violations = (
            _hour_range_violations(table, "hour")
            .aggregate(violations=lambda frame: frame.count())
            .to_pyarrow()
            .to_pylist()
        )
        assert violations == [{"violations": len(cells)}]
    finally:
        connection.disconnect()


def test_geometry_hour_cells_are_all_violations() -> None:
    """A geometry is never a 0-23 integer literal, so no cell may be published.

    Built through real DDL because a geospatial memtable needs the optional
    geoarrow dependency; the engine still executes the predicate.
    """
    connection = ibis.duckdb.connect()
    try:
        connection.raw_sql("CREATE TABLE geom_probe (id BIGINT, hour GEOMETRY)")
        connection.raw_sql("INSERT INTO geom_probe VALUES (1, NULL), (2, NULL)")
        violations = (
            _hour_range_violations(connection.table("geom_probe"), "hour")
            .aggregate(violations=lambda frame: frame.count())
            .to_pyarrow()
            .to_pylist()
        )
        assert violations == [{"violations": 2}]
    finally:
        connection.disconnect()


def test_date_hour_cells_are_all_violations_through_their_text_rendering() -> None:
    """A civil date is outside the domain, judged through its text rendering.

    This pins the documented branch ordering: dates reach the text branch, so
    relocating them onto a numeric comparison would raise the bare comparison
    error the earlier branches exist to remove.
    """
    connection = ibis.duckdb.connect()
    try:
        cells = [date(2026, 7, 1), date(2026, 7, 2)]
        table = connection.create_table(
            "date_probe", ibis.memtable({"hour": cells}, schema={"hour": "date"})
        )
        violations = (
            _hour_range_violations(table, "hour")
            .aggregate(violations=lambda frame: frame.count())
            .to_pyarrow()
            .to_pylist()
        )
        assert violations == [{"violations": len(cells)}]
    finally:
        connection.disconnect()


@pytest.mark.parametrize("engine", ["duckdb", "sqlite"])
def test_string_hour_cells_follow_the_declared_value_domain(engine: str) -> None:
    """Execute the predicate on both local engines; the oracle stays independent.

    SQLite previously coerced malformed cells to hour zero, so execution there
    is the regression that matters most.
    """
    connection = ibis.duckdb.connect() if engine == "duckdb" else ibis.sqlite.connect(":memory:")
    try:
        cells: list[str | None] = [*_HOUR_CELLS, None]
        table = connection.create_table(
            "hour_probe", ibis.memtable({"hour": cells}, schema={"hour": "string"})
        )
        actual = {
            row["hour"] for row in _hour_range_violations(table, "hour").to_pyarrow().to_pylist()
        }
        expected = {cell for cell in cells if _oracle(cell)}
        assert actual == expected
    finally:
        connection.disconnect()


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
    backend.con.execute("CREATE TABLE probe (ts TEXT)")
    backend.con.execute("INSERT INTO probe VALUES (?)", (wall,))
    backend.con.commit()
    adapter = SQLiteExecutionAdapter(backend)
    adapter.initialize()
    try:
        table = ibis.table({"ts": "timestamp(6)"}, name="probe")
        expression = bucket_start_expr(table["ts"], builtin_grain(unit, count=count))
        actual = adapter.read_table(table.select(b=expression))
        assert actual.column(0).to_pylist() == [expected]
    finally:
        adapter.disconnect()


def test_sqlite_integer_hour_interval_keeps_the_canonical_fraction(tmp_path: Path) -> None:
    """The composite hour axis adds an integer interval the same exact way.

    ``source_time`` composes a civil date with ``hour.as_interval("h")``, so this
    is the second shape the shift branch owns.  Hand-computed: midnight plus
    fifteen hours, with the base's fraction and the six digits both intact.
    """
    backend = ibis.sqlite.connect(tmp_path / "prefix.sqlite")
    backend.con.execute("CREATE TABLE probe (ts TEXT, hour INTEGER)")
    backend.con.execute("INSERT INTO probe VALUES ('2026-07-01 00:00:00.123456', 15)")
    backend.con.commit()
    adapter = SQLiteExecutionAdapter(backend)
    adapter.initialize()
    try:
        table = ibis.table({"ts": "timestamp(6)", "hour": "int64"}, name="probe")
        expression = table["ts"] + table["hour"].as_interval("h")
        actual = adapter.read_table(table.select(b=expression))
        assert actual.column(0).to_pylist() == [datetime(2026, 7, 1, 15, 0, 0, 123456)]
    finally:
        adapter.disconnect()
