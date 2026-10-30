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
        assert native_scans
        if representation in ("encoded", "civil_date"):
            assert "try_strptime" in backend.compile(optimized).lower()
            if "Filters" in native_scans[0]["extra_info"]:
                assert "try_strptime" in str(native_scans[0]["extra_info"]["Filters"]).lower()
        else:
            assert "Filters" in native_scans[0]["extra_info"]
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


def test_named_zone_with_raw_lineage_uses_exact_wall_bound() -> None:
    table = ibis.table({"point": "timestamp"}, name="events")
    value = localize("America/New_York", table.point)
    left = datetime(2050, 1, 1, tzinfo=timezone.utc)
    expression = table.filter(value >= left)
    rewritten = time_filter.optimize_time(expression)
    assert expression.op().predicates[0] not in rewritten.op().predicates
    assert len(rewritten.op().predicates) == 1
    assert rewritten.equals(table.filter(table.point >= datetime(2049, 12, 31, 19)))


@pytest.mark.parametrize("owned", [True, False, "projected"])
def test_gap_membership_rewrite_preserves_native_projection_and_unowned_fallback(
    owned: bool | str,
) -> None:
    table = ibis.memtable(
        {
            "id": [0, 1, 2, 3],
            "point": [
                datetime(2026, 3, 8, 2, 30),
                datetime(2026, 3, 8, 3),
                datetime(2026, 3, 8, 3, 30),
                None,
            ],
        },
        schema={"id": "int64", "point": "timestamp"},
    )
    raw = table.point if owned is True else table.point.coalesce(datetime(2026, 3, 8, 2, 30))
    if owned == "projected":
        table = table.mutate(derived=raw).view()
        raw = table.derived
    value = localize("America/New_York", raw)
    start = datetime(2026, 3, 8, 7, tzinfo=timezone.utc)
    expression = table.filter((value >= start) & (value < start + timedelta(minutes=15))).select(
        id=table.id, projected=value
    )
    backend = ibis.duckdb.connect(TimeZone="UTC")
    try:
        result = backend.execute(lower_temporal(expression, "duckdb"))
        if owned is True:
            assert result.id.tolist() == [0, 1]
            assert result.projected.tolist() == [start + timedelta(minutes=30), start]
        else:
            pd.testing.assert_frame_equal(result, backend.execute(expression))
            assert result.id.tolist() == [1]
    finally:
        backend.disconnect()


@pytest.mark.parametrize(
    "day,clocks,start,end,expected",
    [
        (
            "2026-03-08",
            ("02:30", "03:00", "03:30"),
            "07:00",
            "07:15",
            [0, 1],
        ),
        (
            "2026-11-01",
            ("00:59", "01:00", "01:15", "01:30", "01:59", "02:00"),
            "05:30",
            "06:30",
            [1, 2],
        ),
    ],
)
def test_prepared_view_retains_exact_gap_and_fold_raw_predicates(
    day: str,
    clocks: tuple[str, ...],
    start: str,
    end: str,
    expected: list[int],
) -> None:
    import ibis.expr.operations as ops

    table = ibis.memtable(
        {
            "id": range(len(clocks) + 1),
            "point": [*(datetime.fromisoformat(f"{day} {clock}") for clock in clocks), None],
        },
        schema={"id": "int64", "point": "timestamp"},
    )
    prepared = table.mutate(v=localize("America/New_York", table.point)).view()
    left = datetime.fromisoformat(f"{day} {start}").replace(tzinfo=timezone.utc)
    right = datetime.fromisoformat(f"{day} {end}").replace(tzinfo=timezone.utc)
    expression = prepared.filter(prepared.v >= left, prepared.v < right)
    rewritten = time_filter.optimize_time(expression)
    # Both predicates must reach retained raw clocks through the prepared view.
    # This also distinguishes the fold path where native NY membership agrees.
    assert all(
        isinstance(predicate, (ops.GreaterEqual, ops.Less))
        and isinstance(predicate.left, ops.Field)
        and predicate.left.name == "point"
        for predicate in rewritten.op().predicates
    )
    backend = ibis.duckdb.connect(TimeZone="UTC")
    try:
        result = backend.execute(lower_temporal(expression, "duckdb"))
        assert result.id.tolist() == expected
        if day == "2026-03-08":
            # Membership changed, while native projected gap values stay intact.
            assert result.v.tolist() == [left + timedelta(minutes=30), left]
    finally:
        backend.disconnect()
