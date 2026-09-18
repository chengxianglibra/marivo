"""Real source projections preserve necessary assertions and hidden state."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Literal
from uuid import uuid4

import pytest

from marivo.analysis.compiler.normalize import required_source_dependencies
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import MaterializationError, SourceSchemaError
from marivo.analysis.observation.predicates import gt
from marivo.datasource.ir import TableSourceIR
from marivo.refs import ref
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.validator import Registry
from tests.lazy_execution_fixtures import make_execution_registry
from tests.lazy_scalar_source_fixtures import registry_for
from tests.lazy_scalar_type_fixtures import source_writer
from tests.lazy_source_dependency_fixtures import capture_source_sql

pytestmark = pytest.mark.runtime
Engine = Literal["duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse"]


@contextmanager
def _source(
    engine: Engine, path: Path, monkeypatch: pytest.MonkeyPatch, variant: str
) -> Iterator[tuple[Registry, CompiledExpressionSidecar]]:
    if (
        engine not in {"duckdb", "sqlite"}
        and os.environ.get(f"MARIVO_{engine.upper()}_ANALYSIS_TEST") != "1"
    ):
        pytest.skip(f"opt-in {engine} service")
    name = "c1_" + uuid4().hex
    path = path / "source.db"
    if engine == "duckdb":
        registry, sidecar = make_execution_registry(path)
    elif engine == "postgres":
        from tests.lazy_postgres_fixtures import registry_for as pg_registry

        registry, sidecar = pg_registry(name, monkeypatch)
    else:
        registry, sidecar = registry_for(path, engine=engine, table=name)
        if engine in {"mysql", "clickhouse"}:
            from tests.multisource_environment.credentials import password

            monkeypatch.setenv(f"MARIVO_TEST_{engine.upper()}_PASSWORD", password())
    entity = registry.entities["sales.orders"]
    assert isinstance(entity.source, TableSourceIR)
    columns = tuple(
        (
            key,
            replace(binding, source="gross", data_type="float64")
            if key == "amount"
            else replace(binding, data_type="array<string>")
            if key == "tenant"
            else binding,
        )
        for key, binding in entity.source.columns
    )
    entity = replace(entity, source=replace(entity.source, table=name, columns=columns))
    entities = {**registry.entities, entity.semantic_id: entity}
    if variant == "relationship":
        customer = entities["sales.customers"]
        assert isinstance(customer.source, TableSourceIR)
        entities[customer.semantic_id] = replace(
            customer, source=replace(customer.source, table=name + "_customers")
        )
    registry = replace(registry, entities=entities)
    registry.freeze()
    unused_type = {
        "duckdb": "VARCHAR[]",
        "sqlite": "BLOB",
        "postgres": "JSONB",
        "mysql": "JSON",
        "trino": "ARRAY(VARCHAR)",
        "clickhouse": "Array(String)",
    }[engine]
    suffix = " ENGINE=MergeTree ORDER BY id" if engine == "clickhouse" else ""
    gross_type = "TEXT" if variant == "mismatch" and engine != "clickhouse" else "DOUBLE"
    gross = "" if variant == "missing" else f", gross {gross_type}"
    try:
        with source_writer(engine, path) as execute:
            execute(
                f"CREATE TABLE {name} (id BIGINT{gross}, weight DOUBLE, customer_id BIGINT, tenant {unused_type}, undocumented {unused_type}){suffix}"
            )
            if variant not in {"missing", "mismatch"}:
                execute(
                    f"INSERT INTO {name} (id,gross,weight,customer_id) VALUES (1,10,1,1),(2,20,2,2),(3,30,1,1)"
                )
                if variant == "duplicate":
                    execute(f"INSERT INTO {name} (id,gross,weight) VALUES (1,10,1)")
            if variant == "relationship":
                region_type = (
                    "TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin"
                    if engine == "mysql"
                    else "String"
                    if engine == "clickhouse"
                    else "TEXT"
                    if engine == "sqlite"
                    else "VARCHAR"
                )
                execute(
                    f"CREATE TABLE {name}_customers (id BIGINT, region {region_type}, gross {unused_type}){suffix}"
                )
                execute(f"INSERT INTO {name}_customers (id,region) VALUES (1,'a'),(2,'b')")
        yield registry, sidecar
    finally:
        with source_writer(engine, path) as execute:
            execute(f"DROP TABLE IF EXISTS {name}")
            if variant == "relationship":
                execute(f"DROP TABLE IF EXISTS {name}_customers")


@pytest.mark.parametrize("engine", ["duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse"])
def test_unused_declared_and_physical_columns(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _source(engine, tmp_path, monkeypatch, "valid") as (registry, sidecar):
        driver_sql = capture_source_sql(monkeypatch)
        runtime = DatasetRuntime.create(tmp_path / "project", "c1-projection")
        sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
        metrics = [
            ref.metric(f"sales.{name}")
            for name in ("revenue", "order_count", "mean_amount", "weighted_amount")
        ]
        target = sources.observe(metrics).aggregate()
        dependency = required_source_dependencies(target).entries[0]
        assert dependency.physical_columns == {"id", "gross", "weight"}
        result = target.execute()
        frame = result.to_pandas()
        assert frame.revenue.tolist() == [60.0]
        assert frame.order_count.tolist() == [3]
        assert frame.mean_amount.tolist() == [20.0]
        assert frame.weighted_amount.tolist() == [20.0]
        assert runtime.statistics.transferred_rows == 1
        assert runtime.statistics.transferred_bytes > 0
        submitted = [sql for sql in driver_sql() if "SELECT" in sql.upper()]
        assert submitted
        assert all("tenant" not in sql and "undocumented" not in sql for sql in submitted)
        assert any("gross" in sql for sql in submitted)
        assert target.execute().state.artifact_ref == result.state.artifact_ref
        assert runtime.statistics.primary_queries == 0


@pytest.mark.parametrize("variant", ["missing", "mismatch", "duplicate"])
@pytest.mark.parametrize("engine", ["duckdb", "sqlite"])
def test_necessary_columns_fail_even_for_empty_output(
    engine: Engine, variant: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _source(engine, tmp_path, monkeypatch, variant) as (registry, sidecar):
        runtime = DatasetRuntime.create(tmp_path / "project", "c1-negative")
        target = (
            runtime.sources(semantic_registry=registry, sidecar=sidecar)
            .observe(ref.metric("sales.revenue"))
            .where(gt(ref.metric("sales.revenue"), 100000))
            .aggregate()
        )
        with pytest.raises(MaterializationError) as caught:
            target.execute()
        if variant != "duplicate":
            error = caught.value
            assert isinstance(error, SourceSchemaError)
            assert error.reason == ("missing_column" if variant == "missing" else "type_mismatch")
            assert error.logical_column == "amount" and error.physical_column == "gross"
            assert error.entity_ref == "sales.orders"


@pytest.mark.parametrize("engine", ["duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse"])
def test_same_named_column_on_another_relation_stays_unused(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _source(engine, tmp_path, monkeypatch, "relationship") as (registry, sidecar):
        runtime = DatasetRuntime.create(tmp_path / "project", "c1-relation")
        target = (
            runtime.sources(semantic_registry=registry, sidecar=sidecar)
            .observe(ref.metric("sales.revenue"))
            .with_dimensions(ref.dimension("sales.customers.region"))
            .aggregate()
        )
        dependencies = {
            entry.entity.ref.path: entry for entry in required_source_dependencies(target).entries
        }
        assert dependencies["sales.orders"].physical_columns == {"id", "customer_id", "gross"}
        assert dependencies["sales.customers"].physical_columns == {"id", "region"}
        frame = target.execute().to_pandas()
        assert dict(zip(frame.region, frame.revenue, strict=True)) == {"a": 40.0, "b": 20.0}


@pytest.mark.skipif(
    os.environ.get("MARIVO_POSTGRES_ANALYSIS_TEST") != "1", reason="opt-in PostgreSQL service"
)
def test_postgres_reader_column_comments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from marivo.datasource.backends import _build_backend_from_effective, _effective_kwargs
    from marivo.datasource.engines.postgres import _inspect_postgres
    from tests.multisource_environment import postgres_analysis as pg

    with _source("postgres", tmp_path, monkeypatch, "valid") as (registry, sidecar):
        entity = registry.entities["sales.orders"]
        assert isinstance(entity.source, TableSourceIR)
        name = entity.source.table
        with pg.connection(admin=True) as admin:
            admin.execute(f"COMMENT ON COLUMN {name}.gross IS 'Gross order amount'")
            admin.execute(f"COMMENT ON COLUMN {name}.tenant IS 'Unused source context'")
        datasource = next(iter(registry.datasources.values()))
        backend = _build_backend_from_effective(
            datasource, _effective_kwargs(datasource), read_only=True
        ).backend
        try:
            metadata = _inspect_postgres(
                datasource=datasource.name,
                backend=backend,
                table=name,
                database="public",
                table_expr=backend.table(name, database="public"),
                include_partitions=False,
                default_schema=None,
            )
            comments = {column.name: column.comment for column in metadata.columns}
            assert comments["gross"] == "Gross order amount"
            assert comments["tenant"] == "Unused source context"
            assert comments["weight"] is None
        finally:
            backend.disconnect()
