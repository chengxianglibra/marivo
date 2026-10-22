"""Native oracles for shared physical time rewrites and their scan guards."""

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ibis
import pandas as pd
import pytest

from marivo.analysis.compiler.source_time import localize, render, source_time
from marivo.analysis.materialization import time_filter
from marivo.analysis.materialization.temporal_sql import lower_temporal
from marivo.semantic.ir import TimestampParse
from tests.analysis.temporal.encoded_time_fixtures import axis


def test_utc_cancellation_preserves_instant_identity_and_keeps_named_fold() -> None:
    table = ibis.table({"wall": "timestamp", "instant": "timestamp('UTC')"}, name="events")
    expression = table.select(
        wall=render("UTC", localize("UTC", table.wall)),
        instant=localize("UTC", render("UTC", table.instant)),
        fold=localize("America/New_York", render("America/New_York", table.instant)),
    )
    optimized = time_filter.cancel_utc(expression)
    expected = table.select(
        wall=table.wall.cast("timestamp(6)"),
        instant=table.instant.cast("timestamp('UTC', 6)"),
        fold=localize("America/New_York", render("America/New_York", table.instant)),
    )
    assert optimized.equals(expected)
    assert optimized.schema() == expression.schema()


@pytest.mark.parametrize(
    "zone,start,end,points",
    [
        (
            "America/New_York",
            "2025-01-01T05:00",
            "2025-01-02T05:00",
            (
                "2024-12-31T23:59:59.999999",
                "2025-01-01T00:00",
                "2025-01-01T23:59:59.999999",
                "2025-01-02T00:00",
            ),
        ),
        (
            "America/New_York",
            "2025-03-09T07:00",
            "2025-03-09T08:00",
            ("2025-03-09T01:30", "2025-03-09T02:30", "2025-03-09T03:30", "2025-03-09T04:00"),
        ),
        (
            "America/New_York",
            "2025-11-02T05:00",
            "2025-11-02T06:00",
            ("2025-11-02T00:30", "2025-11-02T01:30", "2025-11-02T02:30"),
        ),
        (
            "Australia/Lord_Howe",
            "2025-04-05T14:30",
            "2025-04-05T15:30",
            ("2025-04-06T01:15", "2025-04-06T01:45", "2025-04-06T02:15"),
        ),
        (
            "Pacific/Apia",
            "2011-12-30T09:00",
            "2011-12-31T11:00",
            ("2011-12-29T23:30", "2011-12-30T12:00", "2011-12-31T12:00"),
        ),
    ],
)
def test_selection_matches_native_gap_fold_and_non_hour_rules(
    zone: str, start: str, end: str, points: tuple[str, ...]
) -> None:
    backend = ibis.duckdb.connect(TimeZone="UTC")
    try:
        table = ibis.memtable(
            {
                "id": list(range(len(points) + 1)),
                "point": [*(datetime.fromisoformat(p) for p in points), None],
            },
            schema={"id": "int64", "point": "timestamp"},
        )
        field = replace(axis("%Y-%m-%d %H:%M"), parse=TimestampParse(timezone=zone))
        value, _ = source_time(
            table.point,
            field,
            boundary_timezone="UTC",
            read_timezone="UTC",
            engine="duckdb",
        )
        value = localize("UTC", value)
        left, right = (datetime.fromisoformat(x).replace(tzinfo=timezone.utc) for x in (start, end))
        condition = (value >= left) & (value < right)
        expression = table.filter(condition).select(id=table.id, value=value)
        native = backend.execute(expression).sort_values("id").reset_index(drop=True)
        optimized = lower_temporal(expression, "duckdb")
        actual = backend.execute(optimized).sort_values("id").reset_index(drop=True)
        pd.testing.assert_frame_equal(actual, native)
    finally:
        backend.disconnect()


def test_encoded_scan_guard_keeps_native_path_for_unrepresentable_literal() -> None:
    # A candidate scan guard must never introduce an out-of-range literal on
    # a narrow integer column, even when the exact semantic filter is empty.
    import ibis.expr.operations as ops

    table = ibis.table({"point": "int16"}, name="encoded")
    guard = time_filter._encoded_guard(
        table.point, "%Y%m%d", datetime(2025, 1, 1), ops.GreaterEqual
    )
    assert guard.equals(ibis.literal(True))


