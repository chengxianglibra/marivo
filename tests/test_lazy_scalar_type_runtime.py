"""Real scalar types through source execution, Parquet and cold recovery."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pytest

from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import MaterializationError, SourceSchemaError
from marivo.analysis.observation.predicates import eq
from marivo.datasource.ir import TableSourceIR
from marivo.refs import ref
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.validator import Registry
from tests.lazy_execution_fixtures import make_execution_registry
from tests.lazy_scalar_source_fixtures import registry_for
from tests.lazy_scalar_type_fixtures import Engine, arrow_result, cold_check, source_writer

pytestmark = pytest.mark.runtime
ENGINES = ["duckdb", "sqlite", "postgres", "mysql", "clickhouse", "trino"]
REVENUE = ref.metric("sales.revenue")
CHANNEL = ref.dimension("sales.orders.channel")


@contextmanager
def source(
    engine: Engine,
    path: Path,
    monkeypatch: pytest.MonkeyPatch,
    physical: str,
    logical: str,
    values: list[str],
    *,
    identity: bool = False,
    measure: bool = False,
) -> Iterator[tuple[Registry, CompiledExpressionSidecar, str]]:
    if (
        engine not in {"duckdb", "sqlite"}
        and os.environ.get(f"MARIVO_{engine.upper()}_ANALYSIS_TEST") != "1"
    ):
        pytest.skip(f"opt-in {engine} service")
    name = "c2_" + uuid4().hex
    database = path / "source.db"
    if engine == "duckdb":
        registry, sidecar = make_execution_registry(database)
    elif engine == "postgres":
        from tests.lazy_postgres_fixtures import registry_for as pg_registry

        registry, sidecar = pg_registry(name, monkeypatch)
    else:
        registry, sidecar = registry_for(database, engine=engine, table=name)
        if engine in {"mysql", "clickhouse"}:
            from tests.multisource_environment.credentials import password

            monkeypatch.setenv(f"MARIVO_TEST_{engine.upper()}_PASSWORD", password())
    entity = registry.entities["sales.orders"]
    assert isinstance(entity.source, TableSourceIR)
    selected = "id" if identity else "amount" if measure else "channel"
    columns = tuple(
        (
            key,
            replace(
                binding,
                data_type=logical
                if key == selected
                else "int64"
                if key in {"id", "amount"} or (measure and key == "weight")
                else binding.data_type,
            ),
        )
        for key, binding in entity.source.columns
    )
    entity = replace(entity, source=replace(entity.source, table=name, columns=columns))
    metrics = dict(registry.metrics)
    for agg in ("min", "max"):
        metrics[f"sales.{agg}_amount"] = replace(
            metrics["sales.revenue"],
            semantic_id=f"sales.{agg}_amount",
            name=f"{agg}_amount",
            aggregation=agg,
        )
    registry = replace(
        registry, entities={**registry.entities, entity.semantic_id: entity}, metrics=metrics
    )
    registry.freeze()
    suffix = " ENGINE=MergeTree ORDER BY tuple()" if engine == "clickhouse" else ""
    declarations = [
        f"id {physical if identity else 'BIGINT'}",
        f"amount {physical if measure else 'BIGINT'}",
    ]
    if measure:
        declarations.append("weight BIGINT")
    if not identity and not measure:
        declarations.append(f"channel {physical}")
    try:
        with source_writer(engine, database) as execute:
            execute(f"CREATE TABLE {name} ({','.join(declarations)}){suffix}")
            rows = [
                f"({value if identity else i},{value if measure else 1}"
                + (f",{value}" if not identity and not measure else "")
                + (",1" if measure else "")
                + ")"
                for i, value in enumerate(values, 1)
            ]
            execute(f"INSERT INTO {name} VALUES " + ",".join(rows))
        yield registry, sidecar, name
    finally:
        with source_writer(engine, database) as execute:
            execute(f"DROP TABLE IF EXISTS {name}")


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("kind", ["boolean", "string", "timestamp"])
def test_scalar_group_transport_and_cold(
    engine: Engine, kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected: list[bool | str | datetime | None]
    if kind == "boolean":
        physical = "Nullable(Bool)" if engine == "clickhouse" else "BOOLEAN"
        values = ["false", "true", "NULL", "true"]
        expected = [False, True, None]
        logical = "boolean"
    elif kind == "timestamp":
        physical = {
            "sqlite": "TIMESTAMP",
            "mysql": "DATETIME(6)",
            "clickhouse": "Nullable(DateTime('UTC'))",
        }.get(engine, "TIMESTAMP(6)")
        logical = "timestamp"
        fraction = "000000" if engine == "clickhouse" else "000001"
        texts = ["2024-02-29 00:00:00.000000", f"2024-02-29 00:00:01.{fraction}"]
        prefix = "TIMESTAMP " if engine == "trino" else ""
        values = [prefix + repr(text) for text in texts] + ["NULL", prefix + repr(texts[1])]
        expected = [datetime.fromisoformat(text) for text in texts] + [None]
    else:
        physical = {
            "sqlite": "VARCHAR(20)",
            "mysql": "VARCHAR(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin",
            "clickhouse": "LowCardinality(Nullable(String))",
        }.get(engine, "VARCHAR(20)")
        logical = "string"
        expected = ["", "A", "a", "a ", "\u00e9", None]
        values = ["NULL" if value is None else repr(value) for value in expected]
    with source(engine, tmp_path, monkeypatch, physical, logical, values) as (registry, sidecar, _):
        project = tmp_path / "project"
        runtime = DatasetRuntime.create(project, "c2-types")
        sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
        target = sources.observe(REVENUE).with_dimensions(CHANNEL).aggregate()
        result = target.execute()
        table = arrow_result(runtime, result.state.artifact_ref.ref)
        actual = table.column("channel").to_pylist()
        assert set(actual) == set(expected)
        if kind in {"boolean", "timestamp"}:
            counts = dict(zip(actual, table.column("revenue").to_pylist(), strict=True))
            assert counts[expected[1]] == 2
        if kind != "timestamp":
            selected = expected[0]
            assert isinstance(selected, (bool, str))
            filtered = target.where(eq(CHANNEL, selected)).execute()
            assert filtered.to_pandas().revenue.tolist() == [1]
        cold_check(project, runtime.session_ref, result.state.artifact_ref.ref, table, tmp_path)


@pytest.mark.parametrize("engine", ["duckdb", "mysql", "clickhouse"])
def test_uint64_identity_is_exact(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    physical = {"duckdb": "UBIGINT", "mysql": "BIGINT UNSIGNED", "clickhouse": "UInt64"}[engine]
    expected = [0, 2**53 + 1, 2**63, 2**64 - 1]
    with source(
        engine, tmp_path, monkeypatch, physical, "uint64", list(map(str, expected)), identity=True
    ) as (registry, sidecar, _):
        runtime = DatasetRuntime.create(tmp_path / "project", "c2-identity")
        result = (
            runtime.sources(semantic_registry=registry, sidecar=sidecar)
            .population(ref.entity("sales.orders"))
            .execute()
        )
        assert sorted(result.to_pandas().entity_identity.tolist()) == [(v,) for v in expected]
        table = arrow_result(runtime, result.state.artifact_ref.ref)
        cold_check(
            tmp_path / "project",
            runtime.session_ref,
            result.state.artifact_ref.ref,
            table,
            tmp_path,
        )


@pytest.mark.parametrize("engine", ["duckdb", "mysql", "clickhouse"])
@pytest.mark.parametrize("total", [258, 2**63 - 1, 2**63, 2**64])
def test_uint_sum_result_range(
    engine: Engine, total: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    physical = {"duckdb": "UBIGINT", "mysql": "BIGINT UNSIGNED", "clickhouse": "UInt64"}[engine]
    values = ["0", "1", "2", "255"] if total == 258 else [str(total - 1), "1"]
    with source(engine, tmp_path, monkeypatch, physical, "uint64", values, measure=True) as (
        registry,
        sidecar,
        _,
    ):
        runtime = DatasetRuntime.create(tmp_path / "project", "c2-sum")
        target = (
            runtime.sources(semantic_registry=registry, sidecar=sidecar)
            .observe(REVENUE)
            .aggregate()
        )
        if total >= 2**63:
            with pytest.raises(Exception, match=r"(?i)(range|overflow|convert)"):
                target.execute()
            assert runtime.store.resources(runtime.session_ref) == ()
        else:
            assert target.execute().to_pandas().revenue.tolist() == [total]


@pytest.mark.parametrize(
    "physical,logical,value",
    [
        ("BOOLEAN", "boolean", "2"),
        ("BOOLEAN", "boolean", "-1"),
        ("TIMESTAMP", "timestamp", "'2024-02-30 00:00:00.000000'"),
        ("TIMESTAMP", "timestamp", "'2024-01-01 00:00:00'"),
        ("TIMESTAMP", "timestamp", "1700000000"),
    ],
)
def test_sqlite_invalid_storage_never_publishes(
    physical: str, logical: str, value: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with source("sqlite", tmp_path, monkeypatch, physical, logical, [value]) as (
        registry,
        sidecar,
        _,
    ):
        runtime = DatasetRuntime.create(tmp_path / "project", "c2-invalid")
        target = (
            runtime.sources(semantic_registry=registry, sidecar=sidecar)
            .observe(REVENUE)
            .with_dimensions(CHANNEL)
            .aggregate()
            .where(eq(REVENUE, -1))
        )
        with pytest.raises(MaterializationError):
            target.execute()
        assert runtime.store.resources(runtime.session_ref) == ()


@pytest.mark.parametrize("engine", ["duckdb", "mysql", "clickhouse"])
@pytest.mark.parametrize("width", [8, 16, 32, 64])
def test_unsigned_dimensions_filter_and_order(
    engine: Engine, width: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    physical = {
        "duckdb": {8: "UTINYINT", 16: "USMALLINT", 32: "UINTEGER", 64: "UBIGINT"},
        "mysql": {
            8: "TINYINT UNSIGNED",
            16: "SMALLINT UNSIGNED",
            32: "INT UNSIGNED",
            64: "BIGINT UNSIGNED",
        },
        "clickhouse": {i: f"Nullable(UInt{i})" for i in (8, 16, 32, 64)},
    }[engine][width]
    maximum = 2**width - 1
    with source(
        engine, tmp_path, monkeypatch, physical, f"uint{width}", ["0", "1", str(maximum), "NULL"]
    ) as (registry, sidecar, _):
        runtime = DatasetRuntime.create(tmp_path / "project", "c2-uint-dim")
        grouped = (
            runtime.sources(semantic_registry=registry, sidecar=sidecar)
            .observe(REVENUE)
            .with_dimensions(CHANNEL)
            .aggregate()
        )
        result = grouped.execute()
        table = arrow_result(runtime, result.state.artifact_ref.ref)
        assert set(table.column("channel").to_pylist()) == {0, 1, maximum, None}
        assert table.schema.field("channel").type == getattr(pa, f"uint{width}")()
        assert grouped.where(eq(CHANNEL, maximum)).execute().to_pandas().revenue.tolist() == [1]
        # Existing Metric rank orders its scalar value and uses the typed axis for ties.
        ranked = grouped.rank(grouped.fields.metric(REVENUE)).limit(3).execute()
        assert len(ranked.to_pandas()) == 3


@pytest.mark.parametrize("engine", ["mysql", "sqlite"])
@pytest.mark.parametrize("invalid", ["2", "-1", "'true'"])
def test_bounded_boolean_decode_rejects_invalid_observed_cells(
    engine: Engine, invalid: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if engine == "mysql" and invalid == "'true'":
        pytest.skip("MySQL strict integer insertion already rejects text")
    with source(engine, tmp_path, monkeypatch, "BOOLEAN", "boolean", [invalid]) as (
        registry,
        _,
        name,
    ):
        from marivo.datasource.engines import mysql, sqlite
        from marivo.datasource.errors import DatasourcePreviewError

        if engine == "sqlite":
            backend = sqlite.connect("test", {"path": str(tmp_path / "source.db")})
        else:
            from tests.multisource_environment.credentials import password

            spec = registry.datasources["warehouse"]
            backend = mysql.connect("test", {**dict(spec.fields), "password": password()})
        try:
            expression = backend.sql(f"SELECT channel FROM {name}", schema={"channel": "boolean"})
            with pytest.raises(DatasourcePreviewError, match="Boolean storage"):
                expression.execute()
        finally:
            backend.disconnect()


@pytest.mark.parametrize("engine", ["postgres", "mysql", "trino"])
def test_fixed_character_rejects_different_string_semantics(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    physical = (
        "CHAR(8) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin" if engine == "mysql" else "CHAR(8)"
    )
    with source(engine, tmp_path, monkeypatch, physical, "string", ["'a'", "'a '"]) as (
        registry,
        sidecar,
        _,
    ):
        runtime = DatasetRuntime.create(tmp_path / "project", "c2-char")
        target = (
            runtime.sources(semantic_registry=registry, sidecar=sidecar)
            .observe(REVENUE)
            .with_dimensions(CHANNEL)
            .aggregate()
        )
        if engine == "trino":
            # Iceberg exposes this DDL as varchar; qualify the actual exposed type.
            result = target.execute()
            assert set(result.to_pandas().channel.tolist()) == {"a", "a "}
        else:
            with pytest.raises(MaterializationError):
                target.execute()


@pytest.mark.parametrize(
    "engine,kind",
    [(engine, kind) for engine in ENGINES for kind in ("boolean", "timestamp")]
    + [("sqlite", "int64"), ("mysql", "int64"), ("mysql", "uint64")],
)
def test_public_load_to_scalar_execution(
    engine: Engine, kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logical = (
        "boolean"
        if kind == "boolean"
        else "timestamp(0)"
        if engine == "clickhouse"
        else "timestamp(6)"
    )
    physical = (
        ("Nullable(Bool)" if engine == "clickhouse" else "BOOLEAN")
        if kind == "boolean"
        else {
            "sqlite": "TIMESTAMP",
            "mysql": "TIMESTAMP(6)",
            "clickhouse": "Nullable(DateTime('UTC'))",
        }.get(engine, "TIMESTAMP(6)")
    )
    values = (
        ["false", "true", "NULL"]
        if kind == "boolean"
        else (
            [
                "TIMESTAMP '2024-02-29 00:00:00.000000'",
                "TIMESTAMP '2024-03-01 00:00:00.000000'",
                "NULL",
            ]
            if engine == "trino"
            else ["'2024-02-29 00:00:00.000000'", "'2024-03-01 00:00:00.000000'", "NULL"]
        )
    )
    if kind in {"int64", "uint64"}:
        logical = kind
        physical = "BIGINT UNSIGNED" if kind == "uint64" else "BIGINT"
        integer_values = [2**64 - 1 if kind == "uint64" else 2**62 + 1, 2**53 + 1]
        values = [str(value) for value in integer_values] + ["NULL"]
    with source(engine, tmp_path, monkeypatch, physical, logical, values) as (registry, _, name):
        import marivo.analysis as mv
        import marivo.datasource as md
        import marivo.semantic as ms

        project = tmp_path / "public"
        datasource = project / "models/datasources"
        semantic = project / "models/semantic/sales"
        datasource.mkdir(parents=True)
        semantic.mkdir(parents=True)
        (project / "marivo.toml").write_text('[project]\nname = "c2-public"\n')
        spec = registry.datasources["warehouse"]
        fields = {
            **dict(spec.fields),
            **{f"{key}_env": value for key, value in spec.env_refs.items()},
        }
        if "user" in fields:
            monkeypatch.setenv("MARIVO_C2_READER", str(fields.pop("user")))
            fields["user_env"] = "MARIVO_C2_READER"
        (datasource / "warehouse.py").write_text(
            f"import marivo.datasource as md\nmd.{engine}(name='warehouse', **{fields!r})\n"
        )
        (semantic / "_domain.py").write_text(
            "import marivo.semantic as ms\nms.domain(name='sales', owner='Data', default=True)\n"
        )
        (semantic / "orders.py").write_text(
            "import marivo.datasource as md\nimport marivo.semantic as ms\n"
            f"orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table({name!r}, columns={{'id': md.source_column('id', data_type='int64'), 'amount': md.source_column('amount', data_type='int64'), 'channel': md.source_column('channel', data_type={logical!r})}}), primary_key=['id'])\n"
            "channel = ms.dimension_column(name='channel', entity=orders, column='channel')\n"
            "amount = ms.measure_column(name='amount', entity=orders, column='amount', additivity='additive')\n"
            "revenue = ms.aggregate(name='revenue', measure=amount, agg='sum')\n"
        )
        monkeypatch.chdir(project)
        catalog = ms.load(workspace_dir=project)
        catalog.require(REVENUE)
        inspection = md.inspect(
            ms.ref.datasource("warehouse"),
            md.table(
                name,
                columns={
                    "id": md.source_column("id", data_type="int64"),
                    "amount": md.source_column("amount", data_type="int64"),
                    "channel": md.source_column("channel", data_type=logical),
                },
            ),
        )
        session = mv.session.get_or_create("c2-public")
        result = session.observe(REVENUE).with_dimensions(CHANNEL).aggregate().execute()
        assert sorted(result.to_pandas().revenue.tolist()) == [1, 1, 1]
        if kind in {"int64", "uint64"}:
            scope = md.unpruned(max_rows=10, timeout_seconds=5)
            expected_cells = set(integer_values) | {None}
            snapshot = inspection.sample(scope=scope, columns=("channel",), persist_values=True)
            assert {row["channel"] for row in snapshot.retained_values} == expected_cells
            preview = catalog.preview(CHANNEL, scope=scope)
            assert {row["channel"] for row in preview.rows} == expected_cells
        if kind == "boolean" and engine in {"sqlite", "mysql"}:
            from marivo.datasource.errors import DatasourceAuthoringError, DatasourcePreviewError
            from marivo.semantic.errors import SemanticRuntimeError

            with source_writer(engine, tmp_path / "source.db") as execute:
                execute(f"UPDATE {name} SET channel=2 WHERE id=1")
            scope = md.unpruned(max_rows=10, timeout_seconds=5)
            with pytest.raises(DatasourceAuthoringError):
                inspection.sample(scope=scope, columns=("channel",), persist_values=True)
            with pytest.raises((DatasourcePreviewError, SemanticRuntimeError)):
                catalog.preview(CHANNEL, scope=scope)
            report = catalog.source_health(
                [CHANNEL],
                checks=[ms.source_check.allowed_values(CHANNEL, values=(False, True))],
                scope=scope,
            )
            check = next(item for item in report.checks if item.kind == "allowed_values")
            assert check.status != "current"


@pytest.mark.parametrize("engine", ["duckdb", "mysql", "clickhouse"])
def test_unsigned_reducers_and_retained_components(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    physical = {"duckdb": "UBIGINT", "mysql": "BIGINT UNSIGNED", "clickhouse": "Nullable(UInt64)"}[
        engine
    ]
    with source(
        engine,
        tmp_path,
        monkeypatch,
        physical,
        "uint64",
        ["0", "1", "2", "255", "NULL"],
        measure=True,
    ) as (registry, sidecar, name):
        runtime = DatasetRuntime.create(tmp_path / "project", "c2-reducers")
        refs = [
            ref.metric("sales." + n)
            for n in (
                "revenue",
                "order_count",
                "min_amount",
                "max_amount",
                "mean_amount",
                "weighted_amount",
                "conversion_rate",
            )
        ]
        logical = runtime.sources(semantic_registry=registry, sidecar=sidecar).observe(tuple(refs))
        direct = logical.aggregate().execute().to_pandas()
        assert direct.iloc[0].to_dict() == {
            "revenue": 258,
            "order_count": 4,
            "min_amount": 0,
            "max_amount": 255,
            "mean_amount": 64.5,
            "weighted_amount": 64.5,
            "conversion_rate": 64.5,
        }
        retained = logical.execute()
        with source_writer(engine, tmp_path / "source.db") as execute:
            execute(f"DROP TABLE {name}")
        folded = retained.aggregate().execute().to_pandas()
        assert folded.to_dict("list") == direct.to_dict("list")
        assert runtime.statistics.events.get("credential_resolution", 0) == 0
        assert runtime.statistics.events.get("profile_resolution", 0) == 0


@pytest.mark.parametrize("engine", ["duckdb", "postgres", "mysql", "trino"])
@pytest.mark.parametrize("scale", [0, 3, 6])
def test_explicit_timestamp_precision(
    engine: Engine, scale: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    physical = f"{'DATETIME' if engine == 'mysql' else 'TIMESTAMP'}({scale})"
    text = "2024-02-29 12:34:56" + (".123" if scale == 3 else ".123456" if scale == 6 else "")
    literal = ("TIMESTAMP " if engine == "trino" else "") + repr(text)
    with source(
        engine, tmp_path, monkeypatch, physical, f"timestamp({scale})", [literal, "NULL"]
    ) as (registry, sidecar, _):
        runtime = DatasetRuntime.create(tmp_path / "project", "c2-time-precision")
        if engine == "trino" and scale < 6:
            target = (
                runtime.sources(semantic_registry=registry, sidecar=sidecar)
                .observe(REVENUE)
                .with_dimensions(CHANNEL)
                .aggregate()
            )
            with pytest.raises(SourceSchemaError, match="type_mismatch"):
                target.execute()
            assert runtime.store.resources(runtime.session_ref) == ()
            entity = registry.entities["sales.orders"]
            assert isinstance(entity.source, TableSourceIR)
            entity = replace(
                entity,
                source=replace(
                    entity.source,
                    columns=tuple(
                        (
                            key,
                            replace(binding, data_type="timestamp(6)")
                            if key == "channel"
                            else binding,
                        )
                        for key, binding in entity.source.columns
                    ),
                ),
            )
            registry = replace(registry, entities={**registry.entities, entity.semantic_id: entity})
            registry.freeze()
        result = (
            runtime.sources(semantic_registry=registry, sidecar=sidecar)
            .observe(REVENUE)
            .with_dimensions(CHANNEL)
            .aggregate()
            .execute()
        )
        table = arrow_result(runtime, result.state.artifact_ref.ref)
        assert set(table.column("channel").to_pylist()) == {datetime.fromisoformat(text), None}
        assert table.schema.field("channel").type == pa.timestamp(
            "ms" if scale <= 3 and engine != "trino" else "us"
        )


@pytest.mark.parametrize("engine", ["duckdb", "mysql", "clickhouse"])
@pytest.mark.parametrize("duplicate", [False, True])
def test_uint64_composite_identity_validation(
    engine: Engine, duplicate: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    physical = {"duckdb": "UBIGINT", "mysql": "BIGINT UNSIGNED", "clickhouse": "UInt64"}[engine]
    with source(
        engine,
        tmp_path,
        monkeypatch,
        physical,
        "uint64",
        [str(2**63), str(2**64 - 1)],
        identity=True,
    ) as (registry, sidecar, name):
        entity = registry.entities["sales.orders"]
        entity = replace(entity, primary_key=("id", "amount"))
        registry = replace(registry, entities={**registry.entities, entity.semantic_id: entity})
        registry.freeze()
        if duplicate:
            with source_writer(engine, tmp_path / "source.db") as execute:
                execute(f"INSERT INTO {name} VALUES ({2**64 - 1},1)")
        runtime = DatasetRuntime.create(tmp_path / "project", "c2-composite")
        target = runtime.sources(semantic_registry=registry, sidecar=sidecar).population(
            ref.entity("sales.orders")
        )
        if duplicate:
            with pytest.raises(MaterializationError):
                target.execute()
            assert runtime.store.resources(runtime.session_ref) == ()
        else:
            result = target.execute()
            assert sorted(result.to_pandas().entity_identity.tolist()) == [
                (2**63, 1),
                (2**64 - 1, 1),
            ]


@pytest.mark.parametrize("engine", ["duckdb", "mysql", "clickhouse"])
def test_uint64_relationship_keys_preserve_full_range(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    physical = {"duckdb": "UBIGINT", "mysql": "BIGINT UNSIGNED", "clickhouse": "UInt64"}[engine]
    with source(
        engine,
        tmp_path,
        monkeypatch,
        physical,
        "uint64",
        [str(2**63), str(2**64 - 1)],
        identity=True,
    ) as (registry, sidecar, name):
        entities = dict(registry.entities)
        for key in ("sales.orders", "sales.customers"):
            entity = entities[key]
            assert isinstance(entity.source, TableSourceIR)
            entities[key] = replace(
                entity,
                source=replace(
                    entity.source,
                    table=name,
                    columns=tuple(
                        (
                            column,
                            replace(binding, data_type="uint64")
                            if column in {"id", "customer_id"}
                            else binding,
                        )
                        for column, binding in entity.source.columns
                    ),
                ),
            )
        registry = replace(registry, entities=entities)
        registry.freeze()
        string_type = (
            "VARCHAR(8) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin"
            if engine == "mysql"
            else "String"
            if engine == "clickhouse"
            else "VARCHAR"
        )
        with source_writer(engine, tmp_path / "source.db") as execute:
            execute(f"ALTER TABLE {name} ADD COLUMN customer_id {physical} DEFAULT {2**64 - 1}")
            execute(f"ALTER TABLE {name} ADD COLUMN region {string_type} DEFAULT 'target'")
        runtime = DatasetRuntime.create(tmp_path / "project", "c2-uint-relation")
        result = (
            runtime.sources(semantic_registry=registry, sidecar=sidecar)
            .observe(REVENUE)
            .with_dimensions(ref.dimension("sales.customers.region"))
            .aggregate()
            .execute()
        )
        assert result.to_pandas().to_dict("list") == {"region": ["target"], "revenue": [2]}


@pytest.mark.parametrize("engine", ["duckdb", "mysql", "clickhouse"])
def test_uint64_extreme_min_max(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    physical = {"duckdb": "UBIGINT", "mysql": "BIGINT UNSIGNED", "clickhouse": "UInt64"}[engine]
    with source(
        engine, tmp_path, monkeypatch, physical, "uint64", ["0", str(2**64 - 1)], measure=True
    ) as (registry, sidecar, _):
        runtime = DatasetRuntime.create(tmp_path / "project", "c2-extrema")
        result = (
            runtime.sources(semantic_registry=registry, sidecar=sidecar)
            .observe([ref.metric("sales.min_amount"), ref.metric("sales.max_amount")])
            .aggregate()
            .execute()
        )
        table = arrow_result(runtime, result.state.artifact_ref.ref)
        assert table.column("min_amount").to_pylist() == [0]
        assert table.column("max_amount").to_pylist() == [2**64 - 1]
        assert table.schema.field("min_amount").type == pa.uint64()
        assert table.schema.field("max_amount").type == pa.uint64()


@pytest.mark.parametrize(
    "physical,logical,value,expected",
    [
        ("INT2", "int64", "42", 42),
        ("INT8", "int64", "42", 42),
        ("TINYINT", "int64", "42", 42),
        ("SMALLINT", "int64", "42", 42),
        ("MEDIUMINT", "int64", "42", 42),
        ("FLOAT", "float64", "1.25", 1.25),
        ("DOUBLE PRECISION", "float64", "1.25", 1.25),
        ("CHAR(3)", "string", "'a '", "a "),
        ("CLOB", "string", "'a '", "a "),
    ],
)
def test_sqlite_declared_aliases_execute(
    physical: str,
    logical: str,
    value: str,
    expected: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with source("sqlite", tmp_path, monkeypatch, physical, logical, [value, "NULL"]) as (
        registry,
        sidecar,
        _,
    ):
        runtime = DatasetRuntime.create(tmp_path / "project", "c2-sqlite-alias")
        result = (
            runtime.sources(semantic_registry=registry, sidecar=sidecar)
            .observe(REVENUE)
            .with_dimensions(CHANNEL)
            .aggregate()
            .execute()
        )
        table = arrow_result(runtime, result.state.artifact_ref.ref)
        assert set(table.column("channel").to_pylist()) == {expected, None}


@pytest.mark.parametrize("engine,physical", [("mysql", "TIMESTAMP(6)"), ("clickhouse", "DateTime")])
def test_timestamp_requires_actual_utc_source_fact(
    engine: Engine, physical: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = "2024-02-29 12:34:56"
    with source(engine, tmp_path, monkeypatch, physical, "timestamp", [repr(text)]) as (
        registry,
        sidecar,
        _,
    ):
        runtime = DatasetRuntime.create(tmp_path / "project", "c2-utc-source")
        result = (
            runtime.sources(semantic_registry=registry, sidecar=sidecar)
            .observe(REVENUE)
            .with_dimensions(CHANNEL)
            .aggregate()
            .execute()
        )
        table = arrow_result(runtime, result.state.artifact_ref.ref)
        assert table.column("channel").to_pylist() == [datetime.fromisoformat(text)]


@pytest.mark.parametrize("physical", ["String", "LowCardinality(String)", "Nullable(String)"])
def test_clickhouse_string_wrappers_preserve_padding(
    physical: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with source("clickhouse", tmp_path, monkeypatch, physical, "string", ["'a'", "'a '", "''"]) as (
        registry,
        sidecar,
        _,
    ):
        runtime = DatasetRuntime.create(tmp_path / "project", "c2-string-wrapper")
        result = (
            runtime.sources(semantic_registry=registry, sidecar=sidecar)
            .observe(REVENUE)
            .with_dimensions(CHANNEL)
            .aggregate()
            .execute()
        )
        assert set(result.to_pandas().channel.tolist()) == {"a", "a ", ""}


@pytest.mark.parametrize("literal", ["'infinity'", "'-infinity'"])
def test_postgres_infinite_timestamp_rejects_before_empty_publication(
    literal: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with source("postgres", tmp_path, monkeypatch, "TIMESTAMP", "timestamp", [literal]) as (
        registry,
        sidecar,
        _,
    ):
        runtime = DatasetRuntime.create(tmp_path / "project", "c2-infinite-time")
        target = (
            runtime.sources(semantic_registry=registry, sidecar=sidecar)
            .observe(REVENUE)
            .with_dimensions(CHANNEL)
            .aggregate()
            .where(eq(REVENUE, -1))
        )
        with pytest.raises(MaterializationError):
            target.execute()
        assert runtime.store.resources(runtime.session_ref) == ()


def test_mysql_mediumint_unsigned_maps_to_uint32(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with source(
        "mysql",
        tmp_path,
        monkeypatch,
        "MEDIUMINT UNSIGNED",
        "uint32",
        ["0", str(2**24 - 1), "NULL"],
    ) as (registry, sidecar, _):
        runtime = DatasetRuntime.create(tmp_path / "project", "c2-mediumint")
        result = (
            runtime.sources(semantic_registry=registry, sidecar=sidecar)
            .observe(REVENUE)
            .with_dimensions(CHANNEL)
            .aggregate()
            .execute()
        )
        table = arrow_result(runtime, result.state.artifact_ref.ref)
        assert table.schema.field("channel").type == pa.uint32()
        assert set(table.column("channel").to_pylist()) == {0, 2**24 - 1, None}


def test_mysql_timestamp_non_utc_session_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ibis.backends.mysql import Backend

    from marivo.analysis.materialization.mysql_execution import MySQLExecutionAdapter
    from tests.multisource_environment.credentials import password

    with source(
        "mysql", tmp_path, monkeypatch, "TIMESTAMP(6)", "timestamp", ["'2024-02-29 00:00:00'"]
    ) as (registry, _, name):
        spec = registry.datasources["warehouse"]
        backend = Backend().connect(**dict(spec.fields), password=password())
        try:
            with backend.con.cursor() as cursor:
                cursor.execute("SET SESSION time_zone = '+01:00'")
            adapter = MySQLExecutionAdapter(backend)
            with pytest.raises(MaterializationError, match="verified UTC"):
                adapter.get_schema(name)
        finally:
            backend.disconnect()
