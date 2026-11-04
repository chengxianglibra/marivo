"""DATE day keys and projected predicates retain native time identities."""

import operator
import os
from datetime import date, datetime, time, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import ibis
import ibis.expr.operations as ops
import pandas as pd
import pytest
import sqlglot
import sqlglot.expressions as sge

import marivo.analysis as mv
from marivo.analysis.compiler.calendar import KEY_TYPE, builtin_key
from marivo.analysis.dsl.temporal import exact_scope
from marivo.analysis.materialization.date_day_keys import deferred_day_key
from marivo.analysis.materialization.temporal_sql import lower_temporal
from marivo.datasource._temporal_expressions import localize, render, timestamp


@pytest.mark.parametrize(
    "source_zone,report_zone,start,end,fast",
    [
        ("Asia/Shanghai", "Asia/Shanghai", "2026-10-28", "2026-11-04", True),
        ("UTC", "UTC", "1960-01-01", "1960-01-04", True),
        ("America/New_York", "America/New_York", "2026-07-01", "2026-07-04", True),
        ("Asia/Shanghai", "UTC", "2026-10-28", "2026-11-04", False),
        # Endpoints are stable, but the selected range contains a clock change.
        ("America/New_York", "America/New_York", "2026-10-26", "2026-11-09", False),
        ("Pacific/Apia", "Pacific/Apia", "2011-12-26", "2012-01-04", False),
        ("America/Havana", "America/Havana", "2026-03-05", "2026-03-11", False),
        ("Asia/Kathmandu", "Asia/Kathmandu", "1985-12-28", "1986-01-05", False),
    ],
)
def test_date_day_keys_match_native_civil_grouping(
    source_zone: str, report_zone: str, start: str, end: str, fast: bool
) -> None:
    calendar = exact_scope(start, end, report_zone).by(mv.grain("day"))._binding
    backend = ibis.duckdb.connect(TimeZone="UTC")
    try:
        first, stop = date.fromisoformat(start), date.fromisoformat(end)
        days = [first + timedelta(days=i) for i in range((stop - first).days)]
        table = backend.create_table(
            "business_days", ibis.memtable({"point": [*days, None]}, schema={"point": "date"})
        )
        clock = localize(source_zone, timestamp(table.point))
        key = builtin_key(clock, calendar, date_timezone=source_zone)
        deferred = deferred_day_key(table, table.point, calendar, source_zone, "duckdb")
        assert (deferred is not None) == fast
        if deferred is not None:
            marked = table.mutate(bucket=deferred)
            query = marked.group_by("bucket").aggregate(n=marked.count()).order_by("bucket")
            lowered = lower_temporal(query, "duckdb")
            original = table.select(bucket=key)
            expected_groups = backend.execute(
                original.group_by("bucket").aggregate(n=original.count()).order_by("bucket")
            )
            pd.testing.assert_frame_equal(backend.execute(lowered), expected_groups)
        assert key.type() == KEY_TYPE
        actual = backend.execute(table.select(key=key)).key
        expected = (
            backend.con.execute(
                "SELECT timezone(?, date_trunc('day', timezone(?, timezone(?, point::TIMESTAMP)))) "
                "FROM business_days",
                [report_zone, report_zone, source_zone],
            )
            .fetchdf()
            .iloc[:, 0]
        )
        assert actual.tolist() == expected.tolist()
        sql = str(
            ibis.to_sql(lower_temporal(table.select(key=key), "clickhouse"), dialect="clickhouse")
        )
        assert ("formatDateTime" not in sql) == fast
    finally:
        backend.disconnect()


