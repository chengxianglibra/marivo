"""Opt-in real Hive/Iceberg scan constraints; no EXPLAIN enters library execution."""

import json
import os
import uuid
from datetime import date

import ibis
import pytest

from marivo.analysis.compiler.source_time import encoded_time_predicate, source_time
from tests.analysis.temporal.encoded_time_fixtures import axis
from tests.datasource.environment.trino_analysis import connection

pytestmark = pytest.mark.runtime


@pytest.mark.parametrize("connector", ["iceberg", "hive"])
@pytest.mark.parametrize("integer", [False, True])
def test_native_partition_constraints_and_scanned_rows(connector: str, integer: bool) -> None:
    if os.environ.get("MARIVO_TRINO_ANALYSIS_TEST") != "1":
        pytest.skip("Set MARIVO_TRINO_ANALYSIS_TEST=1 for the real Trino qualification server")
    catalog = (
        os.environ.get("MARIVO_TRINO_HIVE_CATALOG", "hive") if connector == "hive" else "iceberg"
    )
    assert catalog.replace("_", "").isalnum()
    with connection(admin=True) as admin:
        cursor = admin.cursor()
        name = f"encoded_range_{uuid.uuid4().hex}"
        target = f'"{catalog}".analysis.{name}'
        created = False
        try:
            catalogs = {row[0] for row in cursor.execute("SHOW CATALOGS").fetchall()}
            if catalog not in catalogs:
                pytest.skip(f"Real {connector} catalog {catalog!r} is not configured")
            cursor.execute(f'CREATE SCHEMA IF NOT EXISTS "{catalog}".analysis').fetchall()
            property_name = "partitioning" if connector == "iceberg" else "partitioned_by"
            cursor.execute(
                f"CREATE TABLE {target} (id BIGINT, payload VARCHAR, point {'BIGINT' if integer else 'VARCHAR'}) "
                f"WITH ({property_name}=ARRAY['point'], format='PARQUET')"
            ).fetchall()
            created = True
            cursor.execute(
                f"INSERT INTO {target} SELECT day * 100 + row, CAST(day * 100 + row AS VARCHAR), "
                + (
                    "CAST(20260700 + day AS BIGINT)"
                    if integer
                    else "CAST(20260700 + day AS VARCHAR)"
                )
                + " FROM UNNEST(sequence(1, 8)) AS d(day) CROSS JOIN UNNEST(sequence(1, 100)) AS r(row)"
            ).fetchall()
            table = ibis.table(
                {"id": "int64", "payload": "string", "point": "int64" if integer else "string"},
                name=name,
                database="analysis",
                catalog=catalog,
            )
            field = axis("%Y%m%d")
            parsed, authority = source_time(
                table.point, field, boundary_timezone="UTC", read_timezone="UTC", engine="trino"
            )
            raw = encoded_time_predicate(
                table.point, field, authority, start=date(2026, 7, 3), end=date(2026, 7, 5)
            )
            assert raw is not None
            optimized = ibis.to_sql(table.filter(raw), dialect="trino")
            previous = ibis.to_sql(
                table.filter((parsed >= date(2026, 7, 3)) & (parsed < date(2026, 7, 5))),
                dialect="trino",
            )
            with connection(catalog=catalog) as reader:
                read = reader.cursor()
                try:
                    plan = json.loads(
                        read.execute("EXPLAIN (TYPE IO, FORMAT JSON) " + optimized).fetchone()[0]
                    )
                    constraints = plan["inputTableColumnInfos"][0]["constraint"][
                        "columnConstraints"
                    ]
                    point = next(
                        item["domain"] for item in constraints if item["columnName"] == "point"
                    )
                    # Hive enumerates the selected partition points in its
                    # scan domain; Iceberg preserves the half-open range.
                    expected_ranges = (
                        [
                            {
                                "low": {"value": day, "bound": "EXACTLY"},
                                "high": {"value": day, "bound": "EXACTLY"},
                            }
                            for day in ("20260703", "20260704")
                        ]
                        if connector == "hive"
                        else [
                            {
                                "low": {"value": "20260703", "bound": "EXACTLY"},
                                "high": {"value": "20260705", "bound": "BELOW"},
                            }
                        ]
                    )
                    expected_domain = {"nullsAllowed": False, "ranges": expected_ranges}
                    assert point == expected_domain
                    rows = read.execute(optimized).fetchall()
                    optimized_rows = read.stats["processedRows"]
                    assert len(rows) == 200
                    assert sorted(rows) == sorted(read.execute(previous).fetchall())
                    assert len(read.execute(ibis.to_sql(table, dialect="trino")).fetchall()) == 800
                    assert 0 < optimized_rows < read.stats["processedRows"]
                    # Grid-key-dependent CASE predicates need an independent
                    # scan envelope even though each cell uses raw comparisons.
                    first_day = 20260701 if integer else "20260701"
                    members = table.filter(table.point == first_day).select(member=table.id % 100)
                    mapping = members.mutate(cell=ibis.literal("first")).union(
                        members.mutate(cell=ibis.literal("second")), distinct=False
                    )
                    day3 = 20260703 if integer else "20260703"
                    day4 = 20260704 if integer else "20260704"
                    day5 = 20260705 if integer else "20260705"
                    grid_queries = []
                    for events in (table, table.filter(raw)):
                        cells = ibis.cases(
                            (
                                mapping.cell == "first",
                                (events.point >= day3) & (events.point < day4),
                            ),
                            (
                                mapping.cell == "second",
                                (events.point >= day4) & (events.point < day5),
                            ),
                            else_=False,
                        )
                        joined = events.inner_join(
                            mapping, [(events.id % 100) == mapping.member, cells]
                        )
                        grid_queries.append(ibis.to_sql(joined.select(events), dialect="trino"))
                    previous_grid = read.execute(grid_queries[0]).fetchall()
                    previous_scan = read.stats["processedRows"]
                    assert sorted(read.execute(grid_queries[1]).fetchall()) == sorted(previous_grid)
                    assert len(previous_grid) == 200
                    assert read.stats["processedRows"] < previous_scan
                    grid_plan = json.loads(
                        read.execute(
                            "EXPLAIN (TYPE IO, FORMAT JSON) " + grid_queries[1]
                        ).fetchone()[0]
                    )
                    assert any(
                        column["columnName"] == "point" and column["domain"] == expected_domain
                        for item in grid_plan["inputTableColumnInfos"]
                        for column in item["constraint"]["columnConstraints"]
                    )
                finally:
                    read.close()
        finally:
            if created:
                cursor.execute(f"DROP TABLE {target}").fetchall()
            cursor.close()
