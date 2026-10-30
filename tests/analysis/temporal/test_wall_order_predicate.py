"""Independent finite source-clock oracles for wall-order membership."""

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
from marivo.analysis.core.time_authority import SourceTimeAuthority
from marivo.semantic.ir import DateParse, HourPrefixParse, TimestampParse
from tests.analysis.temporal.encoded_time_fixtures import axis

# Each instant is independent, explicit source meaning, including gap plateaus.
_CASES = (
    (
        "America/New_York",
        "2026-03-08",
        ("01:30", "02:00", "02:30", "03:00", "03:30", "04:00"),
        ("06:30", "07:00", "07:00", "07:00", "07:30", "08:00"),
        (("06:30", "07:30"), ("07:00", "08:00")),
    ),
    (
        "America/New_York",
        "2026-11-01",
        ("00:59", "01:00", "01:15", "01:30", "01:59", "02:00"),
        ("04:59", "06:00", "06:15", "06:30", "06:59", "07:00"),
        (("05:30", "06:30"), ("05:00", "06:00")),
    ),
    (
        "Australia/Lord_Howe",
        "2026-10-04",
        ("01:59", "02:00", "02:15", "02:30", "02:45"),
        (
            "2026-10-03 15:29",
            "2026-10-03 15:30",
            "2026-10-03 15:30",
            "2026-10-03 15:30",
            "2026-10-03 15:45",
        ),
        (("2026-10-03 15:30", "2026-10-03 15:45"),),
    ),
    (
        "Asia/Shanghai",
        "1991-04-14",
        ("01:59", "02:00", "02:30", "03:00", "03:30"),
        (
            "1991-04-13 17:59",
            "1991-04-13 18:00",
            "1991-04-13 18:00",
            "1991-04-13 18:00",
            "1991-04-13 18:30",
        ),
        (("1991-04-13 18:00", "1991-04-13 18:30"),),
    ),
)


def _instant(day: str, text: str) -> datetime:
    return datetime.fromisoformat(f"{day} {text}" if len(text) == 5 else text).replace(
        tzinfo=timezone.utc
    )


@pytest.mark.parametrize("zone,day,clocks,instants,windows", _CASES)
def test_raw_local_ranges_follow_explicit_wall_meaning(
    zone: str,
    day: str,
    clocks: tuple[str, ...],
    instants: tuple[str, ...],
    windows: tuple[tuple[str, str], ...],
) -> None:
    points = [datetime.fromisoformat(f"{day} {clock}") for clock in clocks]
    table = ibis.memtable(
        {"id": range(len(points) + 1), "point": [*points, None]},
        schema={"id": "int64", "point": "timestamp"},
    )
    field = replace(axis("%Y%m%d%H%M"), parse=TimestampParse(timezone=zone))
    _, authority = source_time(
        table.point, field, boundary_timezone="UTC", read_timezone="UTC", engine="duckdb"
    )
    backend = ibis.duckdb.connect()
    try:
        for left, right in windows:
            start, end = _instant(day, left), _instant(day, right)
            predicate = source_time_predicate(table.point, field, authority, start=start, end=end)
            assert predicate is not None
            expected = [
                i for i, instant in enumerate(instants) if start <= _instant(day, instant) < end
            ]
            assert backend.execute(table.filter(predicate)).id.tolist() == expected
            projected = backend.execute(
                table.select(predicate=predicate, negated=~predicate, disjoined=predicate | False)
            )
            assert pd.isna(projected.iloc[-1]).all()
    finally:
        backend.disconnect()


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
def test_gap_plateau_and_fold_strict_comparisons_are_nullable(
    comparison: TimeComparison, compare: Callable[[datetime, datetime], bool]
) -> None:
    backend = ibis.duckdb.connect()
    try:
        for instant in (
            "2026-03-08 07:00",
            "2026-03-08 07:00:00.000001",
            "2026-11-01 05:30",
            "2026-11-01 06:00",
        ):
            gap = "03-08" in instant
            day = "2026-03-08" if gap else "2026-11-01"
            clocks = (
                ("01:59", "02:00", "02:30", "03:00", "03:01")
                if gap
                else ("00:59", "01:00", "01:30", "02:00")
            )
            meanings = (
                ("06:59", "07:00", "07:00", "07:00", "07:01")
                if gap
                else ("04:59", "06:00", "06:30", "07:00")
            )
            table = ibis.memtable(
                {
                    "id": range(len(clocks) + 1),
                    "point": [
                        *(datetime.fromisoformat(f"{day} {clock}") for clock in clocks),
                        None,
                    ],
                },
                schema={"id": "int64", "point": "timestamp"},
            )
            field = replace(axis("%Y%m%d%H%M"), parse=TimestampParse(timezone="America/New_York"))
            _, authority = source_time(
                table.point, field, boundary_timezone="UTC", read_timezone="UTC", engine="duckdb"
            )
            bound = _instant(day, instant)
            predicate = source_time_comparison(
                table.point, field, authority, bound=bound, comparison=comparison
            )
            assert predicate is not None
            expected = [
                i for i, meaning in enumerate(meanings) if compare(_instant(day, meaning), bound)
            ]
            assert backend.execute(table.filter(predicate)).id.tolist() == expected
            assert pd.isna(backend.execute(table.select(test=predicate)).test.iloc[-1])
    finally:
        backend.disconnect()


