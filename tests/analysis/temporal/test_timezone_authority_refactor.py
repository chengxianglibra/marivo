"""Independent native witnesses for source declarations and safe range rewriting."""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import ibis
import pytest

from marivo.analysis.compiler.source_time import (
    endpoint_rewrite_safe,
    source_time,
    source_time_comparison,
    source_time_predicate,
)
from marivo.analysis.core.time_authority import CivilDateSourceTime
from marivo.datasource._temporal_lowering import lower_temporal
from marivo.semantic._source_timezone import DeclaredSourceTimezone
from marivo.semantic.ir import DateParse, TimestampParse
from tests.analysis.temporal.encoded_time_fixtures import axis


def test_endpoint_rewrite_is_independent_for_each_range_boundary() -> None:
    summer = datetime(2026, 7, 1, 4, tzinfo=timezone.utc)
    transition = datetime(2026, 3, 8, 7, tzinfo=timezone.utc)
    assert endpoint_rewrite_safe(summer, "America/New_York", "duckdb")
    assert not endpoint_rewrite_safe(transition, "America/New_York", "duckdb")
    assert not endpoint_rewrite_safe(summer, "America/New_York", "generic")
    table = ibis.table({"point": "timestamp(6)"}, name="events")
    field = replace(axis("%Y-%m-%d %H:%M"), parse=TimestampParse(timezone="America/New_York"))
    _, authority = source_time(
        table.point,
        field,
        source_timezone=DeclaredSourceTimezone("America/New_York", "field"),
        boundary_timezone="UTC",
        engine="duckdb",
    )
    assert (
        source_time_comparison(table.point, field, authority, bound=summer, comparison="<")
        is not None
    )
    assert (
        source_time_comparison(table.point, field, authority, bound=transition, comparison=">=")
        is None
    )
    predicate = source_time_predicate(table.point, field, authority, start=transition, end=summer)
    assert predicate is not None
    sql = str(ibis.to_sql(predicate, dialect="duckdb"))
    assert "TIMEZONE" in sql.upper()
    import ibis.expr.operations as ops

    assert any(node.left == table.point.op() for node in predicate.op().find(ops.Less))


@pytest.mark.parametrize(
    "source_zone,expected", [("UTC", list(range(1, 8))), ("Asia/Tokyo", list(range(2, 9)))]
)
def test_date_membership_assigns_native_business_day_start(
    source_zone: str, expected: list[int]
) -> None:
    # UTC report selects Tokyo July 2..8; Tokyo report selects UTC July 1..7.
    report_zone = "Asia/Tokyo" if source_zone == "UTC" else "UTC"
    offset = timezone.utc if report_zone == "UTC" else timezone(timedelta(hours=9))
    backend = ibis.duckdb.connect(TimeZone="UTC")
    try:
        table = backend.create_table(
            "business_days", ibis.memtable({"point": [date(2026, 7, day) for day in range(1, 9)]})
        )
        field = replace(axis("%Y%m%d"), parse=DateParse(timezone=source_zone))
        authority = CivilDateSourceTime(
            axis=field.ref.path, physical_type="date", timezone=source_zone, origin="field"
        )
        start, end = datetime(2026, 7, 1, tzinfo=offset), datetime(2026, 7, 8, tzinfo=offset)
        predicate = source_time_predicate(table.point, field, authority, start=start, end=end)
        assert predicate is not None
        actual = backend.execute(
            lower_temporal(table.filter(predicate).order_by("point"), "duckdb")
        )
        assert actual.point.dt.day.tolist() == expected
        native = backend.con.execute(
            "SELECT point FROM business_days WHERE timezone(?, point::TIMESTAMP) >= ? AND timezone(?, point::TIMESTAMP) < ? ORDER BY point",
            [source_zone, start, source_zone, end],
        ).fetchall()
        assert [row[0].day for row in native] == expected
    finally:
        backend.disconnect()


@pytest.mark.parametrize("engine", ["postgres", "mysql"])
def test_fixed_offset_never_reaches_backend_as_a_timezone_name(engine: str) -> None:
    table = ibis.table({"point": "timestamp('UTC', 6)"}, name="events")
    field = replace(axis("%Y-%m-%d %H:%M"), parse=TimestampParse())
    expression, authority = source_time(
        table.point,
        field,
        boundary_timezone="UTC+08:00",
        source_timezone=DeclaredSourceTimezone("UTC+08:00", "datasource"),
        engine=engine,
    )
    assert expression.equals(table.point)
    assert authority.kind == "instant"


@pytest.mark.runtime
@pytest.mark.parametrize("engine,profile", [("postgres", "table"), ("mysql", "innodb-table")])
def test_fixed_offset_native_projection_matches_independent_offset_sql(
    engine: str, profile: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.datasource._temporal_expressions import render
    from tests.datasource.source_cases import SourceData, source_case

    columns = (
        "id BIGINT, wall TIMESTAMP(6), instant TIMESTAMPTZ"
        if engine == "postgres"
        else "id BIGINT, wall DATETIME(6), instant TIMESTAMP(6)"
    )
    data = SourceData(columns, "(1,'2026-07-01 00:30','2026-06-30 16:30')", "", [])
    with source_case(engine, profile, tmp_path, monkeypatch, data) as case:
        table = case.session.bind(case.source, source_identity="fixed-offset-native").relation
        projections = {}
        for name in ("wall", "instant"):
            field = replace(axis("%Y-%m-%d %H:%M"), source_column=name, parse=TimestampParse())
            value, _ = source_time(
                table[name],
                field,
                boundary_timezone="UTC",
                source_timezone=DeclaredSourceTimezone("UTC+08:00", "datasource"),
                engine=engine,
            )
            projections[name] = render("UTC+08:00", value)
        expression = lower_temporal(table.select(**projections), engine)
        actual = case.session._backend.execute(expression)
        assert actual.wall.iloc[0].to_pydatetime() == datetime(2026, 7, 1, 0, 30)
        assert actual.instant.iloc[0].to_pydatetime() == datetime(2026, 7, 1, 0, 30)
        # The native reference uses numeric interval arithmetic, never a POSIX-style zone name.
        if engine == "postgres":
            native = case.session._backend.con.execute(
                "SELECT TIMESTAMPTZ '2026-06-30 16:30+00' AT TIME ZONE 'UTC' + INTERVAL '8 hours'"
            ).fetchone()[0]
        else:
            with case.session._backend.con.cursor() as cursor:
                cursor.execute(
                    "SELECT DATE_ADD(CAST('2026-06-30 16:30' AS DATETIME), INTERVAL 8 HOUR)"
                )
                native = cursor.fetchone()[0]
        assert native == datetime(2026, 7, 1, 0, 30)
