"""Owned fixtures and public typed-graph workloads for the frozen R9.6 profiles."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypeAlias

import ibis
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization.graph_protocol import descriptor_plan
from marivo.datasource.adapters import provider_for
from marivo.datasource.ir import CsvSourceIR, JsonSourceIR, ParquetSourceIR, TableSourceIR
from scripts.r82_deviation_requirements import key_json
from tests.r9_source_cases import Case, SourceData, datasource, source_case

if TYPE_CHECKING:
    from marivo.analysis.core.model import DomainSignature
    from marivo.analysis.materialization import statistical_execution as _statistics

SCENARIOS = (
    "baseline",
    "multi-root-ratio-empty-groups",
    "exact-distinct-quantile",
    "comparison-members-observe",
    "joint-topk-attribution",
    "event-lifecycle-anchor",
    "deviation-runs",
    "association-lags",
    "forecast-models",
    "skew-empty-groups",
    "ties-null",
    "high-cardinality",
    "cross-batch-long-runs-unavailable",
    "many-occurrences-anchors-lags",
    "full-training-numeric-extremes",
)

Result: TypeAlias = (
    mv.MaterializedNumericRelation
    | mv.MaterializedRolledNumericRelation
    | mv.MaterializedRolledRatioRelation
    | mv.MaterializedGroupedNumericRelation
    | mv.MaterializedStatisticRelation
    | mv.MaterializedDifferenceRelation
    | mv.MaterializedRatioRelation
    | mv.MaterializedAttributionResult
    | mv.MaterializedRankingResult
    | mv.MaterializedDeviationResult
    | mv.MaterializedAssociationResult
    | mv.MaterializedForecastResult
    | mv.MaterializedTimeRunResult
    | mv.MaterializedJourneyResult
    | mv.MaterializedHistoryResult
    | mv.MaterializedAnchorDomain
    | mv.MaterializedCategoryRelation
    | mv.MaterializedSelectedNumericRelation
)
Logical: TypeAlias = (
    mv.LogicalStatisticRelation
    | mv.LogicalNumericRelation
    | mv.LogicalRankingResult
    | mv.LogicalAssociationResult
    | mv.LogicalForecastResult
    | mv.LogicalTimeRunResult
    | mv.LogicalAttributionResult
    | mv.LogicalDeviationResult
    | mv.LogicalRolledRatioRelation
    | mv.LogicalSelectedNumericRelation
)
SourceInput: TypeAlias = (
    mv.LogicalNumericRelation
    | mv.LogicalRolledNumericRelation
    | mv.LogicalDifferenceRelation
    | mv.LogicalRatioRelation
    | mv.LogicalCategoryRelation
)

START = datetime(2026, 8, 1, tzinfo=timezone.utc)
GROUPS = 16
BATCH_ROWS = 2048
CROSS_PERIODS = 1536
CROSS_GAP = 1200


def _rows(facts: int, scenario: str) -> list[dict[str, object]]:
    if facts < 2:
        raise ValueError("Cost workloads require at least two original facts")
    events = scenario in ("event-lifecycle-anchor", "many-occurrences-anchors-lags")
    history = scenario in (
        "forecast-models",
        "deviation-runs",
        "association-lags",
        "cross-batch-long-runs-unavailable",
        "full-training-numeric-extremes",
    )
    result: list[dict[str, object]] = []
    for index in range(facts):
        owner = (index // 2 if events else index) % (GROUPS - 1)
        if scenario == "skew-empty-groups":
            owner = 0 if index < facts * 9 // 10 else index % (GROUPS - 1)
        amount = None if index % 23 == 0 else index % 17 + 1
        if scenario == "high-cardinality":
            amount = index + 1
        elif scenario == "full-training-numeric-extremes":
            amount = (
                2**53 + 1024 + index % 17
                if index < 16
                else (1 if (index // 16) % 2 == 0 else -1) * (index % 17 + 1)
            )
        instant = START + timedelta(seconds=index) if events else START
        if history:
            periods = CROSS_PERIODS if scenario == "cross-batch-long-runs-unavailable" else 16
            instant += timedelta(days=index % periods)
        elif scenario in ("joint-topk-attribution", "comparison-members-observe"):
            instant -= timedelta(days=1 if index % 2 else 0)
        result.append(
            {
                "id": 9007199254740992 + index,
                "revision": 1,
                "tenant": "a",
                "owner": owner,
                "amount": amount,
                "y": index % 31 + 1,
                "happened": instant,
                "kind": "started" if index % 2 == 0 else "finished",
                "seq": index,
            }
        )
    return result


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
    if backend in ("duckdb", "sqlite"):
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
        admin = ibis.duckdb.connect(path) if backend == "duckdb" else ibis.sqlite.connect(path)
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
            if backend == "sqlite":
                admin.con.commit()
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
            from tests.multisource_environment import postgres_analysis as pg

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
            from tests.multisource_environment import mysql_analysis as mysql

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
            from tests.multisource_environment import trino_analysis as trino

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
            from tests.multisource_environment import clickhouse_analysis as ch

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
        code += f"{name}_total=ms.aggregate(name={name + '_total'!r}, measure={name}_amount, agg='sum', empty=ms.empty.zero())\n"
        code += f"{name}_ysum=ms.aggregate(name={name + '_ysum'!r}, measure={name}_y, agg='sum', empty=ms.empty.zero())\n"
    code += "average=ms.aggregate(name='average',measure=facts_amount,agg='mean')\n"
    code += "distinct=ms.aggregate(name='distinct',measure=facts_amount,agg='count_distinct',time=facts_time,empty=ms.empty.zero())\n"
    code += "quantile=ms.aggregate(name='quantile',measure=facts_amount,agg=('percentile',0.5),time=facts_time)\n"
    for event in ("started", "finished"):
        code += (
            f"@ms.event(name={event!r},identity=(facts_tenant,facts_id,facts_revision),occurred_at=facts_time,participants=(ms.participant(name='subject',path=(facts_subject,),cardinality='one'),),ai_context=ms.ai_context(business_definition='Owned cost fixture event.'))\n"
            f"def {event}(rows):\n    return ms.bind(facts_kind,rows)=={event!r}\n"
        )
    code += "order=ms.business_order(name='order',subject=subjects,sequences=(ms.event_sequence(started,facts_seq,order='integer'),ms.event_sequence(finished,facts_seq,order='integer')),ai_context=ms.ai_context(business_definition='Owned sequential fixture order.'))\n"
    code += "open_state=ms.lifecycle_state(name='open',initial=True)\ndone_state=ms.lifecycle_state(name='done')\n"
    code += "model=ms.state_model(name='model',subject=subjects,states=(open_state,done_state),transitions=(ms.inception(on=started),ms.transition(from_state=open_state,on=finished,to_state=done_state)),business_order=order,ai_context=ms.ai_context(business_definition='Repeated events retain post-inception transition diagnostics.'))\n"
    (models / "semantic" / "cost" / "objects.py").write_text(code)
    ms.load(workspace_dir=root)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _integer(value: object) -> int:
    if type(value) is not int:
        raise ValueError("Expected an exact fixture integer")
    assert isinstance(value, int)
    return value


def _association(x: list[int], y: list[int], method: str) -> float:
    if len(x) != len(y) or len(x) < 2:
        return math.nan
    pairs = Counter(zip(x, y, strict=True))
    if method == "kendall":
        score = 0
        unique = list(pairs)
        for i, (a, b) in enumerate(unique):
            for c, d in unique[i + 1 :]:
                score += ((a > c) - (a < c)) * ((b > d) - (b < d)) * pairs[a, b] * pairs[c, d]
        total = len(x) * (len(x) - 1) // 2
        tx = sum(count * (count - 1) // 2 for count in Counter(x).values())
        ty = sum(count * (count - 1) // 2 for count in Counter(y).values())
        denominator = math.sqrt((total - tx) * (total - ty))
        return score / denominator if denominator else math.nan

    def ranked(values: list[int]) -> list[Fraction]:
        counts = Counter(values)
        seen = 0
        ranks: dict[int, Fraction] = {}
        for value, count in sorted(counts.items()):
            ranks[value] = Fraction(2 * seen + count + 1, 2)
            seen += count
        return [ranks[value] for value in values]

    xx = ranked(x) if method == "spearman" else [Fraction(value) for value in x]
    yy = ranked(y) if method == "spearman" else [Fraction(value) for value in y]
    mx, my = sum(xx) / len(xx), sum(yy) / len(yy)
    numerator = sum((a - mx) * (b - my) for a, b in zip(xx, yy, strict=True))
    denominator = math.sqrt(float(sum((a - mx) ** 2 for a in xx) * sum((b - my) ** 2 for b in yy)))
    return float(numerator) / denominator if denominator else math.nan


def _keyed_rows(
    table: pa.Table, keys: tuple[str, ...], columns: tuple[str, ...]
) -> dict[tuple[object, ...], tuple[object, ...]]:
    result: dict[tuple[object, ...], tuple[object, ...]] = {}
    for row in table.to_pylist():
        key = tuple(row[name] for name in keys)
        assert key not in result, ("duplicate independent oracle key", key)
        result[key] = tuple(row[name] for name in columns)
    return result


def _keyed_equal(
    actual: Mapping[tuple[object, ...], tuple[object, ...]],
    expected: Mapping[tuple[object, ...], tuple[object, ...]],
) -> str:
    assert set(actual) == set(expected), ("original key ownership", set(actual) ^ set(expected))
    for key, wanted in expected.items():
        observed = actual[key]
        assert len(observed) == len(wanted)
        for value, target in zip(observed, wanted, strict=True):
            if isinstance(value, float) and isinstance(target, (int, float)):
                assert math.isclose(value, target, rel_tol=1e-12, abs_tol=1e-12), (
                    key,
                    observed,
                    wanted,
                )
            else:
                assert value == target, (key, observed, wanted)
    return _digest([(list(key), list(expected[key])) for key in sorted(expected, key=repr)])


def _keyed_parts(result: Result) -> dict[str, pa.Table]:
    assert result._dataset is not None
    return {part.role: part.table for part in result._dataset.verified().parts}


def _keyed_payload(parts: Mapping[str, pa.Table], role: str) -> str:
    table = parts[role]
    assert table.num_rows == 1
    value: object = table[role + "__retained"][0].as_py()
    assert isinstance(value, str)
    return value


def _keyed_fact(row: Mapping[str, object]) -> tuple[str, int, int]:
    tenant = row["tenant"]
    assert isinstance(tenant, str)
    return tenant, _integer(row["id"]), _integer(row["revision"])


def _keyed_instant(row: Mapping[str, object]) -> datetime:
    instant = row["happened"]
    assert isinstance(instant, datetime)
    return instant


def _keyed_facts(
    rows: Sequence[Mapping[str, object]],
    *,
    average: bool = False,
    window: tuple[datetime, datetime] | None = None,
) -> dict[tuple[object, ...], tuple[object, ...]]:
    result: dict[tuple[object, ...], tuple[object, ...]] = {}
    for row in rows:
        amount = row["amount"]
        included = window is None or window[0] <= _keyed_instant(row) < window[1]
        cell: tuple[object, ...]
        if average and amount is None:
            cell = (None, "null", "empty_contribution")
        else:
            cell = (_integer(amount) if included and amount is not None else 0, "defined", None)
        result[_keyed_fact(row)] = cell
    return result


def _keyed_grid(
    domain: DomainSignature, *, periods: int, start: datetime = START
) -> dict[str, datetime]:
    grid = domain.time_grid
    assert grid is not None and len(grid.cells) == periods
    expected = [start + timedelta(days=index) for index in range(periods)]
    assert [cell.start for cell in grid.cells] == expected
    assert all(
        cell.original_start == cell.start
        and cell.original_end == cell.end == cell.start + timedelta(days=1)
        and not cell.partial
        for cell in grid.cells
    )
    return {cell.identity: cell.start for cell in grid.cells}


def _keyed_time_rows(
    table: pa.Table,
    keys: tuple[str, ...],
    domain: DomainSignature,
    columns: tuple[str, ...],
    *,
    periods: int,
    start: datetime = START,
) -> dict[tuple[object, ...], tuple[object, ...]]:
    grid = _keyed_grid(domain, periods=periods, start=start)
    position = [index for index, item in enumerate(domain.instance_key) if item.role == "anchor"]
    assert len(position) == 1 and len(keys) == len(domain.instance_key)
    result: dict[tuple[object, ...], tuple[object, ...]] = {}
    for key, value in _keyed_rows(table, keys, columns).items():
        identity = key[position[0]]
        assert isinstance(identity, str) and identity in grid
        normalized = (*key[: position[0]], grid[identity], *key[position[0] + 1 :])
        assert normalized not in result
        result[normalized] = value
    return result


def _keyed_daily(
    rows: Sequence[Mapping[str, object]],
    column: str,
    *,
    periods: int = 16,
    subjects: bool = False,
    gap: int | None = None,
) -> dict[tuple[object, ...], tuple[object, ...]]:
    totals: dict[tuple[object, ...], int] = {}
    for day in range(periods):
        for owner in range(GROUPS) if subjects else (0,):
            key = (
                ("a", owner, START + timedelta(days=day))
                if subjects
                else (START + timedelta(days=day),)
            )
            totals[key] = 0
    for row in rows:
        day = (_keyed_instant(row) - START).days
        assert 0 <= day < periods
        key = (
            ("a", _integer(row["owner"]), START + timedelta(days=day))
            if subjects
            else (START + timedelta(days=day),)
        )
        value = row[column]
        if value is not None:
            totals[key] += _integer(value)
    return {
        key: (None, "unknown", "insufficient_business_coverage")
        if gap is not None and key[-1] == START + timedelta(days=gap)
        else (value, "defined", None)
        for key, value in totals.items()
    }


def _keyed_input(
    value: _statistics.Input,
    rows: Sequence[Mapping[str, object]],
    column: str,
) -> str:
    from marivo.analysis.materialization import deviation_execution as _deviation

    actual = _keyed_time_rows(
        _deviation.load(value.primary),
        value.keys,
        value.signature.domain,
        ("value", "cell_tag", "cell_reason"),
        periods=16,
    )
    result = _keyed_equal(actual, _keyed_daily(rows, column))
    coverage = next(part for part in value.parts if part.role == "coverage")
    _keyed_equal(
        _keyed_time_rows(
            _deviation.load(coverage.table),
            value.keys,
            value.signature.domain,
            ("coverage__complete",),
            periods=16,
        ),
        dict.fromkeys(actual, (True,)),
    )
    counts = dict.fromkeys(actual, 0)
    for row in rows:
        if row[column] is not None:
            counts[(START + timedelta(days=(_keyed_instant(row) - START).days),)] += 1
    original = next(part for part in value.parts if part.role == "original_state")
    _keyed_equal(
        _keyed_time_rows(
            _deviation.load(original.table),
            value.keys,
            value.signature.domain,
            ("original_state__sum", "original_state__non_null_count"),
            periods=16,
        ),
        {key: (cell[0], counts[key]) for key, cell in actual.items()},
    )
    return result


def _keyed_association(
    result: mv.MaterializedAssociationResult,
    rows: Sequence[Mapping[str, object]],
    method: str,
    lags: range,
) -> str:
    from marivo.analysis.materialization import deviation_execution as _deviation
    from marivo.analysis.materialization import statistical_execution as _statistics

    parts = _keyed_parts(result)
    capture = _statistics.PAIRS.validate_json(_keyed_payload(parts, "pair_inputs"))
    state = _statistics.ASSOCIATION.validate_json(_keyed_payload(parts, "association_state"))
    assert capture.declaration.method == method and capture.declaration.lags == tuple(lags)
    assert len(capture.inputs) == 2
    inputs = [
        _keyed_input(value, rows, column)
        for value, column in zip(capture.inputs, ("amount", "y"), strict=True)
    ]
    vectors = [
        [cell[0] for cell in _keyed_daily(rows, column).values()] for column in ("amount", "y")
    ]
    x, y = ([_integer(value) for value in vector] for vector in vectors)
    expected: dict[tuple[object, ...], tuple[object, ...]] = {}
    assert {candidate.lag for candidate in state.candidates} == set(lags)
    assert len(state.candidates) == len(lags)
    for candidate in state.candidates:
        lag = candidate.lag
        xx = x[max(0, -lag) : min(len(x), len(x) - lag)]
        yy = y[max(0, lag) : min(len(y), len(y) + lag)]
        status = (
            "insufficient_pairs"
            if len(xx) < 2
            else "constant_both"
            if len(set(xx)) == len(set(yy)) == 1
            else "constant_a"
            if len(set(xx)) == 1
            else "constant_b"
            if len(set(yy)) == 1
            else "valid"
        )
        coefficient = _association(xx, yy, method) if status == "valid" else None
        expected[(lag,)] = (
            coefficient,
            "defined" if status == "valid" else "undefined",
            None if status == "valid" else status,
            0,
            1,
            16,
            16 - abs(lag),
            abs(lag),
            0,
            16 - abs(lag),
            status,
            candidate.selected,
        )
    # Verified retained state certifies the exact K/tie law; this oracle binds its inputs to facts.
    assert sum(candidate.selected for candidate in state.candidates) == 1
    output = _deviation.load(state.views)
    witness = _keyed_equal(
        _keyed_rows(
            output,
            ("lag",),
            (
                "value",
                "cell_tag",
                "cell_reason",
                "pair_a",
                "pair_b",
                "input_count",
                "matched_count",
                "boundary_drop_count",
                "null_pair_count",
                "complete_pair_count",
                "status",
                "selected",
            ),
        ),
        expected,
    )
    return _digest([*inputs, witness])


def _keyed_forecast(
    result: mv.MaterializedForecastResult,
    rows: Sequence[Mapping[str, object]],
    model: str,
) -> str:
    from marivo.analysis.materialization import deviation_execution as _deviation
    from marivo.analysis.materialization import statistical_execution as _statistics

    parts = _keyed_parts(result)
    capture = _statistics.TRAINING.validate_json(_keyed_payload(parts, "training_inputs"))
    state = _statistics.FORECAST.validate_json(_keyed_payload(parts, "forecast_state"))
    assert capture.declaration.model == model
    assert capture.declaration.season == (4 if model == "seasonal_naive" else None)
    witness = _keyed_input(capture.input, rows, "amount")
    future = capture.future.grid
    assert len(future.cells) == 4
    assert [cell.start for cell in future.cells] == [
        START + timedelta(days=16 + h) for h in range(4)
    ]
    assert all(
        cell.end == cell.start + timedelta(days=1) and not cell.partial for cell in future.cells
    )
    training = [_integer(cell[0]) for cell in _keyed_daily(rows, "amount").values()]
    prediction = (
        [training[-1]] * 4
        if model == "naive"
        else [
            float(Fraction(training[-1]) + Fraction(training[-1] - training[0], 15) * h)
            for h in range(1, 5)
        ]
        if model == "drift"
        else training[-4:]
    )
    expected: dict[tuple[object, ...], tuple[object, ...]] = {
        (cell.identity,): (value, h)
        for h, (cell, value) in enumerate(zip(future.cells, prediction, strict=True), 1)
    }
    actual = _keyed_rows(
        _deviation.load(state.views), capture.input.keys, ("prediction", "horizon")
    )
    return _digest([witness, _keyed_equal(actual, expected)])


def _keyed_deviation(
    result: mv.MaterializedDeviationResult,
    rows: Sequence[Mapping[str, object]],
    method: str,
) -> str:
    from marivo.analysis.materialization import deviation_execution as _deviation

    parts = _keyed_parts(result)
    capture = _deviation.INPUTS.validate_json(_keyed_payload(parts, "fit_inputs"))
    state = _deviation.STATE.validate_json(_keyed_payload(parts, "fit_state"))
    expected = _keyed_facts(rows)
    witness = _keyed_equal(
        _keyed_rows(
            _deviation.load(capture.primary), capture.keys, ("value", "cell_tag", "cell_reason")
        ),
        expected,
    )
    original_state = next(part for part in capture.parts if part.role == "original_state")
    _keyed_equal(
        _keyed_rows(
            _deviation.load(original_state.table),
            capture.keys,
            ("original_state__sum", "original_state__non_null_count"),
        ),
        {
            _keyed_fact(row): (
                0 if row["amount"] is None else _integer(row["amount"]),
                int(row["amount"] is not None),
            )
            for row in rows
        },
    )
    numbers = [_integer(cell[0]) for cell in expected.values()]
    if method == "zscore":
        center = Fraction(sum(numbers), len(numbers))
        scale = math.sqrt(
            float(sum((Fraction(value) - center) ** 2 for value in numbers) / len(numbers))
        )
    else:
        ordered = sorted(numbers)
        center = Fraction(ordered[(len(ordered) - 1) // 2] + ordered[len(ordered) // 2], 2)
        residuals = sorted(abs(Fraction(value) - center) for value in numbers)
        raw = (residuals[(len(residuals) - 1) // 2] + residuals[len(residuals) // 2]) / 2
        scale = float(raw * Fraction(7413, 5000) if raw else sum(residuals) / len(residuals))
    assert scale > 0
    views = _deviation.load(state.views)
    expected_views = {
        key: (
            cell[0],
            float(center),
            float(Fraction(_integer(cell[0])) - center) / scale,
            "defined",
            None,
        )
        for key, cell in expected.items()
    }
    return _digest(
        [
            witness,
            _keyed_equal(
                _keyed_rows(
                    views,
                    capture.keys,
                    (
                        "observed__value",
                        "reference__value",
                        "score__value",
                        "score__cell_tag",
                        "score__cell_reason",
                    ),
                ),
                expected_views,
            ),
        ]
    )


def _keyed_runs(
    result: mv.MaterializedTimeRunResult,
    rows: Sequence[Mapping[str, object]],
    cross: bool,
) -> str:
    from marivo.analysis.materialization import deviation_execution as _deviation
    from marivo.analysis.materialization import runs_execution as _runs

    parts = _keyed_parts(result)
    capture = _runs.CAPTURE.validate_json(_keyed_payload(parts, "condition_cells"))
    state = _runs.RUNS.validate_json(_keyed_payload(parts, "run_cells"))
    periods, gap = (CROSS_PERIODS, CROSS_GAP) if cross else (16, None)
    grid = _keyed_grid(capture.signature.domain, periods=periods)
    assert len(capture.inputs) == 1
    original = _deviation.load(capture.inputs[0])
    wanted = _keyed_daily(rows, "amount", periods=periods, subjects=cross, gap=gap)
    actual = _keyed_time_rows(
        original,
        capture.keys,
        capture.signature.domain,
        ("value", "cell_tag", "cell_reason"),
        periods=periods,
    )
    witness = _keyed_equal(actual, wanted)
    keys = list(_keyed_rows(original, capture.keys, ()).keys())
    normalized = list(actual)
    classes = [
        "unavailable"
        if wanted[key][1] == "unknown"
        else "true"
        if _integer(wanted[key][0]) >= (0 if cross else 1)
        else "false"
        for key in normalized
    ]
    assert state.classifications == tuple(classes)
    assert state.reasons == tuple(
        ("insufficient_business_coverage",) if value == "unavailable" else () for value in classes
    )
    if cross:
        assert capture.subject is not None
        _keyed_equal(
            _keyed_time_rows(
                _deviation.load(capture.subject),
                capture.keys,
                capture.signature.domain,
                ("subject__key_0", "subject__key_1"),
                periods=periods,
            ),
            {key: key[:2] for key in wanted},
        )
    ranges = ((0, CROSS_GAP), (CROSS_GAP + 1, CROSS_PERIODS)) if cross else ((0, 16),)
    expected: dict[tuple[object, ...], tuple[object, ...]] = {}
    for owner in range(GROUPS) if cross else (0,):
        for start, end in ranges:
            prefix: tuple[object, ...] = ("a", owner) if cross else ()
            expected[(*prefix, START + timedelta(days=start))] = (
                START + timedelta(days=end),
                end - start,
                timedelta(days=end - start),
                "scope_boundary" if start == 0 else "unavailable",
                "scope_boundary" if end == periods else "unavailable",
                None if start == 0 else START + timedelta(days=start - 1),
                None if end == periods else START + timedelta(days=end),
            )
    observed: dict[tuple[object, ...], tuple[object, ...]] = {}
    for row in _deviation.load(state.views).to_pylist():
        indices: object = row["input_rows"]
        assert isinstance(indices, list) and indices
        positions = [_integer(index) for index in indices]
        images = [normalized[index] for index in positions]
        prefix = images[0][:-1]
        assert all(image[:-1] == prefix for image in images)
        instant = row["start"]
        assert isinstance(instant, datetime)
        key = (*prefix, instant)
        assert key not in observed
        expected_days = [image[-1] for image in images]
        assert expected_days == [instant + timedelta(days=day) for day in range(len(images))]
        assert row["cells"] == [keys[index][-1] for index in positions]
        left, right = row["left_cell"], row["right_cell"]
        observed[key] = (
            row["end"],
            row["count"],
            row["duration"],
            row["left_kind"],
            row["right_kind"],
            grid[left] if isinstance(left, str) else None,
            grid[right] if isinstance(right, str) else None,
        )
    return _digest([witness, _keyed_equal(observed, expected)])


def _keyed_events(
    outputs: Sequence[Result], rows: Sequence[Mapping[str, object]], result: Result
) -> str:
    from marivo.analysis.materialization import history_execution as _history
    from marivo.analysis.materialization import journey_execution as _journey
    from marivo.analysis.methods.history import (
        Evaluation,
        History,
        Interval,
        Occurrence,
        Transition,
    )
    from marivo.analysis.methods.journey_matching import JourneyAssignment, OrderedOccurrence

    journey, history, anchors = outputs[:3]
    assert isinstance(journey, mv.MaterializedJourneyResult)
    assert isinstance(history, mv.MaterializedHistoryResult)
    assert isinstance(anchors, mv.MaterializedAnchorDomain)
    ordered = sorted(
        rows, key=lambda row: (_integer(row["owner"]), _keyed_instant(row), _integer(row["seq"]))
    )
    ordinals = {_keyed_fact(row): ordinal for ordinal, row in enumerate(ordered)}
    assignments: dict[tuple[object, ...], JourneyAssignment] = {}
    expected: dict[tuple[object, ...], tuple[object, ...]] = {}
    uses: dict[tuple[object, ...], tuple[object, ...]] = {}
    for index in range(0, len(rows), 2):
        start = rows[index]
        subject = ("a", _integer(start["owner"]))
        occurrence = OrderedOccurrence(
            "cost.started",
            _keyed_fact(start),
            subject,
            _keyed_instant(start),
            ordinals[_keyed_fact(start)],
        )
        finish = rows[index + 1] if index + 1 < len(rows) else None
        completed = (
            None
            if finish is None
            else OrderedOccurrence(
                "cost.finished",
                _keyed_fact(finish),
                subject,
                _keyed_instant(finish),
                ordinals[_keyed_fact(finish)],
            )
        )
        key = (*subject, "cost.started", *_keyed_fact(start))
        assignments[key] = JourneyAssignment(
            subject,
            occurrence,
            (occurrence, completed),
            ("reached", "unreachable" if completed is None else "reached"),
        )
        expected[key] = (0 if finish is None else _integer(finish["y"]), "defined", None)
        uses[key] = () if finish is None else (_keyed_fact(finish),)
    for output, role, column in (
        (journey, "journey", "journey__assignment"),
        (anchors, "anchor", "anchor__assignment"),
    ):
        assert output._dataset is not None
        checked = output._dataset.verified()
        actual = {}
        for row in _keyed_parts(output)[role].to_pylist():
            key = tuple(row[field] for field in checked.contract.key_fields)
            assert key not in actual
            actual[key] = _journey.ASSIGNMENT.validate_json(row[column], strict=True)
            if role == "anchor":
                assert row["anchor__started_at"] == assignments[key].start.instant
        assert actual == assignments
        _keyed_equal(
            _keyed_rows(
                _keyed_parts(output)["subject"],
                checked.contract.key_fields,
                ("subject__key_0", "subject__key_1"),
            ),
            {key: key[:2] for key in assignments},
        )
    end = START + timedelta(seconds=len(rows) + 1)
    records = {}
    for row in _keyed_parts(history)["history"].to_pylist():
        record = _history.HISTORY.validate_json(row["history__record"], strict=True)
        assert record.subject not in records
        records[record.subject] = record
    histories = {}
    for owner in range(GROUPS):
        facts = sorted((row for row in rows if row["owner"] == owner), key=_keyed_instant)
        occurrences = tuple(
            Occurrence(
                "cost." + str(row["kind"]),
                _keyed_fact(row),
                _keyed_instant(row),
                _integer(row["seq"]),
            )
            for row in facts
        )
        evaluations = tuple(
            Evaluation(
                occurrence,
                None if ordinal == 0 else "open" if ordinal == 1 else "done",
                "open" if ordinal == 0 else "done",
                "inception"
                if ordinal == 0
                else "legal_transition"
                if ordinal == 1
                else "illegal_transition",
            )
            for ordinal, occurrence in enumerate(occurrences)
        )
        intervals: list[Interval] = []
        if occurrences:
            first = occurrences[0]
            exit_occurrence = occurrences[1] if len(occurrences) >= 2 else None
            boundary = exit_occurrence.occurred_at if exit_occurrence is not None else end
            intervals.append(
                Interval(
                    1,
                    "open",
                    first,
                    exit_occurrence,
                    first.occurred_at,
                    boundary,
                    "completed" if exit_occurrence is not None else "right_censored",
                    False,
                    (boundary - first.occurred_at) // timedelta(microseconds=1),
                )
            )
            if exit_occurrence is not None:
                intervals.append(
                    Interval(
                        2,
                        "done",
                        exit_occurrence,
                        None,
                        exit_occurrence.occurred_at,
                        end,
                        "right_censored",
                        False,
                        (end - exit_occurrence.occurred_at) // timedelta(microseconds=1),
                    )
                )
        histories[("a", owner)] = History(
            ("a", owner),
            "seeded" if occurrences else "not_started",
            occurrences[0] if occurrences else None,
            end,
            evaluations,
            (Transition(1, occurrences[1], "open", "done"),) if len(occurrences) >= 2 else (),
            evaluations[2:],
            (),
            tuple(intervals),
        )
    assert records == histories
    assert result._dataset is not None
    checked = result._dataset.verified()
    witness = _keyed_equal(
        _keyed_rows(
            checked.primary, checked.contract.key_fields, ("value", "cell_tag", "cell_reason")
        ),
        expected,
    )
    parts = _keyed_parts(result)
    state_expected = {key: (cell[0], int(bool(uses[key]))) for key, cell in expected.items()}
    _keyed_equal(
        _keyed_rows(
            parts["original_state"],
            checked.contract.key_fields,
            ("original_state__sum", "original_state__non_null_count"),
        ),
        state_expected,
    )
    actual_uses = {}
    for row in parts["anchor"].to_pylist():
        key = tuple(row[field] for field in checked.contract.key_fields)
        assert row["anchor__deadline"] == assignments[key].start.instant + timedelta(seconds=2)
        actual_uses[key] = tuple(
            tuple(use["anchor_key_" + str(index)] for index in range(3))
            for use in row["anchor__uses_0"]
        )
    assert actual_uses == uses
    return witness


def _keyed_oracle(work: Workload, result: Result) -> str:
    assert work.scenario != "baseline"
    assert result._dataset is not None
    checked = result._dataset.verified()
    assert checked.primary is not None
    parts = _keyed_parts(result)
    source = any(output is result for output in work.outputs)
    outputs = work.outputs if source else work.fixed_outputs or [result]
    witnesses: list[str] = []
    if isinstance(result, mv.MaterializedRankingResult):
        rank_cells = _keyed_facts(work.rows, average=work.scenario == "ties-null")
        witnesses.append(
            _keyed_equal(
                _keyed_rows(
                    parts["values"],
                    checked.contract.key_fields,
                    ("values__value", "values__cell_tag", "values__cell_reason"),
                ),
                rank_cells,
            )
        )
        _keyed_equal(
            _keyed_rows(
                parts["original_state"],
                checked.contract.key_fields,
                ("original_state__sum", "original_state__non_null_count"),
            ),
            {
                _keyed_fact(row): (
                    0 if row["amount"] is None else _integer(row["amount"]),
                    int(row["amount"] is not None),
                )
                for row in work.rows
            },
        )
        defined = [_integer(row["amount"]) for row in work.rows if row["amount"] is not None]
        ranks = {value: index + 1 for index, value in enumerate(sorted(set(defined), reverse=True))}
        expected = {
            key: (None, *cell[1:])
            if cell[0] is None
            else (ranks[_integer(cell[0])], "defined", None)
            for key, cell in rank_cells.items()
        }
        witnesses.append(
            _keyed_equal(
                _keyed_rows(
                    parts["ranks"],
                    checked.contract.key_fields,
                    ("ranks__value", "ranks__cell_tag", "ranks__cell_reason"),
                ),
                expected,
            )
        )
    elif isinstance(result, mv.MaterializedForecastResult):
        for output, model in zip(outputs, ("naive", "drift", "seasonal_naive"), strict=True):
            assert isinstance(output, mv.MaterializedForecastResult)
            witnesses.append(_keyed_forecast(output, work.rows, model))
    elif isinstance(result, mv.MaterializedAssociationResult):
        for output, method in zip(outputs, ("pearson", "spearman", "kendall"), strict=True):
            assert isinstance(output, mv.MaterializedAssociationResult)
            witnesses.append(_keyed_association(output, work.rows, method, range(-2, 3)))
    elif isinstance(result, mv.MaterializedTimeRunResult):
        witnesses.append(
            _keyed_runs(result, work.rows, work.scenario == "cross-batch-long-runs-unavailable")
        )
        if work.scenario == "deviation-runs":
            for output, method in zip(outputs[:2], ("zscore", "mad"), strict=True):
                assert isinstance(output, mv.MaterializedDeviationResult)
                witnesses.append(_keyed_deviation(output, work.rows, method))
    elif work.scenario in ("event-lifecycle-anchor", "many-occurrences-anchors-lags"):
        witnesses.append(_keyed_events(outputs, work.rows, result))
        if work.scenario == "many-occurrences-anchors-lags":
            association = outputs[3]
            assert isinstance(association, mv.MaterializedAssociationResult)
            witnesses.append(_keyed_association(association, work.rows, "spearman", range(-4, 5)))
    elif work.scenario == "exact-distinct-quantile":
        groups: dict[tuple[object, ...], list[int]] = {("a", owner): [] for owner in range(GROUPS)}
        for row in work.rows:
            if row["amount"] is not None:
                groups[("a", _integer(row["owner"]))].append(_integer(row["amount"]))
        for output, quantile in zip(outputs, (False, True), strict=True):
            assert output._dataset is not None
            current = output._dataset.verified()
            expected = {}
            for key, values in groups.items():
                ordered = sorted(values)
                expected[key] = (
                    (
                        (
                            (ordered[(len(ordered) - 1) // 2] + ordered[len(ordered) // 2]) / 2,
                            "defined",
                            None,
                        )
                        if ordered
                        else (None, "null", "empty_contribution")
                    )
                    if quantile
                    else (len(set(values)), "defined", None)
                )
            witnesses.append(
                _keyed_equal(
                    _keyed_rows(
                        current.primary,
                        current.contract.key_fields,
                        ("value", "cell_tag", "cell_reason"),
                    ),
                    expected,
                )
            )
    elif work.scenario == "multi-root-ratio-empty-groups":
        groups = {("a", owner): [0, 0, 0, 0] for owner in range(GROUPS)}
        for row in work.rows:
            group = groups[("a", _integer(row["owner"]))]
            if row["amount"] is not None:
                group[0] += _integer(row["amount"])
                group[1] += 1
            group[2] += _integer(row["y"])
            group[3] += 1
        captured = work.outputs[0] if source else work.fixed_inputs[0]
        assert captured._dataset is not None
        ratio = captured._dataset.verified()
        expected = {
            key: (value[0] / value[2], "defined", None)
            if value[2]
            else (None, "undefined", "zero_denominator")
            for key, value in groups.items()
        }
        witnesses.append(
            _keyed_equal(
                _keyed_rows(
                    ratio.primary, ratio.contract.key_fields, ("value", "cell_tag", "cell_reason")
                ),
                expected,
            )
        )
        witnesses.append(
            _keyed_equal(
                _keyed_rows(
                    _keyed_parts(captured)["original_state"],
                    ratio.contract.key_fields,
                    (
                        "original_state__numerator_sum",
                        "original_state__numerator_non_null_count",
                        "original_state__denominator_sum",
                        "original_state__denominator_non_null_count",
                    ),
                ),
                {key: tuple(value) for key, value in groups.items()},
            )
        )
        totals = tuple(sum(value[index] for value in groups.values()) for index in range(4))
        witnesses.append(
            _keyed_equal(
                _keyed_rows(
                    parts["original_state"],
                    (),
                    (
                        "original_state__numerator_sum",
                        "original_state__numerator_non_null_count",
                        "original_state__denominator_sum",
                        "original_state__denominator_non_null_count",
                    ),
                ),
                {(): totals},
            )
        )
    elif isinstance(result, mv.MaterializedAttributionResult):
        axis_totals = {kind: [0, 0] for kind in ("started", "finished")}
        for row in work.rows:
            if row["amount"] is not None:
                axis_totals[str(row["kind"])][0 if _keyed_instant(row) == START else 1] += _integer(
                    row["amount"]
                )
        retained = min(axis_totals, key=lambda kind: (-sum(axis_totals[kind]), kind))
        for output, top in zip(outputs, (False, True), strict=True):
            assert output._dataset is not None
            current = output._dataset.verified()
            expected = {}
            for kind, (now, before) in axis_totals.items():
                key = (
                    2,
                    "a",
                    None if top and kind != retained else kind,
                    2 if top and kind != retained else 0,
                )
                expected[key] = (now, before, now - before)
            witnesses.append(
                _keyed_equal(
                    _keyed_rows(
                        _keyed_parts(output)["allocation"],
                        current.contract.key_fields,
                        ("allocation__current", "allocation__baseline", "allocation__contribution"),
                    ),
                    expected,
                )
            )
    elif work.scenario == "comparison-members-observe":
        current_cells = _keyed_facts(work.rows, window=(START, START + timedelta(days=1)))
        baseline_cells = _keyed_facts(work.rows, window=(START - timedelta(days=1), START))
        full_cells = _keyed_facts(work.rows)
        expected = {
            key: cell
            for key, cell in full_cells.items()
            if _integer(current_cells[key][0]) > _integer(baseline_cells[key][0])
        }
        witnesses.append(
            _keyed_equal(
                _keyed_rows(
                    checked.primary,
                    checked.contract.key_fields,
                    ("value", "cell_tag", "cell_reason"),
                ),
                expected,
            )
        )
        if source:
            difference = work.outputs[0]
            assert difference._dataset is not None
            observed = difference._dataset.verified()
            difference_parts = _keyed_parts(difference)
            for role, endpoint_cells in (
                ("current_endpoint", current_cells),
                ("baseline_endpoint", baseline_cells),
            ):
                witnesses.append(
                    _keyed_equal(
                        _keyed_rows(
                            difference_parts[role],
                            observed.contract.key_fields,
                            tuple(
                                role + "__" + name for name in ("value", "cell_tag", "cell_reason")
                            ),
                        ),
                        endpoint_cells,
                    )
                )
        else:
            for capture, capture_cells in zip(
                work.fixed_inputs, (current_cells, baseline_cells, full_cells), strict=True
            ):
                assert capture._dataset is not None
                observed = capture._dataset.verified()
                witnesses.append(
                    _keyed_equal(
                        _keyed_rows(
                            observed.primary,
                            observed.contract.key_fields,
                            ("value", "cell_tag", "cell_reason"),
                        ),
                        capture_cells,
                    )
                )
    elif work.scenario == "skew-empty-groups":
        skew_totals = {(_integer(row["owner"]),): 0 for row in work.rows}
        skew_totals[(0,)] = sum(
            _integer(row["amount"])
            for row in work.rows
            if row["owner"] == 0 and row["amount"] is not None
        )
        witnesses.append(
            _keyed_equal(
                _keyed_rows(
                    checked.primary,
                    checked.contract.key_fields,
                    ("value", "cell_tag", "cell_reason"),
                ),
                {key: (value, "defined", None) for key, value in skew_totals.items()},
            )
        )
    else:
        raise ValueError("Named scenario lacks independent original-key binding")
    assert witnesses
    return _digest(witnesses)


@dataclass
class Workload:
    session: mv.Session
    scenario: str
    rows: list[dict[str, object]]
    environment: dict[str, object]
    outputs: list[Result] = field(default_factory=list)
    fixed_expected: int | float | None = None
    fixed_inputs: list[Result] = field(default_factory=list)
    source_inputs: dict[str, SourceInput] = field(default_factory=dict)
    fixed_outputs: list[Result] = field(default_factory=list)

    @property
    def routes(self) -> tuple[str, ...]:
        if self.scenario == "baseline":
            return ("ibis", "ibis_python")
        if self.scenario in (
            "multi-root-ratio-empty-groups",
            "exact-distinct-quantile",
            "skew-empty-groups",
            "comparison-members-observe",
        ):
            return ("ibis",)
        return ("ibis_python",)

    def _values(self) -> mv.LogicalNumericRelation:
        value = self.session.members(ms.ref.entity("cost.facts")).observe(
            ms.ref.metric("cost.facts_total"),
            during=mv.time_scope(start="2026-08-01", end="2026-08-02")
            if self.scenario == "baseline"
            else None,
        )
        assert isinstance(value, mv.LogicalNumericRelation)
        return value

    def _daily(
        self, metric: str = "facts_total", population: mv.LogicalAnalysisDomain | None = None
    ) -> mv.LogicalRolledNumericRelation:
        periods = CROSS_PERIODS if self.scenario == "cross-batch-long-runs-unavailable" else 16
        grid = mv.time_grid(
            during=mv.time_scope(
                start=START.isoformat(), end=(START + timedelta(days=periods)).isoformat()
            ),
            grain=mv.grain("day"),
        )
        if population is None:
            population = self.session.members(ms.ref.entity("cost.subjects"))
        values = population.each(grid).observe(
            ms.ref.metric("cost." + metric),
            during=grid.window,
            via=ms.ref.relationship("cost.facts_subject"),
        )
        assert isinstance(values, mv.LogicalNumericRelation)
        rolled = values.group_by(grid).rollup()
        assert isinstance(rolled, mv.LogicalRolledNumericRelation)
        return rolled

    def source(self, route: str) -> Result:
        """Execute current public expressions; requested routes never alter registration."""
        if route not in self.routes:
            raise ValueError("Route is outside this workload's declared comparison")
        self.outputs.clear()
        self.fixed_expected = None
        self.fixed_inputs.clear()
        self.fixed_outputs.clear()
        self.source_inputs.clear()
        values = self._values()
        self.source_inputs["values"] = values
        scenario = self.scenario
        result: Result
        daily: mv.LogicalNumericRelation | mv.LogicalRolledNumericRelation | mv.LogicalRatioRelation
        if scenario == "baseline":
            if route == "ibis":
                result = values.rollup().execute()
            elif self.environment["backend"] == "duckdb":
                result = values.deviation(method="zscore").observed.rollup().execute()
            else:
                prepared = self.session.members(ms.ref.entity("cost.facts")).observe(
                    ms.ref.metric("cost.facts_total"),
                    during=mv.time_scope(start="2026-08-01", end="2026-08-02"),
                    coordinates=(ms.ref.dimension("cost.facts.kind"),),
                )
                assert isinstance(prepared, mv.LogicalNumericRelation)
                result = prepared.rollup().execute()
        elif scenario in ("ties-null", "high-cardinality"):
            if scenario == "ties-null":
                average = self.session.members(ms.ref.entity("cost.facts")).observe(
                    ms.ref.metric("cost.average")
                )
                assert isinstance(average, mv.LogicalNumericRelation)
                values = average
                self.source_inputs["values"] = values
            result = values.rank(
                order="descending", ties="dense" if scenario == "ties-null" else "ordinal"
            ).execute()
        elif scenario == "skew-empty-groups":
            members = self.session.members(ms.ref.entity("cost.facts"))
            owner = members.read(ms.ref.dimension("cost.facts.owner"))
            assert isinstance(owner, mv.LogicalCategoryRelation)
            selected = owner.where(owner.value.eq(0)).members()
            assert isinstance(selected, mv.LogicalAnalysisDomain)
            amounts = selected.observe(ms.ref.metric("cost.facts_total"))
            categories = selected.read(ms.ref.dimension("cost.facts.owner"))
            assert isinstance(amounts, mv.LogicalNumericRelation) and isinstance(
                categories, mv.LogicalCategoryRelation
            )
            (
                self.source_inputs["amounts"],
                self.source_inputs["categories"],
                self.source_inputs["groups"],
            ) = amounts, categories, owner
            result = (
                amounts.group_by(categories, groups=owner.group_by()).summarize(mv.sum()).execute()
            )
        elif scenario == "comparison-members-observe":
            members = self.session.members(ms.ref.entity("cost.facts"))
            current = members.observe(
                ms.ref.metric("cost.facts_total"),
                during=mv.time_scope(start="2026-08-01", end="2026-08-02"),
            )
            baseline = members.observe(
                ms.ref.metric("cost.facts_total"),
                during=mv.time_scope(start="2026-07-31", end="2026-08-01"),
            )
            assert isinstance(current, mv.LogicalNumericRelation) and isinstance(
                baseline, mv.LogicalNumericRelation
            )
            difference = current.compare(baseline)
            self.source_inputs["current"], self.source_inputs["baseline"] = current, baseline
            full_values = members.observe(ms.ref.metric("cost.facts_total"))
            assert isinstance(full_values, mv.LogicalNumericRelation)
            self.source_inputs["full_values"] = full_values
            self.outputs.append(difference.execute())
            selected = difference.where(difference.value.gt(0)).members()
            assert isinstance(selected, mv.LogicalAnalysisDomain)
            result = selected.observe(ms.ref.metric("cost.facts_total")).execute()
        elif scenario == "joint-topk-attribution":
            members = self.session.members(ms.ref.entity("cost.facts"))
            axes = (ms.ref.dimension("cost.facts.tenant"), ms.ref.dimension("cost.facts.kind"))
            current_attribution = members.observe(
                ms.ref.metric("cost.facts_total"),
                during=mv.time_scope(start="2026-08-01", end="2026-08-02"),
                coordinates=axes,
            ).rollup()
            baseline_attribution = members.observe(
                ms.ref.metric("cost.facts_total"),
                during=mv.time_scope(start="2026-07-31", end="2026-08-01"),
                coordinates=axes,
            ).rollup()
            change = current_attribution.compare(baseline_attribution)
            self.source_inputs["change"] = change
            self.outputs.append(change.attribute(axes=axes).execute())
            result = change.attribute(axes=axes, top_k=1).execute()
        elif scenario == "multi-root-ratio-empty-groups":
            members = self.session.members(ms.ref.entity("cost.subjects"))
            ratio_metric = mv.runtime_metric.ratio(
                ms.ref.metric("cost.facts_total"),
                ms.ref.metric("cost.other_total"),
                label="independent ratio",
            )
            routes = mv.routes(
                mv.route(
                    ms.ref.entity("cost.facts"),
                    through=(ms.ref.relationship("cost.facts_subject"),),
                ),
                mv.route(
                    ms.ref.entity("cost.other"),
                    through=(ms.ref.relationship("cost.other_subject"),),
                ),
            )
            ratio = members.observe(ratio_metric, via=routes)
            assert isinstance(ratio, mv.LogicalRatioRelation)
            self.outputs.append(ratio.execute())
            self.source_inputs["ratio"] = ratio
            result = ratio.rollup().execute()
        elif scenario == "exact-distinct-quantile":
            members = self.session.members(ms.ref.entity("cost.subjects"))
            window = mv.time_scope(start="2026-08-01", end="2026-08-02")
            for metric in ("distinct", "quantile"):
                result = members.observe(
                    ms.ref.metric("cost." + metric),
                    during=window,
                    via=ms.ref.relationship("cost.facts_subject"),
                ).execute()
                self.outputs.append(result)
            return result
        elif scenario == "association-lags":
            population = self.session.members(ms.ref.entity("cost.subjects"))
            daily, other = self._daily(population=population), self._daily("facts_ysum", population)
            self.source_inputs["daily"], self.source_inputs["other"] = daily, other
            for method in ("pearson", "spearman", "kendall"):
                result = daily.correlate(other, method=method, lag_range=range(-2, 3)).execute()
                self.outputs.append(result)
            return result
        elif scenario in ("forecast-models", "full-training-numeric-extremes"):
            daily = self._daily()
            self.source_inputs["daily"] = daily
            for model in (mv.naive(), mv.drift(), mv.seasonal_naive(periods=4)):
                result = daily.forecast(horizon=mv.periods(4), model=model).execute()
                self.outputs.append(result)
            return result
        elif scenario == "deviation-runs":
            deviation_methods: tuple[Literal["zscore", "mad"], ...] = ("zscore", "mad")
            for method in deviation_methods:
                self.outputs.append(values.deviation(method=method).execute())
            daily = self._daily()
            self.source_inputs["daily"] = daily
            result = daily.runs(where=daily.value.gt(0)).execute()
        elif scenario == "cross-batch-long-runs-unavailable":
            grid = mv.time_grid(
                during=mv.time_scope(start=START, end=START + timedelta(days=CROSS_PERIODS)),
                grain=mv.grain("day"),
            )
            daily = (
                self.session.members(ms.ref.entity("cost.subjects"))
                .each(grid)
                .observe(
                    ms.ref.metric("cost.facts_total"),
                    during=grid.window,
                    via=ms.ref.relationship("cost.facts_subject"),
                    complete_during=(
                        mv.time_scope(start=START, end=START + timedelta(days=CROSS_GAP)),
                        mv.time_scope(
                            start=START + timedelta(days=CROSS_GAP + 1),
                            end=START + timedelta(days=CROSS_PERIODS),
                        ),
                    ),
                )
            )
            assert isinstance(daily, mv.LogicalNumericRelation)
            self.source_inputs["daily"] = daily
            self.outputs.append(daily.execute())
            result = daily.runs(where=daily.value.gte(0)).execute()
        elif scenario in ("event-lifecycle-anchor", "many-occurrences-anchors-lags"):
            end = START + timedelta(seconds=len(self.rows) + 1)
            window = mv.time_scope(start=START, end=end)
            members = self.session.members(ms.ref.entity("cost.subjects"))
            started = ms.participant_role(event=ms.ref.event("cost.started"), name="subject")
            finished = ms.participant_role(event=ms.ref.event("cost.finished"), name="subject")
            claims = (
                mv.SourceOriginCompletenessDeclarationV1(
                    inputs=(started.event, finished.event),
                    source_origin_ref=ms.ref.datasource("warehouse"),
                    complete_through=end,
                    rationale="Complete owned fixture event source.",
                ),
            )
            pattern = mv.EventPattern(
                steps=(
                    mv.step(participant=started, key="start"),
                    mv.step(participant=finished, key="finish"),
                )
            )
            journey = self.session.events.match(
                pattern,
                population=members,
                cohort_window=window,
                completion_through=end,
                matching=mv.every_start(completion_assignment="exclusive"),
                business_order=ms.ref.business_order("cost.order"),
                completeness=claims,
            )
            self.outputs.append(journey.execute())
            self.outputs.append(
                self.session.lifecycle.replay(
                    ms.ref.state_model("cost.model"),
                    population=members,
                    window=window,
                    seed=mv.from_inception(),
                    completeness=claims,
                ).execute()
            )
            anchors = self.session.anchors(journey, population=members, during=window)
            self.outputs.append(anchors.execute())
            result = anchors.observe(
                ms.ref.metric("cost.facts_ysum"),
                within=mv.elapsed(mv.duration(seconds=2)),
                via=ms.ref.relationship("cost.facts_subject"),
            ).execute()
            if scenario == "many-occurrences-anchors-lags":
                grid = mv.time_grid(
                    during=mv.time_scope(start=START, end=START + timedelta(days=16)),
                    grain=mv.grain("day"),
                )
                left = (
                    members.each(grid)
                    .observe(
                        ms.ref.metric("cost.facts_total"),
                        during=grid.window,
                        via=ms.ref.relationship("cost.facts_subject"),
                    )
                    .group_by(grid)
                    .rollup()
                )
                right = (
                    members.each(grid)
                    .observe(
                        ms.ref.metric("cost.facts_ysum"),
                        during=grid.window,
                        via=ms.ref.relationship("cost.facts_subject"),
                    )
                    .group_by(grid)
                    .rollup()
                )
                self.outputs.append(
                    left.correlate(right, method="spearman", lag_range=range(-4, 5)).execute()
                )
        else:
            raise ValueError("Frozen scenario has no public workload recipe")
        self.outputs.append(result)
        return result

    def fixed(self, producer: Result) -> Logical | FixedCompound:
        if self.scenario == "baseline":
            self.fixed_expected = sum(self._oracle_values(producer))
            assert isinstance(
                producer,
                (
                    mv.MaterializedNumericRelation,
                    mv.MaterializedRolledNumericRelation,
                    mv.MaterializedGroupedNumericRelation,
                ),
            )
            return producer.summarize(mv.sum())
        if self.scenario == "multi-root-ratio-empty-groups":
            captured_ratio = self.source_inputs["ratio"].execute()
            assert isinstance(captured_ratio, mv.MaterializedRatioRelation)
            self.fixed_inputs = [captured_ratio]
            return captured_ratio.rollup()
        if isinstance(producer, mv.MaterializedRankingResult):
            captured = self.source_inputs["values"].execute()
            assert isinstance(captured, mv.MaterializedNumericRelation)
            self.fixed_inputs = [captured]
            return captured.rank(
                order="descending", ties="dense" if self.scenario == "ties-null" else "ordinal"
            )
        if isinstance(producer, mv.MaterializedForecastResult):
            history = self.source_inputs["daily"].execute()
            assert isinstance(
                history, (mv.MaterializedRolledNumericRelation, mv.MaterializedNumericRelation)
            )
            self.fixed_inputs = [history]
            return FixedCompound(
                self,
                tuple(
                    history.forecast(horizon=mv.periods(4), model=model)
                    for model in (mv.naive(), mv.drift(), mv.seasonal_naive(periods=4))
                ),
            )
        if isinstance(producer, mv.MaterializedAssociationResult):
            left, right = (
                self.source_inputs["daily"].execute(),
                self.source_inputs["other"].execute(),
            )
            assert isinstance(
                left, (mv.MaterializedRolledNumericRelation, mv.MaterializedNumericRelation)
            ) and isinstance(
                right, (mv.MaterializedRolledNumericRelation, mv.MaterializedNumericRelation)
            )
            self.fixed_inputs = [left, right]
            return FixedCompound(
                self,
                tuple(
                    left.correlate(right, method=method, lag_range=range(-2, 3))
                    for method in ("pearson", "spearman", "kendall")
                ),
            )
        if isinstance(producer, mv.MaterializedTimeRunResult):
            captured_runs = self.source_inputs["daily"].execute()
            assert isinstance(
                captured_runs,
                (mv.MaterializedNumericRelation, mv.MaterializedRolledNumericRelation),
            )
            self.fixed_inputs = [captured_runs]
            runs = captured_runs.runs(
                where=captured_runs.value.gte(0)
                if self.scenario == "cross-batch-long-runs-unavailable"
                else captured_runs.value.gt(0)
            )
            if self.scenario == "deviation-runs":
                captured_values = self.source_inputs["values"].execute()
                assert isinstance(captured_values, mv.MaterializedNumericRelation)
                self.fixed_inputs.append(captured_values)
                return FixedCompound(
                    self,
                    (
                        captured_values.deviation(method="zscore"),
                        captured_values.deviation(method="mad"),
                        runs,
                    ),
                )
            return runs
        if isinstance(producer, mv.MaterializedAttributionResult):
            captured_change = self.source_inputs["change"].execute()
            assert isinstance(captured_change, mv.MaterializedDifferenceRelation)
            self.fixed_inputs = [captured_change]
            axes = (ms.ref.dimension("cost.facts.tenant"), ms.ref.dimension("cost.facts.kind"))
            return FixedCompound(
                self,
                (
                    captured_change.attribute(axes=axes),
                    captured_change.attribute(axes=axes, top_k=1),
                ),
            )
        if self.scenario == "comparison-members-observe":
            current, baseline, full = (
                self.source_inputs[name].execute()
                for name in ("current", "baseline", "full_values")
            )
            assert (
                isinstance(current, mv.MaterializedNumericRelation)
                and isinstance(baseline, mv.MaterializedNumericRelation)
                and isinstance(full, mv.MaterializedNumericRelation)
            )
            self.fixed_inputs = [current, baseline, full]
            change = current.compare(baseline)
            return full.where(change.value.gt(0))
        if self.scenario == "skew-empty-groups":
            amounts, categories, groups = (
                self.source_inputs[name].execute() for name in ("amounts", "categories", "groups")
            )
            assert (
                isinstance(amounts, mv.MaterializedNumericRelation)
                and isinstance(categories, mv.MaterializedCategoryRelation)
                and isinstance(groups, mv.MaterializedCategoryRelation)
            )
            self.fixed_inputs = [amounts, categories, groups]
            return amounts.group_by(categories, groups=groups.group_by()).summarize(mv.sum())
        raise ValueError(
            "The current public contract has no fixed recompute entry for this source algorithm; a different terminal reduction is not a comparable cost sample"
        )

    def validate(self, result: Result) -> dict[str, object]:
        assert result._dataset is not None
        checked = result._dataset.verified()
        assert checked.primary is not None and checked.parts
        payload_digest = _digest(
            {
                "primary": checked.primary.to_pylist(),
                "parts": [
                    {"role": part.role, "rows": part.table.to_pylist()} for part in checked.parts
                ],
            }
        )
        if self.fixed_expected is not None and not any(output is result for output in self.outputs):
            actual = result.to_pandas().value.tolist()
            assert actual == [self.fixed_expected]
            assert result.to_pandas().cell_tag.tolist() == ["defined"]
            return {
                "passed": True,
                "expected": [self.fixed_expected],
                "result_digest": payload_digest,
                "values_digest": _digest(actual),
                "expected_digest": _digest([self.fixed_expected]),
                "oracle": "Exact sum of independently verified owned source-result values",
                "parts": [part.role for part in checked.parts],
            }
        if self.scenario == "baseline":
            baseline_expected = sum(
                _integer(row["amount"]) for row in self.rows if row["amount"] is not None
            )
            actual = result.to_pandas().value.tolist()
            assert actual == [baseline_expected], (actual, baseline_expected)
            assert result.to_pandas().cell_tag.tolist() == ["defined"]
            return {
                "passed": True,
                "expected": baseline_expected,
                "result_digest": payload_digest,
                "values_digest": _digest(actual),
                "expected_digest": _digest([baseline_expected]),
                "oracle": "Independent exact original-fact integer sum",
                "parts": [part.role for part in checked.parts],
                "schema": [[item.name, str(item.type)] for item in checked.primary.schema],
            }
        key_binding_digest = _keyed_oracle(self, result)
        expected = self._oracle_values(result)
        if isinstance(result, mv.MaterializedRankingResult):
            actual = result.ranks.to_pandas().value.dropna().tolist()
        elif isinstance(result, mv.MaterializedAttributionResult):
            actual = result.contribution.to_pandas().value.tolist()
        elif isinstance(result, mv.MaterializedForecastResult):
            actual = result.prediction.to_pandas().value.tolist()
        elif isinstance(result, mv.MaterializedAssociationResult):
            actual = result.coefficient.to_pandas().value.dropna().tolist()
        elif isinstance(result, mv.MaterializedTimeRunResult):
            actual = result.count.to_pandas().value.tolist()
        else:
            actual = result.to_pandas().value.dropna().tolist()
        assert len(actual) == len(expected), (len(actual), len(expected))
        assert all(
            math.isclose(float(a), float(b), rel_tol=1e-10, abs_tol=1e-12)
            for a, b in zip(sorted(actual), sorted(expected), strict=True)
        ), (actual, expected)
        return {
            "passed": True,
            "expected_digest": _digest(sorted(expected)),
            "result_digest": payload_digest,
            "values_digest": _digest(sorted(actual)),
            "output_rows": len(actual),
            "parts": [part.role for part in checked.parts],
            "oracle": "Independent original-fact arithmetic and exact key/state checks",
            "original_key_binding": True,
            "original_key_digest": key_binding_digest,
        }

    def _daily_values(self, column: str) -> list[int]:
        result = [0] * 16
        for row in self.rows:
            instant = row["happened"]
            assert isinstance(instant, datetime)
            value = row[column]
            if value is not None:
                result[(instant - START).days] += _integer(value)
        return result

    def _oracle_values(self, result: Result) -> Sequence[int | float]:
        outputs = (
            self.fixed_outputs
            if any(output is result for output in self.fixed_outputs)
            else self.outputs
        )
        original = [
            _integer(row["amount"]) if row["amount"] is not None else 0 for row in self.rows
        ]
        if self.scenario == "baseline":
            return [sum(original)]
        if isinstance(result, mv.MaterializedRankingResult):
            if self.scenario == "high-cardinality":
                return list(range(1, len(self.rows) + 1))
            defined = [_integer(row["amount"]) for row in self.rows if row["amount"] is not None]
            rank = {
                value: index + 1 for index, value in enumerate(sorted(set(defined), reverse=True))
            }
            frame = result.ranks.to_pandas()
            assert frame.cell_tag.tolist().count("null") == len(self.rows) - len(defined)
            return [rank[value] for value in defined]
        if self.scenario == "skew-empty-groups":
            totals = {_integer(row["owner"]): 0 for row in self.rows}
            totals[0] = sum(
                _integer(row["amount"])
                for row in self.rows
                if row["owner"] == 0 and row["amount"] is not None
            )
            frame = result.to_pandas().set_index("group")
            assert frame.value.to_dict() == totals
            return list(totals.values())
        if self.scenario == "comparison-members-observe":
            comparison_expected = {
                ("a", _integer(row["id"]), 1): _integer(row["amount"])
                for row in self.rows
                if row["happened"] == START
                and row["amount"] is not None
                and _integer(row["amount"]) > 0
            }
            frame = result.to_pandas().set_index(["member", "coord_0", "coord_1"])
            assert frame.value.to_dict() == comparison_expected
            return list(comparison_expected.values())
        if self.scenario in ("event-lifecycle-anchor", "many-occurrences-anchors-lags"):
            journeys, history, anchors = outputs[:3]
            assert isinstance(journeys, mv.MaterializedJourneyResult)
            assert isinstance(history, mv.MaterializedHistoryResult)
            assert isinstance(anchors, mv.MaterializedAnchorDomain)
            assert len(journeys.to_pandas()) == (len(self.rows) + 1) // 2
            assert len(anchors.to_pandas()) == (len(self.rows) + 1) // 2
            assert history._dataset is not None
            records = [
                json.loads(row["history__record"])
                for part in history._dataset.verified().parts
                if part.role == "history"
                for row in part.table.to_pylist()
            ]
            assert len(records) == GROUPS
            by_subject = {tuple(record["subject"]): record for record in records}
            assert set(by_subject) == {("a", owner) for owner in range(GROUPS)}
            for owner in range(GROUPS):
                record = by_subject[("a", owner)]
                original_events = [row for row in self.rows if row["owner"] == owner]
                evaluations = record["evaluations"]
                assert len(evaluations) == len(original_events)
                assert record["classification"] == ("seeded" if original_events else "not_started")
                assert record["pre_inception"] == []
                assert len(record["transitions"]) == int(len(original_events) >= 2)
                assert len(record["violations"]) == max(0, len(original_events) - 2)
                for ordinal, (evaluation, fact) in enumerate(
                    zip(evaluations, original_events, strict=True)
                ):
                    occurrence = evaluation["occurrence"]
                    instant = fact["happened"]
                    assert isinstance(instant, datetime)
                    assert occurrence["key"] == ["a", fact["id"], 1]
                    assert occurrence["event"] == "cost." + str(fact["kind"])
                    assert occurrence["occurred_at"] == instant.isoformat().replace("+00:00", "Z")
                    assert occurrence["sequence"] == fact["seq"]
                    assert evaluation["before"] == (
                        None if ordinal == 0 else "open" if ordinal == 1 else "done"
                    )
                    assert evaluation["after"] == ("open" if ordinal == 0 else "done")
                    assert evaluation["disposition"] == (
                        "inception"
                        if ordinal == 0
                        else "legal_transition"
                        if ordinal == 1
                        else "illegal_transition"
                    )
            event_expected = [
                _integer(self.rows[index + 1]["y"]) if index + 1 < len(self.rows) else 0
                for index in range(0, len(self.rows), 2)
            ]
            if self.scenario == "many-occurrences-anchors-lags":
                correlation = outputs[3]
                assert isinstance(correlation, mv.MaterializedAssociationResult)
                event_coefficients = correlation.coefficient.to_pandas()
                assert len(event_coefficients) == 9
                x, y = self._daily_values("amount"), self._daily_values("y")
                coefficients = []
                for lag in range(-4, 5):
                    xx = x[max(0, -lag) : min(len(x), len(x) - lag)]
                    yy = y[max(0, lag) : min(len(y), len(y) + lag)]
                    coefficient = _association(xx, yy, "spearman")
                    if math.isfinite(coefficient):
                        coefficients.append(coefficient)
                assert all(
                    math.isclose(float(a), float(b), rel_tol=1e-10, abs_tol=1e-12)
                    for a, b in zip(
                        sorted(event_coefficients.value.dropna().tolist()),
                        sorted(coefficients),
                        strict=True,
                    )
                )
                assert correlation.selected.to_pandas().value.tolist().count(True) == 1
            return event_expected
        if self.scenario == "multi-root-ratio-empty-groups":
            a, b = [0] * GROUPS, [0] * GROUPS
            for row in self.rows:
                owner = _integer(row["owner"])
                a[owner] += _integer(row["amount"]) if row["amount"] is not None else 0
                b[owner] += _integer(row["y"])
            frame = outputs[0].to_pandas()
            assert frame.cell_reason.tolist().count("zero_denominator") == 1
            return [sum(a) / sum(b)]
        if self.scenario == "exact-distinct-quantile":
            groups: list[list[int]] = [[] for _ in range(GROUPS)]
            for row in self.rows:
                if row["amount"] is not None:
                    groups[_integer(row["owner"])].append(_integer(row["amount"]))
            distinct = outputs[0].to_pandas().value.tolist()
            assert sorted(distinct) == sorted(
                len(set(quantile_values)) for quantile_values in groups
            )
            quantile_expected = []
            for quantile_values in groups:
                quantile_values.sort()
                if quantile_values:
                    middle = (len(quantile_values) - 1) / 2
                    low, high = math.floor(middle), math.ceil(middle)
                    quantile_expected.append((quantile_values[low] + quantile_values[high]) / 2)
            assert result.to_pandas().cell_tag.tolist().count("null") == 1
            return quantile_expected
        if isinstance(result, mv.MaterializedAttributionResult):
            amounts: dict[str, int] = {"started": 0, "finished": 0}
            for row in self.rows:
                sign = 1 if row["happened"] == START else -1
                amounts[str(row["kind"])] += sign * (
                    _integer(row["amount"]) if row["amount"] is not None else 0
                )
            full = outputs[0]
            assert isinstance(full, mv.MaterializedAttributionResult)
            assert sorted(full.contribution.to_pandas().value.tolist()) == sorted(amounts.values())
            return list(amounts.values())
        if isinstance(result, mv.MaterializedForecastResult):
            training = self._daily_values("amount")
            models = ("naive", "drift", "seasonal")
            for output, model in zip(outputs, models, strict=True):
                assert isinstance(output, mv.MaterializedForecastResult)
                forecast_expected = (
                    [training[-1]] * 4
                    if model == "naive"
                    else [
                        training[-1] + (training[-1] - training[0]) / (len(training) - 1) * step
                        for step in range(1, 5)
                    ]
                    if model == "drift"
                    else training[-4:]
                )
                assert all(
                    math.isclose(float(a), float(b), rel_tol=1e-12, abs_tol=1e-12)
                    for a, b in zip(
                        output.prediction.to_pandas().value.tolist(), forecast_expected, strict=True
                    )
                )
            return [_integer(value) for value in training[-4:]]
        if isinstance(result, mv.MaterializedAssociationResult):
            x, y = self._daily_values("amount"), self._daily_values("y")
            association_values: list[int | float] = []
            for output, method in zip(outputs, ("pearson", "spearman", "kendall"), strict=True):
                assert isinstance(output, mv.MaterializedAssociationResult)
                association_expected = []
                for lag in range(-2, 3):
                    xx = x[max(0, -lag) : min(len(x), len(x) - lag)]
                    yy = y[max(0, lag) : min(len(y), len(y) + lag)]
                    association_coefficient = _association(xx, yy, method)
                    if math.isfinite(association_coefficient):
                        association_expected.append(association_coefficient)
                actual = output.coefficient.to_pandas().value.dropna().tolist()
                assert len(actual) == len(association_expected)
                assert all(
                    math.isclose(float(a), float(b), rel_tol=1e-10, abs_tol=1e-12)
                    for a, b in zip(sorted(actual), sorted(association_expected), strict=True)
                )
                assert output.selected.to_pandas().value.tolist().count(True) >= 1
                association_values = association_expected
            return association_values
        if isinstance(result, mv.MaterializedTimeRunResult):
            if self.scenario == "cross-batch-long-runs-unavailable":
                original_grid = self.outputs[0].to_pandas()
                assert original_grid.cell_tag.tolist().count("unknown") == GROUPS
                assert set(
                    original_grid.loc[original_grid.cell_tag == "unknown", "cell_reason"]
                ) == {"insufficient_business_coverage"}
                assert sorted(result.duration.to_pandas().value.tolist()) == sorted(
                    [
                        timedelta(days=CROSS_GAP),
                        timedelta(days=CROSS_PERIODS - CROSS_GAP - 1),
                    ]
                    * GROUPS
                )
                return [CROSS_GAP, CROSS_PERIODS - CROSS_GAP - 1] * GROUPS
            for fit, method in zip(outputs[:2], ("zscore", "mad"), strict=True):
                assert isinstance(fit, mv.MaterializedDeviationResult)
                assert sorted(fit.observed.to_pandas().value.tolist()) == sorted(original)
                if method == "zscore":
                    center = Fraction(sum(original), len(original))
                    variance = sum((Fraction(value) - center) ** 2 for value in original) / len(
                        original
                    )
                    scale = math.sqrt(float(variance))
                else:
                    ordered = sorted(original)
                    center = Fraction(
                        ordered[(len(ordered) - 1) // 2] + ordered[len(ordered) // 2], 2
                    )
                    residuals = sorted(abs(Fraction(value) - center) for value in original)
                    scale = float(
                        (residuals[(len(residuals) - 1) // 2] + residuals[len(residuals) // 2]) / 2
                    )
                    scale = scale * 7413 / 5000 if scale else float(sum(residuals) / len(residuals))
                assert all(
                    math.isclose(float(value), float(center), rel_tol=1e-12)
                    for value in fit.reference.to_pandas().value.tolist()
                )
                scores = fit.score.to_pandas().value.tolist()
                expected_scores = [(float(Fraction(value) - center) / scale) for value in original]
                assert all(
                    math.isclose(float(a), float(b), rel_tol=1e-10, abs_tol=1e-12)
                    for a, b in zip(sorted(scores), sorted(expected_scores), strict=True)
                )
            return [16]
        raise ValueError("Named scenario has no complete independent oracle")

    def identity(self, result: Result) -> dict[str, object]:
        assert result._dataset is not None
        bindings: list[dict[str, object]] = []
        outputs = (
            self.fixed_outputs
            if any(output is result for output in self.fixed_outputs)
            else self.outputs
            if any(output is result for output in self.outputs)
            else [result]
        )
        for output in outputs:
            assert output._dataset is not None
            plan = descriptor_plan(output._dataset.artifact.descriptor, output._node.definition)
            bindings.extend(
                {
                    "key": key_json(item.key),
                    "route": item.key.route,
                    "node": item.node_id,
                    "implementation": str(item.implementation.qualification),
                }
                for item in plan.physical_requirements
            )
        own = descriptor_plan(result._dataset.artifact.descriptor, result._node.definition)
        root = next(
            (
                item
                for item in own.physical_requirements
                if item.node_id == result._node.definition.identity
            ),
            None,
        )
        return {
            "artifact_ref": result.state.artifact_ref.ref,
            "actual_routes": sorted({str(row["route"]) for row in bindings}),
            "root_route": root.key.route if root is not None else "unknown",
            "method_bindings": bindings,
            "parts": [part.role for part in result._dataset.verified().parts],
            "question": "Exact original-fact integer total"
            if self.scenario == "baseline"
            else self.scenario,
            "component_artifacts": [output.state.artifact_ref.ref for output in outputs],
            "fixed_input_artifacts": [
                output.state.artifact_ref.ref for output in self.fixed_inputs
            ],
        }


@dataclass
class FixedCompound:
    """Measure each original public method in a compound fixed question."""

    workload: Workload
    operations: tuple[Logical, ...]

    def execute(self) -> Result:
        self.workload.fixed_outputs.clear()
        for operation in self.operations:
            self.workload.fixed_outputs.append(operation.execute())
        if not self.workload.fixed_outputs:
            raise ValueError("An empty compound is not a measured algorithm")
        return self.workload.fixed_outputs[-1]


@contextmanager
def workload(
    backend: str,
    profile: str,
    facts: int,
    scenario: str,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Workload]:
    if scenario not in SCENARIOS:
        raise ValueError("Unknown frozen cost scenario")
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(root))
    monkeypatch.chdir(root)
    rows = _rows(facts, scenario)
    with _tables(backend, profile, root, monkeypatch, rows) as (case, subjects, other):
        _author(root, backend, case, subjects, other, monkeypatch)
        session = mv.session.get_or_create("r96-cost", report_timezone="UTC")
        yield Workload(
            session,
            scenario,
            rows,
            {
                **case.environment,
                "fixture_batch_rows": BATCH_ROWS,
                "fact_rows": facts,
                "companion_root_rows": facts,
                "subject_rows": GROUPS,
                "raw_input_digest": _digest(rows),
                "cross_batch_grid_periods": CROSS_PERIODS
                if scenario == "cross-batch-long-runs-unavailable"
                else None,
                "cross_batch_unknown_period": CROSS_GAP
                if scenario == "cross-batch-long-runs-unavailable"
                else None,
                "replay_question": "retain illegal repeated inception triggers"
                if scenario in ("event-lifecycle-anchor", "many-occurrences-anchors-lags")
                else None,
            },
        )