@pytest.mark.parametrize(
    "fmt",
    [
        "%Y%m%d",
        "%Y-%m-%d",
        "%Y%m%d%H",
        "%Y-%m-%d %H",
        "%Y%m%d%H%M",
        "%Y-%m-%d %H:%M",
        "%Y%m%d%H%M%S",
        "%Y-%m-%d %H:%M:%S",
    ],
)
def test_ordered_formats_quantize_after_wall_translation(fmt: str) -> None:
    points = [
        datetime(2026, 3, 8, hour, minute)
        for hour, minute in ((1, 0), (2, 0), (2, 30), (3, 0), (3, 30), (4, 0))
    ]
    texts = sorted({p.strftime(fmt) for p in points})
    kind = "int64" if "-" not in fmt else "string"
    values = [int(text) for text in texts] if kind == "int64" else texts
    table = ibis.memtable({"point": [*values, None]}, schema={"point": kind})
    field = axis(fmt, "America/New_York")
    _, authority = source_time(
        table.point,
        field,
        boundary_timezone="UTC",
        read_timezone="America/New_York",
        engine="duckdb",
    )
    start, end = (
        datetime(2026, 3, 8, 7, tzinfo=timezone.utc),
        datetime(2026, 3, 8, 8, tzinfo=timezone.utc),
    )
    predicate = source_time_predicate(table.point, field, authority, start=start, end=end)
    assert predicate is not None
    expected = []
    for value, text in zip(values, texts, strict=True):
        point = datetime.strptime(text, fmt)
        if "%H" not in fmt:
            selected = (
                start.replace(tzinfo=None).date() <= point.date() < end.replace(tzinfo=None).date()
            )
        else:
            selected = datetime(2026, 3, 8, 2) <= point < datetime(2026, 3, 8, 4)
        if selected:
            expected.append(value)
    backend = ibis.duckdb.connect()
    try:
        assert backend.execute(table.filter(predicate)).point.tolist() == expected
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
    _, authority = source_time(
        table.point,
        field,
        boundary_timezone="UTC",
        read_timezone="UTC",
        prefix=prepared_prefix,
        prefix_timezone="America/New_York",
        engine="duckdb",
    )
    assert authority.read_timezone == "America/New_York"
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
        table.point, field, boundary_timezone="UTC", read_timezone="UTC", engine="duckdb"
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
        is None
    )
    with pytest.raises(DatasetCompilationError) as caught:
        source_time(
            table.point,
            replace(field, parse=HourPrefixParse(prefix="bad")),
            boundary_timezone="UTC",
            read_timezone="UTC",
            prefix=table.point,
            engine="duckdb",
        )
    assert caught.value.expected == "a civil-date hour prefix"


def test_invalid_integer_encoding_keeps_nullable_comparison() -> None:
    table = ibis.memtable({"point": [1, None]}, schema={"point": "int16"})
    authority = SourceTimeAuthority(
        axis="sales.events.point",
        physical_type="int16",
        kind="civil_date",
        read_timezone=None,
        source="civil_date",
        boundary_timezone="UTC",
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


@pytest.mark.parametrize(
    "zone,instant,wall,inclusive,strict",
    [
        ("Australia/Lord_Howe", "2026-04-04T14:45:00+00:00", "2026-04-05T01:30", True, False),
        ("Australia/Lord_Howe", "2026-04-04T15:00:00+00:00", "2026-04-05T01:30", False, True),
        ("Pacific/Apia", "2011-12-30T10:00:00+00:00", "2011-12-30T00:00", True, False),
        ("Pacific/Apia", "2011-12-30T10:00:00+00:00", "2011-12-31T00:00", False, True),
        ("UTC+05:30", "2026-10-22T07:00:00+00:00", "2026-10-22T12:30", True, False),
    ],
)
def test_transition_cutoffs_use_actual_offset_change(
    zone: str, instant: str, wall: str, inclusive: bool, strict: bool
) -> None:
    from marivo.analysis.compiler._wall_order import wall_order_bound
    from marivo.analysis.core.time_authority import time_zone

    assert wall_order_bound(datetime.fromisoformat(instant), time_zone(zone), strict=strict) == (
        datetime.fromisoformat(wall),
        inclusive,
    )


def test_transparent_source_view_retains_raw_lineage() -> None:
    table = ibis.table({"point": "timestamp"}, name="events").view()
    field = replace(axis("%Y%m%d%H"), parse=TimestampParse(timezone="America/New_York"))
    _, authority = source_time(
        table.point, field, boundary_timezone="UTC", read_timezone="UTC", engine="duckdb"
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