@pytest.mark.parametrize("zone", ["UTC", "Asia/Shanghai", "America/New_York"])
@pytest.mark.parametrize("comparison", ["ge", "gt", "lt", "le", "eq", "ne"])
@pytest.mark.parametrize("minute", [0, 30])
def test_projected_date_comparisons_preserve_strictness_null_and_boolean_structure(
    zone: str, comparison: str, minute: int
) -> None:
    compare = getattr(operator, comparison)
    bound = datetime(2026, 7, 2, 0, minute, tzinfo=ZoneInfo(zone))
    days = [date(2026, 7, day) for day in (1, 2, 3)]
    backend = ibis.duckdb.connect(TimeZone="UTC")
    try:
        table = backend.create_table(
            "business_days",
            ibis.memtable(
                {"id": [0, 1, 2, 3], "point": [*days, None]},
                schema={"id": "int64", "point": "date"},
            ),
        )
        projected = table.mutate(clock=localize(zone, timestamp(table.point))).view()
        condition = compare(projected.clock, bound)
        expected = [
            i
            for i, day in enumerate(days)
            if compare(datetime.combine(day, time(), ZoneInfo(zone)), bound)
        ]
        for predicate, ids in (
            (condition, expected),
            (~condition, [i for i in range(3) if i not in expected]),
            (condition | (projected.id == 3), [*expected, 3]),
            (condition & (projected.id != 0), [i for i in expected if i != 0]),
        ):
            query = projected.filter(predicate).select("id").order_by("id")
            optimized = lower_temporal(query, "duckdb")
            assert backend.execute(optimized).id.tolist() == ids
            sql = str(ibis.to_sql(optimized, dialect="duckdb"))
            for where in sqlglot.parse_one(sql, read="duckdb").find_all(sge.Where):
                assert "clock" not in where.sql()
    finally:
        backend.disconnect()


@pytest.mark.parametrize("engine", ["duckdb", "postgres", "mysql", "trino", "clickhouse"])
@pytest.mark.parametrize("day", ["1960-01-02", "2026-10-30"])
def test_projected_date_range_uses_original_typed_column(engine: str, day: str) -> None:
    table = ibis.table({"point": "date"}, name="events")
    projected = table.mutate(clock=localize("Asia/Shanghai", timestamp(table.point)))
    start = datetime.fromisoformat(day).replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    query = projected.filter(
        (projected.clock >= start) & (projected.clock < start + timedelta(days=1))
    )
    sql = str(ibis.to_sql(lower_temporal(query, engine), dialect=engine))
    where = sqlglot.parse_one(sql, read=engine).find(sge.Where)
    assert where is not None
    # MySQL's historical timezone conversion is deliberately unqualified.
    if engine == "mysql" and day.startswith("1960"):
        assert "clock" in where.sql()
    else:
        comparisons = list(where.find_all(sge.GTE, sge.LT))
        assert len(comparisons) == 2
        assert all(
            isinstance(item.this, sge.Column) and item.this.name == "point" for item in comparisons
        )
        if engine == "clickhouse":
            assert ("toDate32(" in sql) == day.startswith("1960")


def test_date_filter_keeps_native_predicate_near_skipped_day() -> None:
    table = ibis.table({"point": "date"}, name="events")
    projected = table.mutate(clock=localize("Pacific/Apia", timestamp(table.point)))
    bound = datetime(2011, 12, 30, 10, tzinfo=timezone.utc)
    query = projected.filter(projected.clock >= bound)
    optimized = lower_temporal(query, "duckdb")
    assert any(
        isinstance(item.left, ops.Field) and item.left.name == "clock"
        for item in optimized.op().find(ops.GreaterEqual)
    )


def test_rendered_date_clock_does_not_acquire_instant_comparison_semantics() -> None:
    backend = ibis.duckdb.connect(TimeZone="UTC")
    try:
        table = backend.create_table(
            "business_days", ibis.memtable({"point": [date(2026, 7, day) for day in (1, 2, 3)]})
        )
        clock = render("Asia/Shanghai", localize("Asia/Shanghai", timestamp(table.point)))
        projected = table.mutate(clock=clock)
        query = projected.filter(projected.clock >= datetime(2026, 7, 2)).select("point")
        assert backend.execute(lower_temporal(query, "duckdb")).point.dt.date.tolist() == [
            date(2026, 7, 2),
            date(2026, 7, 3),
        ]
    finally:
        backend.disconnect()


