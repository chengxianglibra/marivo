"""Opt-in real Hive/Iceberg scan constraints; no EXPLAIN enters library execution."""

import json
import os
import uuid
from collections.abc import Callable
from datetime import date
from pathlib import Path

import ibis
import pytest

import marivo.analysis as mv
from marivo.analysis.compiler.source_time import source_time, source_time_predicate
from marivo.semantic.reader import SemanticProject
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
                            "user=md.env('MARIVO_PARTITION_READER'),wall_clock_timezone='Asia/Shanghai')\n"
                        )
                        (semantic / "_domain.py").write_text(
                            "import marivo.semantic as ms\nms.domain(name='sales',owner='Test',default=True)\n"
                        )
                        (semantic / "events.py").write_text(
                            "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                            f"events=ms.entity(name='events',datasource=ms.ref.datasource('warehouse'),source=md.table({name!r}),primary_key=['id'])\n"
                            "point=ms.time_dimension_column(name='point',entity=events,column='point',parse=ms.strptime('%Y%m%d'))\n"
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


@pytest.mark.parametrize("integer", [False, True])
@pytest.mark.parametrize("start_hour", [0, 3])
def test_hour_prefix_public_filters_prune_both_iceberg_partitions(
    integer: bool,
    start_hour: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    if os.environ.get("MARIVO_TRINO_ANALYSIS_TEST") != "1":
        pytest.skip("Set MARIVO_TRINO_ANALYSIS_TEST=1 for the real Trino qualification server")
    name = f"hour_range_{uuid.uuid4().hex}"
    target = f'"iceberg".analysis.{name}'
    with connection(admin=True) as admin:
        cursor = admin.cursor()
        created = False
        try:
            cursor.execute(
                f"CREATE TABLE {target} (id BIGINT, amount BIGINT, day VARCHAR, hour {'BIGINT' if integer else 'VARCHAR'}) "
                "WITH (partitioning=ARRAY['day','hour'], format='PARQUET')"
            ).fetchall()
            created = True
            encoded_hour = "CAST(h AS BIGINT)" if integer else "lpad(CAST(h AS VARCHAR), 2, '0')"
            cursor.execute(
                f"INSERT INTO {target} SELECT d * 1000 + h * 10 + r, r, CAST(20261000 + d AS VARCHAR), {encoded_hour} "
                "FROM UNNEST(sequence(27,29)) AS dates(d) CROSS JOIN UNNEST(sequence(0,23)) AS hours(h) CROSS JOIN UNNEST(sequence(1,10)) AS rows(r)"
            ).fetchall()
            lower_hour = str(start_hour) if integer else repr(f"{start_hour:02d}")
            cursor.execute(
                f"INSERT INTO {target} VALUES (1,1000,'20261028',NULL),(2,1000,NULL,{lower_hour})"
            ).fetchall()
            semantic_project_factory(
                {
                    "datasources/warehouse.py": "import marivo.datasource as md\n"
                    "md.trino(name='warehouse',host='127.0.0.1',port=18080,catalog='iceberg',schema='analysis',user=md.env('MARIVO_PARTITION_READER'),wall_clock_timezone='Asia/Shanghai')\n",
                    "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Test',default=True)\n",
                    "sales/events.py": "import marivo.semantic as ms\nimport marivo.datasource as md\n"
                    f"events=ms.entity(name='events',datasource=ms.ref.datasource('warehouse'),source=md.table({name!r}),primary_key=['id'])\n"
                    "identity=ms.dimension_column(name='id',entity=events,column='id')\n"
                    "day=ms.time_dimension_column(name='day',entity=events,column='day',parse=ms.strptime('%Y%m%d'))\n"
                    "hour=ms.time_dimension_column(name='hour',entity=events,column='hour',parse=ms.hour_prefix(day))\n"
                    "amount=ms.measure_column(name='amount',entity=events,column='amount',additivity=ms.additive_all())\n"
                    "@ms.metric()\ndef total():\n    return amount.sum(time=hour)\n",
                }
            )
            monkeypatch.setenv("MARIVO_PARTITION_READER", "analysis_reader")
            monkeypatch.chdir(tmp_path)
            monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
            session = mv.session.get_or_create("hour-partition", timezone="Asia/Shanghai")
            events = session.catalog.get("entity:sales.events")
            hour = session.catalog.get("time_dimension:sales.events.hour")
            identity = session.catalog.get("dimension:sales.events.id")
            metrics = {
                "n": events.count(time=hour),
                "total": session.catalog.get("metric:sales.total"),
            }
            start_text, end_text = (
                f"2026-10-28 {start_hour:02d}:00:00",
                f"2026-10-28 {start_hour + 1:02d}:00:00",
            )
            scope = session.time_scope(start=start_text, end=end_text)
            upper_hour = str(start_hour + 1) if integer else repr(f"{start_hour + 1:02d}")
            raw_sql = f"SELECT count(*),sum(amount) FROM {target} WHERE day='20261028' AND hour>={lower_hour} AND hour<{upper_hour}"
            native_clock = (
                "date_add('hour',CAST(hour AS BIGINT),CAST(date_parse(day,'%Y%m%d') AS TIMESTAMP))"
            )
            native_sql = f"SELECT count(*),sum(amount) FROM {target} WHERE {native_clock}>=TIMESTAMP '{start_text}' AND {native_clock}<TIMESTAMP '{end_text}'"
            evidence: dict[str, object] = {}
            with connection() as reader:
                read = reader.cursor()
                try:
                    expected = read.execute(raw_sql).fetchone()
                    raw_stats = dict(read.stats)
                    assert expected == [10, 55]
                    assert read.execute(native_sql).fetchone() == expected
                    native_stats = dict(read.stats)
                    read.execute(
                        f"SELECT count(*),sum(amount) FROM {target} WHERE day='20261028'"
                    ).fetchone()
                    day_stats = dict(read.stats)
                    for mode in ("scope", "calendar", "within", "and"):
                        source_trace.native_sql.clear()
                        if mode in ("scope", "calendar"):
                            frame = session.observe(
                                metrics,
                                by=(scope.by(mv.grain("hour")) if mode == "calendar" else scope,),
                            )
                        else:
                            predicate = hour.within(scope)
                            if mode == "and":
                                predicate = predicate & (identity > 0)
                            frame = session.observe(metrics, where=predicate)
                        actual = frame.compute().to_pandas()
                        assert actual[["n", "total"]].values.tolist() == [[10, 55]]
                        sql = next(
                            sql for sql in source_trace.native_sql if name in sql and "WHERE" in sql
                        )
                        plan = json.loads(
                            read.execute("EXPLAIN (TYPE IO, FORMAT JSON) " + sql).fetchone()[0]
                        )
                        constraints = [
                            item
                            for entry in plan["inputTableColumnInfos"]
                            for item in entry["constraint"]["columnConstraints"]
                        ]
                        assert {
                            item["columnName"] for item in constraints if item["domain"]["ranges"]
                        } >= {"day", "hour"}, plan
                        read.execute(sql).fetchall()
                        stats = dict(read.stats)
                        assert stats["processedRows"] == raw_stats["processedRows"] == 10
                        assert stats["processedRows"] < day_stats["processedRows"]
                        evidence[mode] = {"sql": sql, "plan": plan, "stats": stats}
                    destination = os.environ.get("MARIVO_TEMPORAL_EVIDENCE_DIR")
                    if destination:
                        Path(destination).mkdir(parents=True, exist_ok=True)
                        Path(
                            destination, f"hour-partition-{integer}-{start_hour:02d}.json"
                        ).write_text(
                            json.dumps(
                                {
                                    "raw_sql": raw_sql,
                                    "raw_stats": raw_stats,
                                    "native_sql": native_sql,
                                    "native_stats": native_stats,
                                    "day_stats": day_stats,
                                    "public": evidence,
                                },
                                default=str,
                                sort_keys=True,
                            )
                        )
                finally:
                    read.close()
        finally:
            if created:
                cursor.execute(f"DROP TABLE {target}").fetchall()
            cursor.close()
