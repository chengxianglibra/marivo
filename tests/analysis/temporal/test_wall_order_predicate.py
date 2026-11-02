"""Source clocks use native interpretation; exact raw rewrites retain UTC precision."""

import operator
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timezone

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import pandas as pd
import pytest

from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.compiler.source_time import (
    TimeComparison,
    source_time,
    source_time_comparison,
    source_time_predicate,
)
from marivo.analysis.core.time_authority import CivilDateSourceTime
from marivo.analysis.materialization.temporal_sql import lower_temporal
from marivo.semantic.ir import DateParse, HourPrefixParse, TimestampParse
from tests.analysis.temporal.encoded_time_fixtures import axis
from tests.shared_fixtures import declared_test_timezone


@pytest.mark.parametrize(
    "zone,day,clocks",
    [
        ("America/New_York", "2026-03-08", ("01:30", "02:00", "02:30", "03:00", "03:30")),
        ("America/New_York", "2026-11-01", ("00:59", "01:00", "01:30", "02:00")),
        ("Australia/Lord_Howe", "2026-10-04", ("01:59", "02:00", "02:15", "02:30", "02:45")),
        ("Asia/Shanghai", "1991-04-14", ("01:59", "02:00", "02:30", "03:00", "03:30")),
    ],
)
@pytest.mark.parametrize(
    "comparison,compare",
    [
        (">=", operator.ge),
        ("<", operator.lt),
        (">", operator.gt),
        ("<=", operator.le),
        ("==", operator.eq),
        ("!=", operator.ne),
    ],
)
def test_named_clock_membership_matches_independent_native_projection(
    zone: str,
    day: str,
    clocks: tuple[str, ...],
    comparison: TimeComparison,
    compare: Callable[[datetime, datetime], bool],
) -> None:
    points = [datetime.fromisoformat(f"{day} {clock}") for clock in clocks]
    table = ibis.memtable(
        {"id": range(len(points) + 1), "point": [*points, None]},
        schema={"id": "int64", "point": "timestamp"},
    )
    field = replace(axis("%Y%m%d%H%M"), parse=TimestampParse(timezone=zone))
    normalized, authority = source_time(
        table.point,
        field,
        boundary_timezone="UTC",
        source_timezone=declared_test_timezone(field, "UTC"),
        engine="duckdb",
    )
    backend = ibis.duckdb.connect()
    try:
        native = [
            backend.con.execute("SELECT timezone(?, CAST(? AS TIMESTAMP))", [zone, point])
            .fetchone()[0]
            .astimezone(timezone.utc)
            for point in points
        ]
        for bound in native:
            assert (
                source_time_comparison(
                    table.point, field, authority, bound=bound, comparison=comparison
                )
                is None
            )
            operation = {
                ">=": ops.GreaterEqual,
                "<": ops.Less,
                ">": ops.Greater,
                "<=": ops.LessEqual,
                "==": ops.Equals,
                "!=": ops.NotEquals,
            }[comparison]
            predicate = operation(
                normalized.op(), ibis.literal(bound.replace(tzinfo=None)).op()
            ).to_expr()
            expected = [i for i, instant in enumerate(native) if compare(instant, bound)]
            actual = backend.execute(lower_temporal(table.filter(predicate), "duckdb"))
            assert actual.id.tolist() == expected
            assert pd.isna(backend.execute(table.select(test=predicate)).test.iloc[-1])
    finally:
        backend.disconnect()