@pytest.mark.parametrize("literal", ["2026-10-30", "20261030", "2026-1-2"])
def test_unbound_clickhouse_strings_do_not_acquire_inferred_utc_cutoffs(literal: str) -> None:
    table = ibis.table({"point": "date"}, name="events")
    projected = table.mutate(clock=localize("Asia/Shanghai", timestamp(table.point)))
    query = projected.filter(projected.clock >= literal)
    sql = str(ibis.to_sql(lower_temporal(query, "clickhouse"), dialect="clickhouse"))
    where = sqlglot.parse_one(sql, read="clickhouse").find(sge.Where)
    assert where is not None
    assert "clock" in where.sql()


@pytest.mark.runtime
@pytest.mark.parametrize("historical", [False, True])
def test_clickhouse_date_axes_prune_partitions(historical: bool) -> None:
    if os.environ.get("MARIVO_CLICKHOUSE_ANALYSIS_TEST") != "1":
        pytest.skip("Explicit ready ClickHouse service required")
    from tests.datasource.environment.clickhouse_analysis import connection

    name = "date_pruning_" + uuid4().hex
    rows_per_day = 8192
    day_type = "Date32" if historical else "Date"
    day_function = "toDate32" if historical else "toDate"
    first = "1960-10-25" if historical else "2026-10-25"
    with connection(admin=True) as admin:
        admin.command(
            f"CREATE TABLE {name} (point {day_type}, amount Int64) "
            "ENGINE=MergeTree PARTITION BY point ORDER BY point"
        )
        try:
            admin.command(
                f"INSERT INTO {name} SELECT addDays({day_function}('{first}'), toInt32(intDiv(number, {rows_per_day}))), "
                f"toInt64(number % 97) FROM numbers({10 * rows_per_day})"
            )
            with connection() as reader:
                table = ibis.table({"point": "date", "amount": "int64"}, name=name)
                projected = table.mutate(clock=localize("Asia/Shanghai", timestamp(table.point)))
                start = datetime(
                    1960 if historical else 2026, 10, 26, tzinfo=ZoneInfo("Asia/Shanghai")
                )
                for days in (1, 2, 4, 7):
                    end = start + timedelta(days=days)
                    filtered = projected.filter(
                        (projected.clock >= start) & (projected.clock < end)
                    )
                    coordinate = (
                        exact_scope(
                            start,
                            end,
                            "Asia/Shanghai",
                        )
                        .by(mv.grain("day"))
                        ._binding
                    )
                    key = deferred_day_key(
                        filtered, filtered.point, coordinate, "Asia/Shanghai", "clickhouse"
                    )
                    assert key is not None
                    calendar = filtered.mutate(bucket=key)
                    native_start = start.date()
                    native_end = end.date()
                    native = reader.query(
                        f"SELECT point, sum(amount), quantile(0.25)(amount), quantile(0.5)(amount), "
                        f"quantile(0.75)(amount), quantile(0.95)(amount) FROM {name} "
                        f"WHERE point >= {day_function}('{native_start}') AND point < {day_function}('{native_end}') "
                        "GROUP BY point ORDER BY point"
                    )
                    for source, key in ((filtered, "clock"), (calendar, "bucket")):
                        query = (
                            source.group_by(source[key])
                            .aggregate(
                                total=source.amount.sum(),
                                q25=source.amount.quantile(0.25),
                                q50=source.amount.quantile(0.5),
                                q75=source.amount.quantile(0.75),
                                q95=source.amount.quantile(0.95),
                            )
                            .order_by(key)
                        )
                        sql = str(
                            ibis.to_sql(lower_temporal(query, "clickhouse"), dialect="clickhouse")
                        )
                        actual = reader.query(sql)
                        rows = [
                            (
                                (point if point.tzinfo else point.replace(tzinfo=timezone.utc))
                                .astimezone(ZoneInfo("Asia/Shanghai"))
                                .date(),
                                *values,
                            )
                            for point, *values in actual.result_rows
                        ]
                        assert rows == native.result_rows
                        assert (
                            int(actual.summary["read_rows"])
                            == int(native.summary["read_rows"])
                            == days * rows_per_day
                        )
                        assert "formatDateTime" not in sql
                        if source is calendar:
                            plan = "\n".join(
                                str(row[0])
                                for row in reader.query("EXPLAIN actions=1 " + sql).result_rows
                            )
                            above, below = plan.split("Aggregating", 1)
                            assert "FUNCTION toDateTime64" in above
                            assert "FUNCTION toDateTime64" not in below
                            assert "FUNCTION toString" not in below
                            assert "FUNCTION toTimezone" not in below
        finally:
            admin.command(f"DROP TABLE IF EXISTS {name}")


