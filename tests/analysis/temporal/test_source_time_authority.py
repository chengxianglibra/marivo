"""Source authority rejects duplicate clocks and irreducible civil-time ambiguity."""

from dataclasses import replace
from datetime import date, datetime, timezone

import ibis
import ibis.expr.datatypes as dt
import pytest

from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.compiler.source_time import source_time
from marivo.semantic.ir import StrptimeParse, TimestampParse
from tests.analysis.temporal.encoded_time_fixtures import axis


@pytest.mark.parametrize("declared", ("UTC", "Asia/Shanghai"))
def test_aware_source_rejects_any_parser_timezone_and_repair_runs(declared: str) -> None:
    table = ibis.memtable(
        {"point": [datetime(2026, 8, 1, tzinfo=timezone.utc)]},
        schema={"point": dt.Timestamp(timezone="UTC")},
    )
    field = replace(axis("%Y-%m-%d %H:%M"), parse=TimestampParse(timezone=declared))
    with pytest.raises(DatasetCompilationError) as caught:
        source_time(
            table.point, field, boundary_timezone="UTC", read_timezone="UTC", engine="duckdb"
        )
    error = caught.value
    assert error.location == "analysis.source_time[time_dimension:sales.events.point]"
    assert error.expected == "an aware source without parser timezone"
    assert error.received is not None
    assert "duckdb" in error.received
    assert "timestamp('UTC')" in error.received
    assert f"parser_timezone={declared!r}" in error.received
    assert "sales.events.point" in error.repair.action
    assert "parse=ms.timestamp()" in error.repair.action
    repaired, authority = source_time(
        table.point,
        replace(field, parse=TimestampParse()),
        boundary_timezone="UTC",
        read_timezone="UTC",
        engine="duckdb",
    )
    assert authority.kind == "instant" and authority.source == "physical"
    backend = ibis.duckdb.connect()
    try:
        assert backend.execute(table.select(point=repaired)).point.tolist() == [
            datetime(2026, 8, 1)
        ]
    finally:
        backend.disconnect()


def test_zoned_strptime_from_string_uses_declared_authority() -> None:
    table = ibis.memtable({"point": ["2026-08-01 12:30:00"]}, schema={"point": "string"})
    field = replace(
        axis("%Y-%m-%d %H:%M:%S"),
        parse=StrptimeParse("%Y-%m-%d %H:%M:%S", timezone="UTC"),
    )
    prepared, authority = source_time(
        table.point,
        field,
        boundary_timezone="UTC",
        read_timezone="UTC",
        engine="clickhouse",
    )

    assert prepared.type().timezone == "UTC"
    assert authority.physical_type == str(table.point.type())
    assert authority.kind == "localizable"
    assert authority.source == "declared"
    assert authority.read_timezone == "UTC"

    # Re-preparing a frozen carrier must use its recorded source category;
    # the carrier's zoned Ibis type is not the original physical column type.
    _, retained = source_time(
        prepared,
        field,
        boundary_timezone="UTC",
        read_timezone="UTC",
        engine="clickhouse",
        frozen=authority,
    )
    assert retained == authority


def test_aware_physical_source_rejects_strptime_timezone() -> None:
    table = ibis.memtable(
        {"point": [datetime(2026, 8, 1, tzinfo=timezone.utc)]},
        schema={"point": dt.Timestamp(timezone="UTC")},
    )
    field = replace(
        axis("%Y-%m-%d %H:%M:%S"),
        parse=StrptimeParse("%Y-%m-%d %H:%M:%S", timezone="UTC"),
    )

    with pytest.raises(DatasetCompilationError) as caught:
        source_time(
            table.point,
            field,
            boundary_timezone="UTC",
            read_timezone="UTC",
            engine="clickhouse",
        )

    assert caught.value.location == "analysis.source_time[time_dimension:sales.events.point]"
    assert "parse=ms.timestamp()" in caught.value.repair.action


def test_native_date_is_prepared_as_date_without_guessing_timestamp() -> None:
    table = ibis.memtable({"point": [date(2026, 8, 1)]}, schema={"point": "date"})
    field = replace(axis("%Y%m%d"), logical_type="unknown", parse=None)
    prepared, authority = source_time(
        table.point, field, boundary_timezone="Asia/Shanghai", read_timezone="UTC", engine="duckdb"
    )
    assert prepared.type().is_date()
    assert authority.kind == "civil_date" and authority.read_timezone is None


@pytest.mark.parametrize(
    "zone,points",
    [
        (
            "Europe/Berlin",
            [
                datetime(2026, 3, 29, 1, 30),
                datetime(2026, 3, 29, 2, 30),
                datetime(2026, 10, 25, 2, 30),
                datetime(2026, 10, 25, 3, 30),
            ],
        ),
        (
            "Australia/Lord_Howe",
            [datetime(2026, 4, 5, 1, 45), datetime(2026, 10, 4, 2, 15), datetime(2026, 10, 4, 3)],
        ),
    ],
)
def test_naive_source_gap_and_fold_follow_native_backend(zone: str, points: list[datetime]) -> None:
    table = ibis.memtable({"point": points}, schema={"point": "timestamp"})
    field = replace(axis("%Y-%m-%d %H:%M"), parse=TimestampParse(timezone=zone))
    value, _ = source_time(
        table.point,
        field,
        boundary_timezone="UTC",
        read_timezone="UTC",
        engine="duckdb",
    )
    backend = ibis.duckdb.connect()
    try:
        expected = [
            backend.con.execute(
                "SELECT timezone('UTC', timezone(?, ?::TIMESTAMP))", [zone, point]
            ).fetchone()[0]
            for point in points
        ]
        assert backend.execute(table.select(value=value)).value.tolist() == expected
    finally:
        backend.disconnect()
