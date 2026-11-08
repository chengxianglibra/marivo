"""Independent canonical range oracles and native parsing for other formats."""

from dataclasses import replace
from datetime import date, datetime, timedelta

import ibis
import pytest
import sqlglot
from sqlglot import expressions as sge

from marivo.analysis.compiler.source_time import source_time, source_time_predicate
from marivo.analysis.core.time_authority import CivilDateSourceTime
from marivo.semantic.ir import StrptimeParse
from tests.analysis.temporal.encoded_time_fixtures import axis
from tests.shared_fixtures import declared_test_timezone


@pytest.mark.parametrize(
    "fmt,kind",
    [
        ("%Y-%m-%d", "string"),
        ("%Y%m%d", "string"),
        ("%Y%m%d", "int64"),
        ("%Y%m%d%H", "string"),
        ("%Y%m%d%H", "int64"),
        ("%Y-%m-%d %H", "string"),
        ("%Y-%m-%d %H:%M", "string"),
        ("%Y%m%d%H%M", "string"),
        ("%Y%m%d%H%M", "int64"),
        ("%Y%m%d%H%M%S", "string"),
        ("%Y%m%d%H%M%S", "int64"),
        ("%Y-%m-%d %H:%M:%S", "string"),
    ],
)
def test_inverse_matches_independently_parsed_canonical_points(fmt: str, kind: str) -> None:
    # Include leap-day, month/year rollover, an empty window, and exact as well
    # as non-aligned boundaries. Expected membership never uses inverse code.
    windows = (
        (datetime(2024, 2, 28, 23, 30), datetime(2024, 3, 1, 0, 30)),
        (datetime(2026, 12, 31, 23, 59, 30), datetime(2027, 1, 1, 0, 0, 1)),
        (datetime(2026, 7, 1, 10, 30), datetime(2026, 7, 1, 11, 30)),
        (datetime(2026, 7, 1, 11), datetime(2026, 7, 1, 12)),
        (datetime(2026, 7, 1, 11), datetime(2026, 7, 1, 11)),
    )
    backend = ibis.duckdb.connect()
    try:
        for start, end in windows:
            texts = sorted(
                {
                    (point + timedelta(seconds=offset)).strftime(fmt)
                    for point in (start, end)
                    for offset in (-86400, -3600, -60, -1, 0, 1, 60, 3600, 86400)
                }
            )
            values = [int(text) for text in texts] if kind == "int64" else texts
            table = ibis.memtable({"point": [*values, None]}, schema={"point": kind})
            field = axis(fmt)
            _, authority = source_time(
                table.point,
                field,
                boundary_timezone="UTC",
                source_timezone=declared_test_timezone(field, "UTC"),
                engine="duckdb",
            )
            predicate = source_time_predicate(table.point, field, authority, start=start, end=end)
            assert predicate is not None
            actual = backend.execute(table.filter(predicate)).point.tolist()
            expected = [
                value
                for value, text in zip(values, texts, strict=True)
                if start <= datetime.strptime(text, fmt) < end
            ]
            assert actual == expected
    finally:
        backend.disconnect()


@pytest.mark.parametrize("dialect", ["duckdb", "postgres", "mysql", "trino", "clickhouse"])
def test_filter_ast_uses_original_column_and_keeps_parser_in_projection(dialect: str) -> None:
    table = ibis.table({"point": "string"}, name="events")
    field = axis("%Y%m%d")
    parsed, authority = source_time(
        table.point,
        field,
        boundary_timezone="UTC",
        source_timezone=declared_test_timezone(field, "UTC"),
        engine=dialect,
    )
    predicate = source_time_predicate(
        table.point, field, authority, start=date(2026, 7, 1), end=date(2026, 8, 1)
    )
    assert predicate is not None
    # Parsing remains available for output, but cannot wrap a filtering column.
    sql = ibis.to_sql(table.filter(predicate).select(point=parsed), dialect=dialect)
    ast = sqlglot.parse_one(sql, read=dialect)
    where = ast.find(sge.Where)
    assert where is not None
    comparisons = list(where.find_all(sge.GTE, sge.LT))
    assert len(comparisons) == 2
    assert all(isinstance(item.this, sge.Column) for item in comparisons)
    assert {item.expression.this for item in comparisons} == {"20260701", "20260801"}
    assert "point" in ast.expressions[0].sql(dialect=dialect)
    assert not isinstance(ast.expressions[0], sge.Column)