@pytest.mark.parametrize("encoding", ["compact", "dashed", "integer"])
@pytest.mark.parametrize(
    "source_zone,report_zone,start,end,fast",
    [
        ("UTC", "UTC", "2026-10-28", "2026-10-31", True),
        ("Asia/Shanghai", "Asia/Shanghai", "2026-10-28", "2026-10-31", True),
        ("Asia/Shanghai", "UTC", "2026-10-28", "2026-10-31", False),
        ("America/New_York", "America/New_York", "2026-10-30", "2026-11-04", False),
    ],
)
def test_encoded_day_keys_restore_native_groups_after_aggregation(
    encoding: str, source_zone: str, report_zone: str, start: str, end: str, fast: bool
) -> None:
    from marivo.analysis.compiler.source_time import source_time
    from marivo.semantic._source_timezone import DeclaredSourceTimezone
    from tests.analysis.temporal.encoded_time_fixtures import axis

    calendar = exact_scope(start, end, report_zone).by(mv.grain("day"))._binding
    fmt = "%Y-%m-%d" if encoding == "dashed" else "%Y%m%d"
    contract = axis(fmt)
    first, stop = date.fromisoformat(start), date.fromisoformat(end)
    points = [first + timedelta(days=i) for i in range((stop - first).days)]
    values = [
        int(point.strftime(fmt)) if encoding == "integer" else point.strftime(fmt)
        for point in points
    ]
    backend = ibis.duckdb.connect(TimeZone="UTC")
    try:
        table = backend.create_table(
            "encoded_days",
            ibis.memtable(
                {"point": [*values, *values, None]},
                schema={"point": "int64" if encoding == "integer" else "string"},
            ),
        )
        clock, authority = source_time(
            table.point,
            contract,
            boundary_timezone="UTC",
            source_timezone=DeclaredSourceTimezone(source_zone, "field"),
            engine="duckdb",
        )
        native = builtin_key(clock, calendar)
        deferred = deferred_day_key(
            table, table.point, calendar, source_zone, "duckdb", encoded=(contract, authority)
        )
        assert (deferred is not None) == fast
        if deferred is not None:
            marked = table.mutate(bucket=deferred)
            original = table.mutate(bucket=native)
            actual = lower_temporal(
                marked.group_by("bucket").aggregate(n=marked.count()).order_by("bucket"), "duckdb"
            )
            expected = lower_temporal(
                original.group_by("bucket").aggregate(n=original.count()).order_by("bucket"),
                "duckdb",
            )
            pd.testing.assert_frame_equal(backend.execute(actual), backend.execute(expected))
        # A derived encoding cannot authorize raw grouping, even if its values agree.
        derived = table.point + 0 if encoding == "integer" else table.point.concat("")
        assert (
            deferred_day_key(
                table, derived, calendar, source_zone, "duckdb", encoded=(contract, authority)
            )
            is None
        )
    finally:
        backend.disconnect()