@pytest.mark.parametrize("representation", ["utc", "fixed", "encoded", "civil_date"])
def test_common_filter_reaches_parquet_scan(tmp_path: Path, representation: str) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    values: list[object] = [datetime(2025, 1, 1), datetime(2025, 1, 2), datetime(2025, 1, 3)]
    if representation in ("encoded", "civil_date"):
        values = (
            ["2025010100", "2025010200", "2025010300"]
            if representation == "encoded"
            else ["20250101", "20250102", "20250103"]
        )
    path = tmp_path / "events.parquet"
    pq.write_table(pa.table({"point": values}), path)
    backend = ibis.duckdb.connect(TimeZone="UTC")
    try:
        table = backend.read_parquet(path)
        if representation in ("encoded", "civil_date"):
            value, _ = source_time(
                table.point,
                axis("%Y%m%d%H" if representation == "encoded" else "%Y%m%d"),
                boundary_timezone="UTC",
                read_timezone="UTC",
                engine="duckdb",
            )
            if representation == "encoded":
                value = localize("UTC", value)
        else:
            value = localize(
                "UTC",
                table.point - ibis.interval(seconds=28800)
                if representation == "fixed"
                else table.point,
            )
        left = datetime(2025, 1, 1, tzinfo=timezone.utc)
        right = datetime(2025, 1, 2, tzinfo=timezone.utc)
        expression = table.filter((value >= left) & (value < right))
        expected = backend.execute(expression)
        optimized = lower_temporal(expression, "duckdb")
        pd.testing.assert_frame_equal(backend.execute(optimized), expected)
        plan = json.loads(
            backend.con.execute("EXPLAIN (FORMAT JSON) " + backend.compile(optimized)).fetchone()[1]
        )

        def scans(nodes: list[dict[str, object]]) -> list[dict[str, object]]:
            output = []
            for node in nodes:
                if "PARQUET" in str(node["name"]):
                    output.append(node)
                children = node.get("children")
                if isinstance(children, list):
                    output.extend(scans(children))
            return output

        native_scans = scans(plan)
        assert native_scans and "Filters" in native_scans[0]["extra_info"]
    finally:
        backend.disconnect()


def test_unowned_parser_does_not_acquire_canonical_widths() -> None:
    table = ibis.memtable({"point": ["2025081", "20250801", "20250802"]})
    value = localize("UTC", table.point.as_timestamp("%Y%m%d").cast("timestamp(6)"))
    left = datetime(2025, 8, 1, tzinfo=timezone.utc)
    expression = table.filter((value >= left) & (value < left + timedelta(days=1)))
    backend = ibis.duckdb.connect(TimeZone="UTC")
    try:
        actual = backend.execute(lower_temporal(expression, "duckdb"))
        assert actual.point.tolist() == ["2025081", "20250801"]
        pd.testing.assert_frame_equal(actual, backend.execute(expression))
    finally:
        backend.disconnect()


@pytest.mark.parametrize("engine", ["duckdb", "postgres", "mysql", "clickhouse", "trino"])
def test_native_envelope_compiles_for_every_backend(engine: str) -> None:
    table = ibis.table({"point": "timestamp"}, name="events")
    value = localize("America/New_York", table.point)
    left = datetime(2050, 1, 1, tzinfo=timezone.utc)
    expression = table.filter((value >= left) & (value < left + timedelta(days=400)))
    result = lower_temporal(expression, engine)
    sql = ibis.to_sql(result, dialect=engine)
    # Conservative raw bounds work outside any former directory/year limit.
    assert "2049" in sql and "2051" in sql
    assert "point" in sql


def test_named_zone_envelope_never_removes_native_residual() -> None:
    table = ibis.table({"point": "timestamp"}, name="events")
    value = localize("America/New_York", table.point)
    left = datetime(2050, 1, 1, tzinfo=timezone.utc)
    expression = table.filter(value >= left)
    rewritten = time_filter.optimize_time(expression)
    assert expression.op().predicates[0] in rewritten.op().predicates
    assert len(rewritten.op().predicates) == 2
