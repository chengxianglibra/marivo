"""Schema requests exclude unrelated types before each adapter's mapper runs."""

from pathlib import Path
from types import SimpleNamespace

import ibis.expr.datatypes as dt
import pytest

from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.compiler.normalize import required_source_dependencies
from marivo.analysis.materialization.clickhouse_execution import ClickHouseExecutionAdapter
from marivo.analysis.materialization.duckdb_execution import DuckDBExecutionAdapter
from marivo.analysis.materialization.errors import MaterializationError, SourceSchemaError
from marivo.analysis.materialization.mysql_execution import MySQLExecutionAdapter
from marivo.analysis.materialization.postgres_execution import PostgresExecutionAdapter
from marivo.analysis.materialization.scalar_sql_execution import ScalarExecutionAdapter
from marivo.analysis.materialization.sqlite_execution import SQLiteExecutionAdapter
from marivo.analysis.materialization.trino_execution import TrinoExecutionAdapter
from marivo.analysis.session._lazy_sources import make_lazy_sources
from marivo.refs import ref
from tests.lazy_execution_fixtures import make_execution_registry
from tests.lazy_observation_fixtures import NoIoActionPort


@pytest.mark.parametrize(
    "adapter_type",
    [
        DuckDBExecutionAdapter,
        PostgresExecutionAdapter,
        MySQLExecutionAdapter,
        SQLiteExecutionAdapter,
        TrinoExecutionAdapter,
        ClickHouseExecutionAdapter,
    ],
)
@pytest.mark.parametrize("invalid", [False, True])
def test_mapper_sees_only_requested_relation_columns(
    adapter_type: type, invalid: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry, sidecar = make_execution_registry(Path("must-not-open"))
    sources = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="schema",
        store_id="schema",
    )
    target = sources.observe(ref.metric("sales.revenue")).aggregate()
    dependency = required_source_dependencies(target).entries[0]
    parsed: list[str] = []

    class Mapper:
        @staticmethod
        def from_string(value: str, *, nullable: bool = True) -> dt.DataType:
            parsed.append(value)
            if value == "unparseable_unused":
                raise AssertionError("unrelated physical type reached the mapper")
            return (dt.int64 if value == "BIGINT" else dt.float64).copy(nullable=nullable)

    native = SimpleNamespace(
        compiler=SimpleNamespace(type_mapper=Mapper),
        con=SimpleNamespace(database="main", catalog="iceberg", schema="analysis"),
    )
    adapter = object.__new__(adapter_type)
    for attribute in ("_backend", "_mysql", "_clickhouse", "_trino"):
        monkeypatch.setattr(adapter, attribute, native, raising=False)
    if adapter_type is PostgresExecutionAdapter:
        rows = [
            ("id", "BIGINT", True),
            ("amount", "DOUBLE", True),
            ("region", "unparseable_unused", True),
        ]
    elif adapter_type is DuckDBExecutionAdapter:
        rows = [
            ("id", "BIGINT", "YES"),
            ("amount", "DOUBLE", "YES"),
            ("region", "unparseable_unused", "YES"),
        ]
    elif adapter_type is MySQLExecutionAdapter:
        rows = [
            ("id", "BIGINT", "YES", None),
            ("amount", "DOUBLE", "YES", None),
            ("region", "unparseable_unused", "YES", None),
        ]
    elif adapter_type is ClickHouseExecutionAdapter:
        rows = [("id", "Int64"), ("amount", "Float64"), ("region", "unparseable_unused")]
    else:
        rows = [("id", "BIGINT"), ("amount", "DOUBLE"), ("region", "unparseable_unused")]
    if invalid:
        rows[0] = (rows[0][0], "unparseable_unused", *rows[0][2:])
    iterator = iter(rows)

    def statement(sql: str, **kwargs: object) -> str:
        return sql

    def scalar(sql: str, **kwargs: object) -> object:
        if "sqlite_schema" in sql:
            return "CREATE TABLE orders (id BIGINT, amount DOUBLE, region BLOB)"
        if "join_use_nulls" in sql:
            return "1"
        if "connector_name" in sql:
            return "iceberg"
        if "table_type" in sql:
            return "VIEW"
        if "SELECT engine" in sql:
            return "MergeTree"
        if "DATABASE()" in sql:
            return "main"
        return 0

    def submit(sql: str) -> SimpleNamespace:
        if "ENGINE,TABLE_TYPE" in sql:
            return SimpleNamespace(fetchone=lambda: ("InnoDB", "BASE TABLE"))
        return SimpleNamespace(fetchone=lambda: next(iterator, None))

    monkeypatch.setattr(adapter, "statement", statement)
    monkeypatch.setattr(adapter, "submit", submit)
    monkeypatch.setattr(adapter, "read_scalar", scalar)
    if invalid:
        with pytest.raises(SourceSchemaError) as caught:
            adapter.get_schema("orders", dependency=dependency)
        assert caught.value.reason == "unsupported_physical_type"
        assert caught.value.physical_column == "id"
        assert caught.value.entity_ref == "sales.orders"
        return
    schema = adapter.get_schema("orders", dependency=dependency)
    assert set(schema.names) == {"id", "amount"}
    assert "unparseable_unused" not in parsed
    with pytest.raises(DatasetCompilationError, match="schema request mismatch"):
        adapter.get_schema("another_relation", dependency=dependency)