def test_fixed_offset_inverse_and_civil_date_do_not_adopt_report_zone() -> None:
    table = ibis.table({"point": "string"}, name="events")
    field = replace(axis("%Y-%m-%d %H:%M"), timezone=None, parse=StrptimeParse("%Y-%m-%d %H:%M"))
    _, authority = source_time(
        table.point,
        field,
        boundary_timezone="UTC",
        source_timezone=declared_test_timezone(field, "UTC+05:30"),
        engine="duckdb",
    )
    predicate = source_time_predicate(
        table.point,
        field,
        authority,
        start=datetime(2026, 7, 1, 0, 0, 30),
        end=datetime(2026, 7, 1, 1),
    )
    assert predicate is not None
    sql = ibis.to_sql(table.filter(predicate), dialect="duckdb")
    assert "2026-07-01 05:31" in sql and "2026-07-01 06:30" in sql
    field = axis("%Y%m%d")
    _, authority = source_time(
        table.point,
        field,
        boundary_timezone="America/New_York",
        source_timezone=declared_test_timezone(field, "UTC"),
        engine="duckdb",
    )
    predicate = source_time_predicate(
        table.point, field, authority, start=date(2026, 7, 1), end=date(2026, 7, 2)
    )
    assert predicate is not None


@pytest.mark.parametrize(
    "fmt,kind,zone",
    [
        ("%d/%m/%Y", "string", "UTC"),
        ("%Y%m%d", "float64", "UTC"),
        ("%Y-%m-%d", "int64", "UTC"),
    ],
)
def test_safe_membership_retains_nonordered_parser(fmt: str, kind: str, zone: str) -> None:
    table = ibis.table({"point": kind}, name="events")
    field = axis(fmt, zone)
    _, authority = source_time(
        table.point,
        field,
        boundary_timezone="UTC",
        source_timezone=declared_test_timezone(field, zone),
        engine="duckdb",
    )
    assert (
        source_time_predicate(
            table.point, field, authority, start=datetime(2026, 3, 8), end=datetime(2026, 3, 9)
        )
        is not None
    )


def test_upper_ceiling_and_integer_limits_never_emit_overflow_literal() -> None:
    table = ibis.table({"point": "int16"}, name="events")
    authority = CivilDateSourceTime(
        axis="sales.events.point",
        physical_type="int16",
        kind="civil_date",
        timezone="UTC",
        origin="datasource",
    )
    predicate = source_time_predicate(
        table.point,
        axis("%Y%m%d"),
        authority,
        start=date(2026, 7, 1),
        end=date(2026, 7, 2),
    )
    assert predicate is not None
    sql = ibis.to_sql(table.filter(predicate), dialect="trino")
    assert "FALSE" in sql
    table = ibis.table({"point": "string"}, name="events")
    field = axis("%Y-%m-%d %H:%M:%S")
    _, parsed_authority = source_time(
        table.point,
        field,
        boundary_timezone="UTC",
        source_timezone=declared_test_timezone(field, "UTC"),
        engine="duckdb",
    )
    predicate = source_time_predicate(
        table.point,
        field,
        parsed_authority,
        start=datetime(9999, 12, 31, 23, 59, 59),
        end=datetime.max,
    )
    assert predicate is not None


def test_canonical_year_padding_and_open_lower_bound() -> None:
    table = ibis.memtable({"point": ["00010101", "00010102", None]}, schema={"point": "string"})
    field = axis("%Y%m%d")
    _, authority = source_time(
        table.point,
        field,
        boundary_timezone="UTC",
        source_timezone=declared_test_timezone(field, "UTC"),
        engine="duckdb",
    )
    predicate = source_time_predicate(table.point, field, authority, start=None, end=date(1, 1, 2))
    assert predicate is not None
    backend = ibis.duckdb.connect()
    try:
        assert backend.execute(table.filter(predicate)).point.tolist() == ["00010101"]
    finally:
        backend.disconnect()