@pytest.mark.parametrize(
    "prefix_kind,hour_kind", [("date", "int8"), ("string", "string"), ("int64", "int64")]
)
def test_hour_prefix_preserves_hour_24_and_inherited_source_zone(
    prefix_kind: str, hour_kind: str
) -> None:
    days = [
        date(2026, 3, 7),
        date(2026, 3, 8),
        date(2026, 3, 8),
        date(2026, 3, 8),
        date(2026, 3, 8),
        None,
    ]
    prefixes = (
        days
        if prefix_kind == "date"
        else [
            None
            if day is None
            else int(day.strftime("%Y%m%d"))
            if prefix_kind == "int64"
            else day.strftime("%Y%m%d")
            for day in days
        ]
    )
    hours = [24, 0, 2, 3, 4, 2]
    table = ibis.memtable(
        {
            "id": range(6),
            "day": prefixes,
            "point": [f"{h:02d}" for h in hours] if hour_kind == "string" else hours,
        },
        schema={"id": "int64", "day": prefix_kind, "point": hour_kind},
    )
    prefix_axis = (
        replace(axis("%Y%m%d"), parse=DateParse()) if prefix_kind == "date" else axis("%Y%m%d")
    )
    prepared_prefix = (
        table.day
        if prefix_kind == "date"
        else table.day.cast("string").as_timestamp("%Y%m%d").cast("date")
    )
    field = replace(axis("%Y%m%d%H"), parse=HourPrefixParse(prefix="day"), timezone=None)
    normalized, authority = source_time(
        table.point,
        field,
        boundary_timezone="UTC",
        source_timezone=declared_test_timezone(field, "America/New_York"),
        prefix=prepared_prefix,
        engine="duckdb",
    )
    assert authority.timezone == "America/New_York"
    predicate = source_time_predicate(
        table.point,
        field,
        authority,
        start=datetime(2026, 3, 8, 5, tzinfo=timezone.utc),
        end=datetime(2026, 3, 8, 8, tzinfo=timezone.utc),
        prefix=table.day,
        prefix_axis=prefix_axis,
    )
    assert predicate is not None
    predicate = (normalized >= ibis.timestamp("2026-03-08 05:00:00")) & (
        normalized < ibis.timestamp("2026-03-08 08:00:00")
    )
    backend = ibis.duckdb.connect()
    try:
        assert backend.execute(table.filter(predicate)).id.tolist() == [0, 1, 2, 3]
        assert pd.isna(backend.execute(table.select(test=predicate)).test.iloc[-1])
    finally:
        backend.disconnect()


def test_native_timestamp_precision_and_unowned_expression_fallback() -> None:
    table = ibis.table({"point": dt.Timestamp(scale=0)}, name="events")
    field = replace(axis("%Y%m%d%H"), parse=TimestampParse(timezone="UTC"))
    _, authority = source_time(
        table.point,
        field,
        boundary_timezone="UTC",
        source_timezone=declared_test_timezone(field, "UTC"),
        engine="duckdb",
    )
    predicate = source_time_predicate(
        table.point,
        field,
        authority,
        start=datetime(2026, 1, 1, microsecond=1),
        end=datetime(2026, 1, 1, 0, 0, 2),
    )
    assert predicate is not None
    assert datetime(2026, 1, 1, 0, 0, 1) in {
        literal.value for literal in predicate.op().find(ops.Literal)
    }
    assert (
        source_time_predicate(
            table.point.coalesce(datetime(2026, 1, 1)),
            field,
            authority,
            start=datetime(2026, 1, 1),
            end=None,
        )
        is not None
    )
    with pytest.raises(DatasetCompilationError) as caught:
        source_time(
            table.point,
            replace(field, parse=HourPrefixParse(prefix="bad")),
            boundary_timezone="UTC",
            source_timezone=declared_test_timezone(
                replace(field, parse=HourPrefixParse(prefix="bad")), "UTC"
            ),
            prefix=table.point,
            engine="duckdb",
        )
    assert caught.value.expected == "a civil-date hour prefix"


def test_invalid_integer_encoding_keeps_nullable_comparison() -> None:
    table = ibis.memtable({"point": [1, None]}, schema={"point": "int16"})
    authority = CivilDateSourceTime(
        axis="sales.events.point",
        physical_type="int16",
        kind="civil_date",
        timezone="UTC",
        origin="datasource",
    )
    predicate = source_time_predicate(
        table.point, axis("%Y%m%d"), authority, start=date(2026, 1, 1), end=None
    )
    assert predicate is not None
    backend = ibis.duckdb.connect()
    try:
        result = backend.execute(table.select(test=predicate, complement=~predicate))
        assert result.iloc[0].tolist() == [None, None]
        assert result.iloc[1].isna().all()
    finally:
        backend.disconnect()


def test_transparent_source_view_retains_raw_lineage() -> None:
    table = ibis.table({"point": "timestamp"}, name="events").view()
    field = replace(axis("%Y%m%d%H"), parse=TimestampParse(timezone="America/New_York"))
    _, authority = source_time(
        table.point,
        field,
        boundary_timezone="UTC",
        source_timezone=declared_test_timezone(field, "UTC"),
        engine="duckdb",
    )
    assert (
        source_time_predicate(
            table.point,
            field,
            authority,
            start=datetime(2026, 3, 8, 7, tzinfo=timezone.utc),
            end=None,
        )
        is not None
    )
