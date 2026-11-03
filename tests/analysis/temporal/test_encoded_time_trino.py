"""Opt-in real Hive/Iceberg scan constraints; no EXPLAIN enters library execution."""

import json
import os
import uuid
from datetime import date
from pathlib import Path

import ibis
import pytest

import marivo.analysis as mv
from marivo.analysis.compiler.source_time import source_time, source_time_predicate
from tests.analysis.temporal.encoded_time_fixtures import axis
from tests.datasource.environment.trino_analysis import connection
from tests.shared_fixtures import declared_test_timezone
from tests.support.source_trace import SourceTrace

pytestmark = pytest.mark.runtime


@pytest.mark.parametrize("connector", ["iceberg", "hive"])
@pytest.mark.parametrize("integer", [False, True])
@pytest.mark.parametrize("zone", ["UTC", "Asia/Shanghai"])
def test_native_partition_constraints_and_scanned_rows(
    connector: str,
    integer: bool,
    zone: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_trace: SourceTrace,
) -> None:
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
                table.point,
                field,
                boundary_timezone=zone,
                source_timezone=declared_test_timezone(field, zone),
                engine="trino",
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
                    rows = read.execute(optimized).fetchall()
                    raw_stats = dict(read.stats)
                    assert len(rows) == 200
                    assert sorted(rows) == sorted(read.execute(previous).fetchall())
                    parsed_stats = dict(read.stats)
                    assert raw_stats["processedRows"] == 200
                    assert raw_stats["processedRows"] < parsed_stats["processedRows"]
                    assert any(
                        item["columnName"] == "point" and item["domain"]["ranges"]
                        for item in constraints
                    )
                    public_evidence: dict[str, object] = {}
                    if connector == "iceberg" and not integer and zone == "Asia/Shanghai":
                        datasource = tmp_path / "models" / "datasources"
                        semantic = tmp_path / "models" / "semantic" / "sales"
                        datasource.mkdir(parents=True)
                        semantic.mkdir(parents=True)
                        (tmp_path / "marivo.toml").write_text('[project]\nname="raw-partition"\n')
                        (datasource / "warehouse.py").write_text(
                            "import marivo.datasource as md\n"
                            "md.trino(name='warehouse',host='127.0.0.1',port=18080,"
                            "catalog='iceberg',schema='analysis',"
                            "user_env='MARIVO_PARTITION_READER',wall_clock_timezone='Asia/Shanghai')\n"
                        )
                        (semantic / "_domain.py").write_text(
                            "import marivo.semantic as ms\nms.domain(name='sales',owner='Test',default=True)\n"
                        )
                        (semantic / "events.py").write_text(
                            "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                            f"events=ms.entity(name='events',datasource=ms.ref.datasource('warehouse'),source=md.table({name!r}),primary_key=['id'])\n"
                            "point=ms.time_dimension_column(name='point',entity=events,column='point',parse=ms.strptime('%Y%m%d'),is_default=True)\n"
                        )
                        with monkeypatch.context() as patch:
                            patch.setenv("MARIVO_PARTITION_READER", "analysis_reader")
                            patch.chdir(tmp_path)
                            session = mv.session.get_or_create("raw-partition", timezone=zone)
                            events = session.catalog.get("entity:sales.events")
                            point = session.catalog.get("time_dimension:sales.events.point")
                            scope = session.time_scope(start="2026-07-03", end="2026-07-05")
                            result = session.observe(
                                {"value": events.count(time=point)}, by=(scope,)
                            ).compute()
                            assert result.to_pandas()["value"].tolist() == [200]
                        public_sql = next(
                            sql for sql in source_trace.native_sql if name in sql and "WHERE" in sql
                        )
                        public_plan = json.loads(
                            read.execute("EXPLAIN (TYPE IO, FORMAT JSON) " + public_sql).fetchone()[
                                0
                            ]
                        )
                        assert any(
                            item["columnName"] == "point" and item["domain"]["ranges"]
                            for entry in public_plan["inputTableColumnInfos"]
                            for item in entry["constraint"]["columnConstraints"]
                        )
                        read.execute(public_sql).fetchall()
                        assert read.stats["processedRows"] == 200
                        public_evidence = {
                            "public_sql": public_sql,
                            "public_plan": public_plan,
                            "public_stats": dict(read.stats),
                        }
                    destination = os.environ.get("MARIVO_TEMPORAL_EVIDENCE_DIR")
                    if destination:
                        Path(destination).mkdir(parents=True, exist_ok=True)
                        Path(
                            destination,
                            f"parse-scan-{connector}-{integer}-{zone.replace('/', '-')}.json",
                        ).write_text(
                            json.dumps(
                                {
                                    "timezone": zone,
                                    "raw_sql": optimized,
                                    "raw_plan": plan,
                                    "raw_stats": raw_stats,
                                    "parsed_sql": previous,
                                    "parsed_stats": parsed_stats,
                                    "expected_rows": 200,
                                    **public_evidence,
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
