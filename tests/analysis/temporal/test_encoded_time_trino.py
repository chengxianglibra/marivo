"""Opt-in real Hive/Iceberg scan constraints; no EXPLAIN enters library execution."""

import json
import os
import uuid
from datetime import date

import ibis
import pytest

from marivo.analysis.compiler.source_time import source_time, source_time_predicate
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
            raw = source_time_predicate(
                table.point,
                field,
                authority,
                start=date(2026, 7, 3),
                end=date(2026, 7, 5),
                engine="trino",
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
                    # Safe parsing has no equivalent raw encoding inverse: all
                    # partitions must remain eligible until parsing succeeds.
                    rows = read.execute(optimized).fetchall()
                    parsed_stats = dict(read.stats)
                    assert len(rows) == 200
                    assert sorted(rows) == sorted(read.execute(previous).fetchall())
                    first, last = (20260703, 20260705) if integer else ("20260703", "20260705")
                    physical = (table.point >= first) & (table.point < last)
                    narrowed = ibis.to_sql(table.filter(physical, raw), dialect="trino")
                    assert sorted(read.execute(narrowed).fetchall()) == sorted(rows)
                    narrowed_stats = dict(read.stats)
                    assert 0 < narrowed_stats["processedRows"] < parsed_stats["processedRows"]
                    physical_plan = json.loads(
                        read.execute("EXPLAIN (TYPE IO, FORMAT JSON) " + narrowed).fetchone()[0]
                    )
                    assert any(
                        item["columnName"] == "point" and item["domain"]["ranges"]
                        for entry in physical_plan["inputTableColumnInfos"]
                        for item in entry["constraint"]["columnConstraints"]
                    )
                    destination = os.environ.get("MARIVO_TEMPORAL_EVIDENCE_DIR")
                    if destination:
                        from pathlib import Path

                        Path(destination).mkdir(parents=True, exist_ok=True)
                        Path(destination, f"parse-scan-{connector}-{integer}.json").write_text(
                            json.dumps(
                                {
                                    "safe_sql": optimized,
                                    "safe_plan": plan,
                                    "safe_stats": parsed_stats,
                                    "physical_sql": narrowed,
                                    "physical_plan": physical_plan,
                                    "physical_stats": narrowed_stats,
                                    "expected_rows": 200,
                                },
                                sort_keys=True,
                                default=str,
                            )
                        )
                finally:
                    read.close()
        finally:
            if created:
                cursor.execute(f"DROP TABLE {target}").fetchall()
            cursor.close()
