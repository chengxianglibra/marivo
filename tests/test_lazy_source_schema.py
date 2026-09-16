"""Schema requests exclude unrelated types before each adapter's mapper runs."""

from pathlib import Path
from types import SimpleNamespace

import ibis.expr.datatypes as dt
import pytest

from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.compiler.normalize import required_source_dependencies
from marivo.analysis.materialization.clickhouse_execution import ClickHouseExecutionAdapter
from marivo.analysis.materialization.duckdb_execution import DuckDBExecutionAdapter
from marivo.analysis.materialization.errors import SourceSchemaError
from marivo.analysis.materialization.mysql_execution import MySQLExecutionAdapter
from marivo.analysis.materialization.postgres_execution import PostgresExecutionAdapter
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
            return "BASE TABLE"
        if "ENGINE" in sql:
            return "InnoDB"
        if "SELECT engine" in sql:
            return "MergeTree"
        if "DATABASE()" in sql:
            return "main"
        return 0

    monkeypatch.setattr(adapter, "statement", statement)
    monkeypatch.setattr(
        adapter, "submit", lambda sql: SimpleNamespace(fetchone=lambda: next(iterator, None))
    )
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
