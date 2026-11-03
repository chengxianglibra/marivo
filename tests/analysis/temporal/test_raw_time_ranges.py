"""Physical range predicates preserve typed cutoffs without wrapping source columns."""

from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import ibis
import ibis.expr.operations as ops
import pytest
import sqlglot
from sqlglot import expressions as sge

from marivo.analysis.compiler.source_time import (
    TimeComparison,
    source_time,
    source_time_comparison,
    source_time_predicate,
)
from marivo.analysis.materialization.temporal_sql import lower_temporal
from marivo.semantic._source_timezone import DeclaredSourceTimezone
from marivo.semantic.ir import DateParse, HourPrefixParse, StrptimeParse, TimestampParse
from tests.analysis.temporal.encoded_time_fixtures import axis
from tests.analysis.temporal.test_calendar_native_keys import _maria
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.shared_fixtures import rendered_help


@pytest.mark.parametrize("engine", ["duckdb", "postgres", "mysql", "trino", "clickhouse"])
@pytest.mark.parametrize("physical", ["date", "timestamp(6)", "string", "int64"])
def test_shanghai_day_compares_original_column_on_every_backend(engine: str, physical: str) -> None:
    table = ibis.table({"point": physical}, name="events")
    parse = (
        DateParse(timezone="Asia/Shanghai")
        if physical == "date"
        else TimestampParse(timezone=None if engine == "clickhouse" else "Asia/Shanghai")
        if physical == "timestamp(6)"
        else StrptimeParse("%Y%m%d", timezone="Asia/Shanghai")
    )
    field = replace(axis("%Y%m%d"), parse=parse)
    _, authority = source_time(
        table.point,
        field,
        boundary_timezone="Asia/Shanghai",
        source_timezone=DeclaredSourceTimezone("Asia/Shanghai", "field"),
        engine=engine,
    )
    predicate = source_time_predicate(
        table.point,
        field,
        authority,
        start=datetime(2026, 10, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
        end=datetime(2026, 10, 29, tzinfo=ZoneInfo("Asia/Shanghai")),
        engine=engine,
    )
    assert predicate is not None
    sql = str(ibis.to_sql(lower_temporal(table.filter(predicate), engine), dialect=engine))
    ast = sqlglot.parse_one(sql, read=engine)
    where = ast.find(sge.Where)
    assert where is not None
    comparisons = list(where.find_all(sge.GTE, sge.LT))
    assert len(comparisons) == 2, sql
    assert all(isinstance(item.this, sge.Column) for item in comparisons), sql
    if engine == "clickhouse" and physical == "date":
        assert "toDate('2026-10-28')" in sql
        assert "toDate('2026-10-29')" in sql
        assert "toDate32(" not in sql
    if physical in ("string", "int64"):
        assert {item.expression.this for item in comparisons} == {"20261028", "20261029"}
        assert all(item.expression.is_string == (physical == "string") for item in comparisons)


@pytest.mark.parametrize(
    "bound,comparison,expected_literal",
    [
        (date(1969, 12, 31), ">=", "toDate32('1969-12-31')"),
        (date(1970, 1, 1), ">=", "toDate('1970-01-01')"),
        (date(2149, 6, 6), ">=", "toDate('2149-06-06')"),
        (date(2149, 6, 7), ">=", "toDate32('2149-06-07')"),
        (date(1969, 12, 31), ">", "toDate('1970-01-01')"),
        (date(2149, 6, 6), ">", "toDate32('2149-06-07')"),
        (datetime(1969, 12, 31, 12, tzinfo=timezone.utc), ">=", "toDate('1970-01-01')"),
        (datetime(2149, 6, 6, 12, tzinfo=timezone.utc), ">=", "toDate32('2149-06-07')"),
    ],
)
def test_clickhouse_date_cutoffs_choose_literal_after_rounding(
    bound: date | datetime, comparison: TimeComparison, expected_literal: str
) -> None:
    table = ibis.table({"point": "date"}, name="events")
    field = replace(axis("%Y%m%d"), parse=DateParse(timezone="UTC"))
    _, authority = source_time(
        table.point,
        field,
        boundary_timezone="UTC",
        source_timezone=DeclaredSourceTimezone("UTC", "field"),
        engine="clickhouse",
    )
    predicate = source_time_comparison(
        table.point, field, authority, bound=bound, comparison=comparison, engine="clickhouse"
    )
    assert predicate is not None
    sql = str(
        ibis.to_sql(lower_temporal(table.filter(predicate), "clickhouse"), dialect="clickhouse")
    )
    where = sqlglot.parse_one(sql, read="clickhouse").find(sge.Where)
    assert where is not None and isinstance(where.this, sge.GTE)
    assert isinstance(where.this.this, sge.Column), sql
    assert expected_literal in sql


@pytest.mark.parametrize(
    "fmt,values,start,end,expected",
    [
        (
            "%Y%m%d%H",
            ["2026102810", "2026102811", "2026102812", "2026102813"],
            "2026-10-28T10:30:00",
            "2026-10-28T12:30:00",
            ["2026102811", "2026102812"],
        ),
        (
            "%Y%m%d",
            ["20261028", "20261029", "20261030"],
            "2026-10-28T00:00:00",
            "2026-10-29T00:00:00",
            ["20261029"],
        ),
    ],
)
def test_raw_cutoffs_round_to_source_ticks_and_respect_source_timezone(
    fmt: str, values: list[str], start: str, end: str, expected: list[str]
) -> None:
    backend = ibis.duckdb.connect()
    try:
        table = backend.create_table("events", ibis.memtable({"point": [*values, None]}))
        field = replace(axis(fmt), parse=StrptimeParse(fmt, timezone="Asia/Shanghai"))
        _, authority = source_time(
            table.point,
            field,
            boundary_timezone="UTC",
            source_timezone=DeclaredSourceTimezone("Asia/Shanghai", "field"),
            engine="duckdb",
        )
        zone = timezone.utc if fmt == "%Y%m%d" else ZoneInfo("Asia/Shanghai")
        predicate = source_time_predicate(
            table.point,
            field,
            authority,
            start=datetime.fromisoformat(start).replace(tzinfo=zone),
            end=datetime.fromisoformat(end).replace(tzinfo=zone),
        )
        assert predicate is not None
        assert backend.execute(table.filter(predicate)).point.tolist() == expected
        assert all(
            isinstance(item.left, ops.Field)
            for item in predicate.op().find((ops.GreaterEqual, ops.Less))
        )
    finally:
        backend.disconnect()


def test_strptime_help_declares_ordered_formats_as_canonical_source_contracts() -> None:
    text = rendered_help("semantic.strptime")
    assert "fixed-width" in text
    assert "zero-padded" in text
    assert "original column" in text
    assert "No partition enumeration or validation queries" in text


@pytest.mark.runtime
def test_clickhouse_date32_historical_range_uses_date32_cutoffs() -> None:
    from tests.datasource.environment.clickhouse_analysis import connection

    table = ibis.table({"point": "date"}, name="events")
    field = replace(axis("%Y%m%d"), parse=DateParse(timezone="Asia/Shanghai"))
    _, authority = source_time(
        table.point,
        field,
        boundary_timezone="UTC",
        source_timezone=DeclaredSourceTimezone("Asia/Shanghai", "field"),
        engine="clickhouse",
    )
    predicate = source_time_predicate(
        table.point,
        field,
        authority,
        start=datetime(1960, 1, 2, tzinfo=ZoneInfo("Asia/Shanghai")),
        end=datetime(1960, 1, 3, tzinfo=ZoneInfo("Asia/Shanghai")),
        engine="clickhouse",
    )
    assert predicate is not None
    sql = str(
        ibis.to_sql(lower_temporal(table.filter(predicate), "clickhouse"), dialect="clickhouse")
    ).replace('FROM "events"', "FROM (SELECT toDate32('1960-01-02') AS point)")
    assert "toDate32('1960-01-02')" in sql
    assert "toDate32('1960-01-03')" in sql
    with connection(port=18323) as client:
        assert client.query(sql).result_rows == [(date(1960, 1, 2),)]


@pytest.mark.parametrize("comparison", [">=", ">", "<", "<=", "==", "!="])
@pytest.mark.parametrize("minute", [0, 30])
def test_canonical_comparisons_keep_strictness_and_nullable_complements(
    comparison: TimeComparison, minute: int
) -> None:
    import operator

    operations = {
        ">=": operator.ge,
        ">": operator.gt,
        "<": operator.lt,
        "<=": operator.le,
        "==": operator.eq,
        "!=": operator.ne,
    }
    values = ["2026102810", "2026102811", "2026102812"]
    bound = datetime(2026, 10, 28, 11, minute)
    backend = ibis.duckdb.connect()
    try:
        table = backend.create_table("events", ibis.memtable({"point": [*values, None]}))
        field = axis("%Y%m%d%H")
        _, authority = source_time(
            table.point,
            field,
            boundary_timezone="UTC",
            source_timezone=DeclaredSourceTimezone("UTC", "field"),
            engine="duckdb",
        )
        predicate = source_time_comparison(
            table.point, field, authority, bound=bound, comparison=comparison
        )
        assert predicate is not None
        expected = [
            value
            for value in values
            if operations[comparison](datetime.strptime(value, "%Y%m%d%H"), bound)
        ]
        complement = [value for value in values if value not in expected]
        assert backend.execute(table.filter(predicate)).point.tolist() == expected
        assert backend.execute(table.filter(~predicate)).point.tolist() == complement
        assert "STRPTIME" not in backend.compile(table.filter(predicate)).upper()
    finally:
        backend.disconnect()


@pytest.mark.parametrize("integer", [False, True])
def test_hour_prefix_ranges_compare_both_original_encoded_columns(integer: bool) -> None:
    days = (
        [20261028, 20261028, 20261029, 20261029, None]
        if integer
        else ["20261028", "20261028", "20261029", "20261029", None]
    )
    backend = ibis.duckdb.connect()
    try:
        table = backend.create_table(
            "events",
            ibis.memtable(
                {"id": [1, 2, 3, 4, 5], "day": days, "hour": ["22", "23", "00", "01", "00"]},
                schema={"id": "int64", "day": "int64" if integer else "string", "hour": "string"},
            ),
        )
        prefix_axis = replace(
            axis("%Y%m%d"), parse=StrptimeParse("%Y%m%d", timezone="Asia/Shanghai")
        )
        prefix, _ = source_time(
            table.day,
            prefix_axis,
            boundary_timezone="UTC",
            source_timezone=DeclaredSourceTimezone("Asia/Shanghai", "field"),
            engine="duckdb",
        )
        field = replace(axis("%Y%m%d%H"), parse=HourPrefixParse(prefix_axis.ref.path))
        _, authority = source_time(
            table.hour,
            field,
            boundary_timezone="UTC",
            source_timezone=DeclaredSourceTimezone("Asia/Shanghai", "field", prefix_axis.ref.path),
            engine="duckdb",
            prefix=prefix,
        )
        predicate = source_time_predicate(
            table.hour,
            field,
            authority,
            start=datetime(2026, 10, 28, 22, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
            end=datetime(2026, 10, 29, 0, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
            prefix=table.day,
            prefix_axis=prefix_axis,
        )
        assert predicate is not None
        assert backend.execute(table.filter(predicate)).id.tolist() == [2, 3]
        assert "STRPTIME" not in backend.compile(table.filter(predicate)).upper()
    finally:
        backend.disconnect()


@pytest.mark.parametrize(
    "backend,profile",
    source_params(
        [
            ("duckdb", "table"),
            ("postgres", "table"),
            ("mysql", "innodb-table"),
            ("mysql", "mariadb"),
            ("trino", "iceberg"),
            ("clickhouse", "mergetree"),
        ]
    ),
)
@pytest.mark.parametrize("physical", ["date", "timestamp", "string", "integer"])
def test_native_shanghai_ranges_match_independent_membership_and_original_predicate(
    backend: str,
    profile: str,
    physical: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _maria(profile, monkeypatch)
    points: list[str | int | None]
    if physical == "timestamp":
        sql_type = "DATETIME(3)" if backend == "mysql" else "TIMESTAMP(3)"
        clickhouse_type = "DateTime64(3, 'UTC')"
        points = (
            [
                "2026-10-27 15:59:59.999",
                "2026-10-27 16:00:00",
                "2026-10-28 15:59:59.999",
                "2026-10-28 16:00:00",
            ]
            if backend == "clickhouse"
            else [
                "2026-10-27 23:59:59.999",
                "2026-10-28 00:00:00",
                "2026-10-28 23:59:59.999",
                "2026-10-29 00:00:00",
            ]
        )
        parse = TimestampParse(timezone=None if backend == "clickhouse" else "Asia/Shanghai")
    elif physical == "date":
        sql_type, clickhouse_type = "DATE", "Date"
        points = ["2026-10-27", "2026-10-28", "2026-10-28", "2026-10-29"]
        parse = DateParse(timezone="Asia/Shanghai")
    else:
        sql_type, clickhouse_type = (
            ("BIGINT", "Int64") if physical == "integer" else ("VARCHAR(8)", "String")
        )
        points = (
            [20261027, 20261028, 20261028, 20261029]
            if physical == "integer"
            else ["20261027", "20261028", "20261028", "20261029"]
        )
        parse = StrptimeParse("%Y%m%d", timezone="Asia/Shanghai")
    points.append(None)
    rows: list[dict[str, object]] = [
        {"id": index, "point": point} for index, point in enumerate(points, start=1)
    ]
    if backend == "clickhouse" and physical in ("date", "timestamp"):
        for row in rows:
            point = row["point"]
            if isinstance(point, str):
                row["point"] = (
                    date.fromisoformat(point)
                    if physical == "date"
                    else datetime.fromisoformat(point).replace(tzinfo=timezone.utc)
                )
    literals = ["NULL" if point is None else repr(point) for point in points]
    if backend == "trino" and physical in ("date", "timestamp"):
        literals = [f"CAST({literal} AS {sql_type})" for literal in literals]
    values = ",".join(f"({index},{literal})" for index, literal in enumerate(literals, start=1))
    data = SourceData(
        f"id BIGINT, point {sql_type}",
        values,
        f"id Int64, point Nullable({clickhouse_type})",
        rows,
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        table = case.session.bind(case.source, source_identity="raw-source-time").relation
        field = replace(axis("%Y%m%d"), parse=parse)
        projected, authority = source_time(
            table.point,
            field,
            boundary_timezone="Asia/Shanghai",
            source_timezone=DeclaredSourceTimezone("Asia/Shanghai", "field"),
            engine=backend,
        )
        start = datetime(2026, 10, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
        end = datetime(2026, 10, 29, tzinfo=ZoneInfo("Asia/Shanghai"))
        predicate = source_time_predicate(
            table.point, field, authority, start=start, end=end, engine=backend
        )
        assert predicate is not None
        query = lower_temporal(table.filter(predicate).select("id").order_by("id"), backend)
        actual = case.session.collect_bounded(query, purpose="time.raw_range", max_rows=6)
        assert actual.id.tolist() == [2, 3]
        sql = case.session.submissions[-1].sql
        ast = sqlglot.parse_one(sql, read=backend)
        where = ast.find(sge.Where)
        assert where is not None
        comparisons = list(where.find_all(sge.GTE, sge.LT))
        assert len(comparisons) == 2 and all(
            isinstance(item.this, sge.Column) for item in comparisons
        ), sql
