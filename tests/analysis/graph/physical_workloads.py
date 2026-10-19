"""Physical integer-sum fixtures shared by native and local-file route regressions."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import ibis
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.frame_data import MemoryFrameBacking, StoredFrameBacking
from marivo.datasource.adapters import provider_for
from marivo.datasource.ir import CsvSourceIR, JsonSourceIR, ParquetSourceIR, TableSourceIR
from tests.datasource.source_cases import Case, SourceData, datasource, source_case

START = datetime(2026, 8, 1, tzinfo=timezone.utc)
GROUPS = 16
BATCH_ROWS = 2048


def _rows(
    facts: int, scenario: Literal["baseline", "all_null", "empty"]
) -> list[dict[str, object]]:
    if facts < 2:
        raise ValueError("Integer-sum fixtures require at least two requested facts")
    if scenario == "empty":
        return []
    if scenario not in ("baseline", "all_null"):
        raise ValueError("Integer-sum fixture has an unsupported scenario")
    return [
        {
            "id": 9007199254740992 + index,
            "revision": 1,
            "tenant": "a",
            "owner": index % (GROUPS - 1),
            "amount": None if scenario == "all_null" or index % 23 == 0 else index % 17 + 1,
            "y": index % 31 + 1,
            "happened": START,
            "kind": "started" if index % 2 == 0 else "finished",
            "seq": index,
        }
        for index in range(facts)
    ]


def _literal(value: object, backend: str) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, datetime):
        rendered = value.strftime("%Y-%m-%d %H:%M:%S.%f")
        return f"TIMESTAMP '{rendered}'" if backend == "trino" else f"'{rendered}'"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    if type(value) is int:
        return str(value)
    raise ValueError("Fixture literal has an unsupported type")


def _values(rows: Sequence[Mapping[str, object]], backend: str) -> str:
    return ",".join(
        "(" + ",".join(_literal(value, backend) for value in row.values()) + ")" for row in rows
    )


def _data(rows: list[dict[str, object]], backend: str) -> SourceData:
    timestamp = "TIMESTAMP(6)" if backend == "trino" else "TIMESTAMP"
    return SourceData(
        "id BIGINT, revision BIGINT, tenant VARCHAR(10), owner BIGINT, amount BIGINT, "
        f"y BIGINT, happened {timestamp}, kind VARCHAR(20), seq BIGINT",
        _values(rows, backend),
        "id Int64, revision Int64, tenant String, owner Int64, amount Nullable(Int64), "
        "y Int64, happened DateTime64(6, 'UTC'), kind String, seq Int64",
        rows,
    )


@contextmanager
def _tables(
    backend: str,
    profile: str,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, object]],
) -> Iterator[tuple[Case, str, str]]:
    subjects = [{"tenant": "a", "sid": index} for index in range(GROUPS)]
    if backend == "duckdb":
        if profile not in (
            "table",
            "ordinary-table",
            "view",
            "main-table",
            "main-view",
            "csv",
            "parquet",
            "local-json",
        ) or (backend != "duckdb" and profile in ("csv", "parquet", "local-json")):
            raise ValueError("Local cost fixture requires a native table or view profile")
        path = root / ("r96." + backend)
        admin = ibis.duckdb.connect(path)
        try:
            data = _data(rows[:1], backend)
            admin.raw_sql("CREATE TABLE r96_facts (" + data.columns + ")")
            admin.raw_sql("CREATE TABLE r96_other (" + data.columns + ")")
            admin.raw_sql("CREATE TABLE r96_subjects (tenant VARCHAR(10), sid BIGINT)")
            admin.raw_sql("INSERT INTO r96_subjects VALUES " + _values(subjects, backend))
            for start in range(0, len(rows), BATCH_ROWS):
                values = _values(rows[start : start + BATCH_ROWS], backend)
                admin.raw_sql("INSERT INTO r96_facts VALUES " + values)
                admin.raw_sql("INSERT INTO r96_other VALUES " + values)
            admin.raw_sql("CREATE VIEW r96_view AS SELECT * FROM r96_facts")

        finally:
            admin.disconnect()
        source = TableSourceIR("r96_view" if "view" in profile else "r96_facts")
        selected_source: TableSourceIR | CsvSourceIR | ParquetSourceIR | JsonSourceIR
        if profile == "parquet":
            file = root / "r96_facts.parquet"
            pq.write_table(pa.Table.from_pylist(rows), file)
            selected_source = ParquetSourceIR(str(file))
        elif profile in ("csv", "local-json"):
            serialized = [
                {
                    name: value.strftime("%Y-%m-%d %H:%M:%S.%f")
                    if isinstance(value, datetime)
                    else value
                    for name, value in row.items()
                }
                for row in rows
            ]
            if profile == "csv":
                file = root / "r96_facts.csv"
                with file.open("w", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                    writer.writeheader()
                    writer.writerows(serialized)
                selected_source = CsvSourceIR(str(file))
            else:
                file = root / "r96_facts.json"
                file.write_text(json.dumps(serialized))
                selected_source = JsonSourceIR(str(file))
        else:
            selected_source = source
        ds = datasource(backend, {"path": str(path), "read_only": True})
        with provider_for(backend).open(ds) as session:
            yield (
                Case(
                    session,
                    selected_source,
                    {"backend": backend, "profile": profile, "read_only": True},
                ),
                "r96_subjects",
                "r96_other",
            )
        return
    native_profile = {"trino": "iceberg", "clickhouse": "mergetree"}.get(backend, "table")
    if profile != "ordinary-table":
        native_profile = profile
    with source_case(backend, native_profile, root, monkeypatch, _data(rows[:1], backend)) as case:
        assert isinstance(case.source, TableSourceIR)
        subjects_name, other = case.source.table + "_subjects", case.source.table + "_other"
        database = case.source.database
        if backend == "postgres":
            from tests.datasource.environment import postgres_analysis as pg

            with pg.connection(admin=True) as admin:
                prefix = "public."
                try:
                    admin.execute(
                        f"CREATE TABLE {prefix}{subjects_name} (tenant VARCHAR(10), sid BIGINT)"
                    )
                    admin.execute(
                        f"CREATE TABLE {prefix}{other} ({_data(rows[:1], backend).columns})"
                    )
                    admin.execute(
                        f"INSERT INTO {prefix}{subjects_name} VALUES " + _values(subjects, backend)
                    )
                    for start in range(0, len(rows), BATCH_ROWS):
                        batch = rows[start : start + BATCH_ROWS]
                        admin.execute(
                            f"INSERT INTO {prefix}{other} VALUES " + _values(batch, backend)
                        )
                        if start == 0:
                            batch = batch[1:]
                        if batch:
                            admin.execute(
                                f"INSERT INTO {prefix}{case.source.table} VALUES "
                                + _values(batch, backend)
                            )
                    admin.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO analysis_reader")
                    case.environment["profile"] = native_profile
                    yield case, subjects_name, other
                finally:
                    admin.execute(f"DROP TABLE IF EXISTS {prefix}{other}")
                    admin.execute(f"DROP TABLE IF EXISTS {prefix}{subjects_name}")
        elif backend == "mysql":
            from tests.datasource.environment import mysql_analysis as mysql

            with mysql.connection(admin=True) as admin, admin.cursor() as cursor:
                try:
                    cursor.execute(
                        f"CREATE TABLE {subjects_name} (tenant VARCHAR(10), sid BIGINT) ENGINE=InnoDB"
                    )
                    cursor.execute(
                        f"CREATE TABLE {other} ({_data(rows[:1], backend).columns}) ENGINE=InnoDB"
                    )
                    cursor.execute(
                        f"INSERT INTO {subjects_name} VALUES " + _values(subjects, backend)
                    )
                    for start in range(0, len(rows), BATCH_ROWS):
                        batch = rows[start : start + BATCH_ROWS]
                        cursor.execute(f"INSERT INTO {other} VALUES " + _values(batch, backend))
                        if start == 0:
                            batch = batch[1:]
                        if batch:
                            cursor.execute(
                                f"INSERT INTO {case.source.table} VALUES " + _values(batch, backend)
                            )
                    case.environment["profile"] = native_profile
                    yield case, subjects_name, other
                finally:
                    cursor.execute(f"DROP TABLE IF EXISTS {other}")
                    cursor.execute(f"DROP TABLE IF EXISTS {subjects_name}")
        elif backend == "trino":
            from tests.datasource.environment import trino_analysis as trino

            assert isinstance(database, tuple)
            catalog = database[0]
            prefix = ".".join(database) + "."
            with trino.connection(admin=True, catalog=catalog) as admin:
                cursor = admin.cursor()
                try:
                    cursor.execute(
                        f"CREATE TABLE {prefix}{subjects_name} (tenant VARCHAR(10), sid BIGINT)"
                    ).fetchall()
                    cursor.execute(
                        f"CREATE TABLE {prefix}{other} ({_data(rows[:1], backend).columns})"
                    ).fetchall()
                    cursor.execute(
                        f"INSERT INTO {prefix}{subjects_name} VALUES " + _values(subjects, backend)
                    ).fetchall()
                    for start in range(0, len(rows), BATCH_ROWS):
                        batch = rows[start : start + BATCH_ROWS]
                        cursor.execute(
                            f"INSERT INTO {prefix}{other} VALUES " + _values(batch, backend)
                        ).fetchall()
                        if start == 0:
                            batch = batch[1:]
                        if batch:
                            cursor.execute(
                                f"INSERT INTO {prefix}{case.source.table} VALUES "
                                + _values(batch, backend)
                            ).fetchall()
                    case.environment["profile"] = native_profile
                    yield case, subjects_name, other
                finally:
                    cursor.execute(f"DROP TABLE IF EXISTS {prefix}{other}").fetchall()
                    cursor.execute(f"DROP TABLE IF EXISTS {prefix}{subjects_name}").fetchall()
                    cursor.close()
        else:
            from tests.datasource.environment import clickhouse_analysis as ch

            assert isinstance(database, str)
            prefix = database + "."
            if native_profile == "distributed":
                ports = ch.CLUSTER_HTTP_PORTS
                local_facts = case.source.table.removesuffix("_distributed")
                local_subjects, local_other = subjects_name + "_local", other + "_local"
                try:
                    for shard, port in enumerate(ports):
                        with ch.connection(admin=True, port=port) as admin:
                            admin.command(
                                f"CREATE TABLE {prefix}{local_subjects} (tenant String, sid Int64) ENGINE=MergeTree ORDER BY tuple()"
                            )
                            admin.command(
                                f"CREATE TABLE {prefix}{local_other} ({_data(rows[:1], backend).clickhouse_columns}) ENGINE=MergeTree ORDER BY tuple()"
                            )
                            admin.command(
                                f"CREATE TABLE {prefix}{subjects_name} AS {prefix}{local_subjects} ENGINE=Distributed({ch.CLUSTER}, {database}, {local_subjects}, rand())"
                            )
                            admin.command(
                                f"CREATE TABLE {prefix}{other} AS {prefix}{local_other} ENGINE=Distributed({ch.CLUSTER}, {database}, {local_other}, rand())"
                            )
                            shard_subjects = subjects[shard :: len(ports)]
                            admin.insert(
                                prefix + local_subjects,
                                [list(row.values()) for row in shard_subjects],
                                column_names=list(subjects[0]),
                            )
                            shard_rows = rows[shard :: len(ports)]
                            for start in range(0, len(shard_rows), BATCH_ROWS):
                                batch = shard_rows[start : start + BATCH_ROWS]
                                admin.insert(
                                    prefix + local_other,
                                    [list(row.values()) for row in batch],
                                    column_names=list(rows[0]),
                                )
                                if shard == 0 and start == 0:
                                    batch = batch[1:]
                                if batch:
                                    admin.insert(
                                        prefix + local_facts,
                                        [list(row.values()) for row in batch],
                                        column_names=list(rows[0]),
                                    )
                    case.environment["profile"] = native_profile
                    yield case, subjects_name, other
                finally:
                    for port in ports:
                        with ch.connection(admin=True, port=port) as admin:
                            for table in (other, subjects_name, local_other, local_subjects):
                                admin.command(f"DROP TABLE IF EXISTS {prefix}{table}")
                return
            if native_profile != "mergetree":
                raise ValueError("Unknown ClickHouse cost profile")
            with ch.connection(admin=True) as admin:
                try:
                    admin.command(
                        f"CREATE TABLE {prefix}{subjects_name} (tenant String, sid Int64) ENGINE=MergeTree ORDER BY tuple()"
                    )
                    admin.command(
                        f"CREATE TABLE {prefix}{other} ({_data(rows[:1], backend).clickhouse_columns}) ENGINE=MergeTree ORDER BY tuple()"
                    )
                    admin.insert(
                        prefix + subjects_name,
                        [list(row.values()) for row in subjects],
                        column_names=list(subjects[0]),
                    )
                    for start in range(0, len(rows), BATCH_ROWS):
                        batch = rows[start : start + BATCH_ROWS]
                        admin.insert(
                            prefix + other,
                            [list(row.values()) for row in batch],
                            column_names=list(rows[0]),
                        )
                        if start == 0:
                            batch = batch[1:]
                        if batch:
                            admin.insert(
                                prefix + case.source.table,
                                [list(row.values()) for row in batch],
                                column_names=list(rows[0]),
                            )
                    case.environment["profile"] = native_profile
                    yield case, subjects_name, other
                finally:
                    admin.command(f"DROP TABLE IF EXISTS {prefix}{other}")
                    admin.command(f"DROP TABLE IF EXISTS {prefix}{subjects_name}")


def _author(
    root: Path,
    backend: str,
    case: Case,
    subjects: str,
    other: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = case.source.database if isinstance(case.source, TableSourceIR) else None
    if isinstance(case.source, TableSourceIR):
        facts_source = f"md.table({case.source.table!r}, database={database!r})"
    elif isinstance(case.source, ParquetSourceIR):
        facts_source = f"md.parquet({case.source.path!r})"
    elif isinstance(case.source, CsvSourceIR):
        facts_source = f"md.csv({case.source.path!r})"
    else:
        assert isinstance(case.source, JsonSourceIR)
        facts_source = f"md.json({case.source.path!r})"
    arguments = {
        **case.session.datasource.fields,
        **{key + "_env": value for key, value in case.session.datasource.env_refs.items()},
    }
    if "user" in arguments:
        reader = arguments.pop("user")
        if "user_env" not in arguments:
            monkeypatch.setenv("MARIVO_R96_READER", str(reader))
            arguments["user_env"] = "MARIVO_R96_READER"
    models = root / "models"
    (models / "datasources").mkdir(parents=True)
    (models / "semantic" / "cost").mkdir(parents=True)
    (root / "marivo.toml").write_text('[project]\nname="r96-cost"\n')
    (models / "datasources" / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        + f"md.{backend}(name='warehouse', "
        + ", ".join(f"{key}={value!r}" for key, value in arguments.items())
        + ")\n"
    )
    (models / "semantic" / "cost" / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='cost', owner='R9.6', default=True)\n"
    )
    code = "import marivo.datasource as md\nimport marivo.semantic as ms\n"
    code += f"subjects=ms.entity(name='subjects', datasource=ms.ref.datasource('warehouse'), source=md.table({subjects!r}, database={database!r}), primary_key=['tenant','sid'])\n"
    code += "subject_tenant=ms.dimension_column(name='tenant', entity=subjects, column='tenant')\nsubject_id=ms.dimension_column(name='sid', entity=subjects, column='sid')\n"
    for name, selected in (
        ("facts", facts_source),
        ("other", f"md.table({other!r}, database={database!r})"),
    ):
        code += f"{name}=ms.entity(name={name!r}, datasource=ms.ref.datasource('warehouse'), source={selected}, primary_key=['tenant','id','revision'])\n"
        for dimension in ("tenant", "owner", "revision", "kind", "seq", "id"):
            code += f"{name}_{dimension}=ms.dimension_column(name={dimension!r}, entity={name}, column={dimension!r})\n"
        code += f"{name}_amount=ms.measure_column(name='amount', entity={name}, column={'amount' if name == 'facts' else 'y'!r}, additivity=ms.additive_all())\n"
        code += f"{name}_y=ms.measure_column(name='y', entity={name}, column='y', additivity=ms.additive_all())\n"
        code += f"{name}_time=ms.time_dimension_column(name='happened', entity={name}, column='happened', granularity='second', parse=ms.timestamp(timezone='UTC'), is_default=True)\n"
        code += f"{name}_subject=ms.relationship(name={name + '_subject'!r}, from_entity={name}, to_entity=subjects, keys=[ms.join_on({name}_tenant, subject_tenant),ms.join_on({name}_owner,subject_id)])\n"
    code += "frozen=ms.finalize()\n"
    for name in ("facts", "other"):
        code += f"{name}_value=frozen.resolve({name}_amount)\n{name}_yvalue=frozen.resolve({name}_y)\n{name}_clock=frozen.resolve({name}_time)\n"
        code += f"@ms.metric()\ndef {name}_total():\n    return {name}_value.sum(empty='zero', time={name}_clock)\n"
        code += f"@ms.metric()\ndef {name}_ysum():\n    return {name}_yvalue.sum(empty='zero', time={name}_clock)\n"
    code += "@ms.metric()\ndef average():\n    return facts_value.mean(time=facts_clock)\n"
    code += (
        "@ms.metric()\ndef distinct():\n    return facts_value.count_distinct(time=facts_clock)\n"
    )
    code += (
        "@ms.metric()\ndef quantile():\n    return facts_value.quantile(0.5, time=facts_clock)\n"
    )
    (models / "semantic" / "cost" / "objects.py").write_text(code)
    ms.load(workspace_dir=root)


def _integer(value: object) -> int:
    if type(value) is not int:
        raise ValueError("Expected an exact fixture integer")
    assert isinstance(value, int)
    return value


@dataclass
class Workload:
    session: mv.Session
    rows: list[dict[str, object]]
    environment: dict[str, object]

    def values(self) -> mv.AnalysisFrame:
        return self.session.observe(
            {"value": self.session.catalog.metrics.get("cost.facts_total")},
            during=self.session.time_scope(start="2026-08-01", end="2026-08-02"),
            by=self.session.members(ms.ref.entity("cost.facts")),
        )

    def total(self) -> mv.AnalysisFrame:
        return self.session.observe(
            {"value": self.session.catalog.metrics.get("cost.facts_total")},
            during=self.session.time_scope(start="2026-08-01", end="2026-08-02"),
        )

    def validate(self, result: mv.AnalysisFrame) -> dict[str, bool]:
        expected = sum(_integer(row["amount"]) for row in self.rows if row["amount"] is not None)
        assert isinstance(result._backing, (MemoryFrameBacking, StoredFrameBacking))
        frame = result.to_pandas()
        assert frame[("value", "value")].tolist() == [expected]
        assert len(frame) == 1
        assert result._schema.axes == ()
        attachments = (
            result._backing.data.copy_attachments()
            if isinstance(result._backing, MemoryFrameBacking)
            else result._backing.data.attachments
        )
        assert all(attachment.name != "original_state" for attachment in attachments)
        return {"passed": True}


@contextmanager
def workload(
    backend: str,
    profile: str,
    facts: int,
    scenario: Literal["baseline", "all_null", "empty"],
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Workload]:
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(root))
    monkeypatch.chdir(root)
    rows = _rows(facts, scenario)
    with _tables(backend, profile, root, monkeypatch, rows) as (case, subjects, other):
        _author(root, backend, case, subjects, other, monkeypatch)
        session = mv.session.get_or_create("integer-sum-regression", report_timezone="UTC")
        yield Workload(session, rows, case.environment)