class _MappingMapper:
    """Native type mapper stand-in for the physical names the stub tests use."""

    @staticmethod
    def from_string(value: str, *, nullable: bool = True) -> dt.DataType:
        mapping: dict[str, dt.DataType] = {
            "BIGINT": dt.int64,
            "Int64": dt.int64,
            "DOUBLE": dt.float64,
            "Float64": dt.float64,
        }
        return mapping[value].copy(nullable=nullable)


def _stub_adapter(
    adapter_type: type, native: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> ScalarExecutionAdapter:
    adapter: ScalarExecutionAdapter = object.__new__(adapter_type)
    for attribute in ("_backend", "_mysql", "_clickhouse", "_trino"):
        monkeypatch.setattr(adapter, attribute, native, raising=False)
    monkeypatch.setattr(adapter, "_run_ref", None, raising=False)
    return adapter


def _stub_statements(
    monkeypatch: pytest.MonkeyPatch,
    adapter: ScalarExecutionAdapter,
    scalars: dict[str, object],
    rows: list[tuple[object, ...]],
    *,
    metadata_row: tuple[object, ...] | None = None,
) -> None:
    """Attach statement/submit/read_scalar stubs keyed on SQL substrings."""

    iterator = iter(rows)

    def statement(sql: str, **kwargs: object) -> str:
        return sql

    def scalar(sql: str, **kwargs: object) -> object:
        for fragment, value in scalars.items():
            if fragment in sql:
                return value
        raise AssertionError(f"unexpected scalar probe: {sql}")

    def submit(sql: str) -> SimpleNamespace:
        if "ENGINE,TABLE_TYPE" in sql:
            return SimpleNamespace(fetchone=lambda: metadata_row)
        return SimpleNamespace(fetchone=lambda: next(iterator, None))

    monkeypatch.setattr(adapter, "statement", statement)
    monkeypatch.setattr(adapter, "submit", submit)
    monkeypatch.setattr(adapter, "read_scalar", scalar)


@pytest.mark.parametrize("engine", ["Distributed(cluster, db, orders, rand())", "View"])
def test_clickhouse_accepts_any_engine_form(engine: str, monkeypatch: pytest.MonkeyPatch) -> None:
    native = SimpleNamespace(
        compiler=SimpleNamespace(type_mapper=_MappingMapper()),
        con=SimpleNamespace(database="analytics", catalog=None, schema=None),
    )
    adapter = _stub_adapter(ClickHouseExecutionAdapter, native, monkeypatch)
    _stub_statements(
        monkeypatch,
        adapter,
        {"join_use_nulls": "1", "SELECT engine": engine, "count": 0},
        [("id", "Int64"), ("amount", "Float64")],
    )
    schema = adapter.get_schema("orders", database="analytics")
    assert set(schema.names) == {"id", "amount"}


def test_clickhouse_rejects_missing_relation_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    native = SimpleNamespace(
        compiler=SimpleNamespace(type_mapper=_MappingMapper()),
        con=SimpleNamespace(database="analytics", catalog=None, schema=None),
    )
    adapter = _stub_adapter(ClickHouseExecutionAdapter, native, monkeypatch)
    _stub_statements(
        monkeypatch,
        adapter,
        {"join_use_nulls": "1", "SELECT engine": None, "count": 0},
        [],
    )
    with pytest.raises(MaterializationError, match=r"no system\.tables engine value"):
        adapter.get_schema("orders", database="analytics")


def test_trino_accepts_view_on_non_iceberg_connector(monkeypatch: pytest.MonkeyPatch) -> None:
    native = SimpleNamespace(
        compiler=SimpleNamespace(type_mapper=_MappingMapper()),
        con=SimpleNamespace(database=None, catalog="tpch", schema="analysis"),
    )
    adapter = _stub_adapter(TrinoExecutionAdapter, native, monkeypatch)
    _stub_statements(
        monkeypatch,
        adapter,
        {"connector_name": "tpch", "table_type": "VIEW", "count": 0},
        [("id", "BIGINT"), ("amount", "DOUBLE")],
    )
    schema = adapter.get_schema("orders", database="analysis")
    assert set(schema.names) == {"id", "amount"}


def test_trino_rejects_dollar_internal_table(monkeypatch: pytest.MonkeyPatch) -> None:
    native = SimpleNamespace(
        compiler=SimpleNamespace(type_mapper=_MappingMapper()),
        con=SimpleNamespace(database=None, catalog="iceberg", schema="analysis"),
    )
    adapter = _stub_adapter(TrinoExecutionAdapter, native, monkeypatch)
    _stub_statements(
        monkeypatch,
        adapter,
        {"connector_name": "iceberg", "table_type": "VIEW", "count": 0},
        [],
    )
    with pytest.raises(MaterializationError, match=r"\$-suffixed internal"):
        adapter.get_schema("orders$partitions", database="analysis")


def test_mysql_accepts_view_without_innodb_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    native = SimpleNamespace(
        compiler=SimpleNamespace(type_mapper=_MappingMapper()),
        con=SimpleNamespace(database=None, catalog=None, schema=None),
    )
    adapter = _stub_adapter(MySQLExecutionAdapter, native, monkeypatch)
    _stub_statements(
        monkeypatch,
        adapter,
        {"DATABASE()": "main", "count": 0},
        [("id", "BIGINT", "YES", None), ("amount", "DOUBLE", "YES", None)],
        metadata_row=(None, "VIEW"),
    )
    schema = adapter.get_schema("orders")
    assert set(schema.names) == {"id", "amount"}


def test_mysql_rejects_missing_relation_row(monkeypatch: pytest.MonkeyPatch) -> None:
    native = SimpleNamespace(
        compiler=SimpleNamespace(type_mapper=_MappingMapper()),
        con=SimpleNamespace(database=None, catalog=None, schema=None),
    )
    adapter = _stub_adapter(MySQLExecutionAdapter, native, monkeypatch)
    _stub_statements(
        monkeypatch,
        adapter,
        {"DATABASE()": "main", "count": 0},
        [],
        metadata_row=None,
    )
    with pytest.raises(MaterializationError, match=r"no information_schema\.tables row"):
        adapter.get_schema("orders")


def test_sqlite_accepts_view_definition(monkeypatch: pytest.MonkeyPatch) -> None:
    native = SimpleNamespace(
        compiler=SimpleNamespace(type_mapper=_MappingMapper()),
        con=SimpleNamespace(database="main", catalog=None, schema=None),
    )
    adapter = _stub_adapter(SQLiteExecutionAdapter, native, monkeypatch)
    _stub_statements(
        monkeypatch,
        adapter,
        {
            "type IN ('table','view')": "CREATE VIEW orders AS SELECT id, amount FROM base_orders",
            "count": 0,
        },
        [("id", "BIGINT"), ("amount", "DOUBLE")],
    )
    schema = adapter.get_schema("orders")
    assert set(schema.names) == {"id", "amount"}
