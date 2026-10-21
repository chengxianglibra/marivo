"""Source authority rejects duplicate clocks and irreducible civil-time ambiguity."""

from dataclasses import replace
from datetime import date, datetime, timezone

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir
import pytest

from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.compiler.source_time import source_time
from marivo.semantic.ir import TimestampParse
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
    assert "Remove timezone" in caught.value.repair.action
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


def test_native_date_is_prepared_as_date_without_guessing_timestamp() -> None:
    table = ibis.memtable({"point": [date(2026, 8, 1)]}, schema={"point": "date"})
    field = replace(axis("%Y%m%d"), logical_type="unknown", parse=None)
    prepared, authority = source_time(
        table.point, field, boundary_timezone="Asia/Shanghai", read_timezone="UTC", engine="duckdb"
    )
    assert prepared.type().is_date()
    assert authority.kind == "civil_date" and authority.read_timezone is None


@pytest.mark.parametrize(
    "zone,points,expected",
    [
        (
            "Europe/Berlin",
            [
                datetime(2026, 3, 29, 1, 30),
                datetime(2026, 3, 29, 2, 30),
                datetime(2026, 10, 25, 2, 30),
                datetime(2026, 10, 25, 3, 30),
            ],
            [False, True, True, False],
        ),
        (
            "Australia/Lord_Howe",
            [datetime(2026, 4, 5, 1, 45), datetime(2026, 10, 4, 2, 15), datetime(2026, 10, 4, 3)],
            [True, True, False],
        ),
    ],
)
def test_naive_source_checks_gap_and_fold_without_assuming_one_hour(
    zone: str, points: list[datetime], expected: list[bool]
) -> None:
    table = ibis.memtable({"point": points}, schema={"point": "timestamp"})
    checks: list[ir.BooleanValue] = []
    field = replace(axis("%Y-%m-%d %H:%M"), parse=TimestampParse(timezone=zone))
    source_time(
        table.point,
        field,
        boundary_timezone="UTC",
        read_timezone="UTC",
        engine="duckdb",
        validity_checks=checks,
    )
    assert len(checks) == 1
    backend = ibis.duckdb.connect()
    try:
        assert backend.execute(table.select(invalid=checks[0])).invalid.tolist() == expected
    finally:
        backend.disconnect()
