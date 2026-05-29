"""Tests for datasource table metadata inspection."""

from __future__ import annotations

import json
from pathlib import Path

import ibis
import pytest

import marivo.analysis as mv
from marivo.analysis.datasources.metadata import (
    ColumnMetadata,
    MetadataWarning,
    PartitionMetadata,
    TableMetadata,
)
from marivo.analysis.errors import DatasourceMetadataError


def test_table_metadata_to_dict_is_json_safe() -> None:
    metadata = TableMetadata(
        datasource="wh",
        table="orders",
        database=("analytics", "public"),
        backend_type="duckdb",
        comment="One row per order.",
        columns=(
            ColumnMetadata(
                name="order_id",
                type="int64",
                nullable=False,
                comment="Unique order id.",
                ordinal_position=1,
            ),
        ),
        partitions=(
            PartitionMetadata(
                name="order_date",
                type="date",
                transform="identity",
                comment="Date partition.",
            ),
        ),
        warnings=(
            MetadataWarning(
                kind="partitions_unavailable",
                message="partition metadata is not exposed",
            ),
        ),
    )

    payload = metadata.to_dict()

    assert payload["datasource"] == "wh"
    assert payload["database"] == ["analytics", "public"]
    assert payload["columns"][0]["nullable"] is False
    assert payload["partitions"][0]["name"] == "order_date"
    assert json.loads(json.dumps(payload))["warnings"][0]["kind"] == "partitions_unavailable"


@pytest.fixture
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _create_metadata_duckdb(path: Path) -> None:
    con = ibis.duckdb.connect(str(path))
    con.raw_sql(
        "CREATE TABLE orders ("
        "order_id INTEGER NOT NULL, "
        "amount DOUBLE, "
        "region VARCHAR, "
        "created_at TIMESTAMP)"
    )
    con.raw_sql("COMMENT ON TABLE orders IS 'One row per order'")
    con.raw_sql("COMMENT ON COLUMN orders.amount IS 'Gross order amount in USD'")
    con.raw_sql("COMMENT ON COLUMN orders.created_at IS 'Order creation timestamp'")
    con.disconnect()


def test_inspect_table_duckdb_returns_comments_and_nullable(project_root: Path) -> None:
    db_path = project_root / "warehouse.duckdb"
    _create_metadata_duckdb(db_path)
    mv.datasources.register("wh", backend_type="duckdb", path=str(db_path))

    metadata = mv.datasources.inspect_table("wh", table="orders")

    assert isinstance(metadata, TableMetadata)
    assert metadata.datasource == "wh"
    assert metadata.table == "orders"
    assert metadata.backend_type == "duckdb"
    assert metadata.comment == "One row per order"
    by_name = {column.name: column for column in metadata.columns}
    assert by_name["order_id"].nullable is False
    assert by_name["amount"].comment == "Gross order amount in USD"
    assert by_name["created_at"].comment == "Order creation timestamp"
    assert metadata.partitions == ()
    assert any(warning.kind == "partitions_unavailable" for warning in metadata.warnings)


def test_inspect_table_missing_datasource_raises(project_root: Path) -> None:
    with pytest.raises(DatasourceMetadataError) as exc_info:
        mv.datasources.inspect_table("missing", table="orders")

    assert exc_info.value.details["datasource"] == "missing"


class _FakeSchema(dict):
    def items(self):
        return super().items()


class _FakeTable:
    def __init__(self, schema: dict[str, str]) -> None:
        self._schema = _FakeSchema(schema)

    def schema(self):
        return self._schema


class _FakeCursor:
    def __init__(self, columns: list[str], rows: list[tuple[object, ...]]) -> None:
        self.description = [(column,) for column in columns]
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeBackend:
    def __init__(self, schema: dict[str, str], query_results: dict[str, _FakeCursor]) -> None:
        self.schema = schema
        self.query_results = query_results
        self.queries: list[str] = []

    def table(self, table: str, database: object = None):
        return _FakeTable(self.schema)

    def raw_sql(self, sql: str):
        self.queries.append(sql)
        for token, cursor in self.query_results.items():
            if token in sql:
                return cursor
        return _FakeCursor([], [])


def test_inspect_table_mysql_adapter_uses_information_schema(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MYSQL_USER", "reader")
    mv.datasources.register(
        "mysql_wh",
        backend_type="mysql",
        host="localhost",
        user_env="MYSQL_USER",
        database="mart",
    )
    backend = _FakeBackend(
        {"order_id": "int64", "amount": "float64"},
        {
            "information_schema.tables": _FakeCursor(
                ["TABLE_COMMENT"],
                [("One row per order",)],
            ),
            "SHOW FULL COLUMNS": _FakeCursor(
                ["Field", "Type", "Null", "Comment"],
                [
                    ("order_id", "bigint", "NO", "Unique order id"),
                    ("amount", "double", "YES", "Gross amount"),
                ],
            ),
        },
    )

    import marivo.analysis.datasources.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = mv.datasources.inspect_table("mysql_wh", table="orders")

    assert metadata.backend_type == "mysql"
    assert metadata.comment == "One row per order"
    by_name = {column.name: column for column in metadata.columns}
    assert by_name["order_id"].nullable is False
    assert by_name["order_id"].comment == "Unique order id"
    assert by_name["amount"].nullable is True
    assert any("SHOW FULL COLUMNS" in query for query in backend.queries)


def test_inspect_table_trino_adapter_uses_information_schema(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mv.datasources.register(
        "trino_wh",
        backend_type="trino",
        host="trino.example",
        catalog="hive",
        schema="analytics",
    )
    backend = _FakeBackend(
        {"order_id": "int64", "amount": "float64"},
        {
            "information_schema.tables": _FakeCursor(
                ["comment"],
                [("One row per order",)],
            ),
            "information_schema.columns": _FakeCursor(
                ["column_name", "data_type", "is_nullable", "comment", "ordinal_position"],
                [
                    ("order_id", "bigint", "NO", "Unique order id", 1),
                    ("amount", "double", "YES", "Gross amount", 2),
                ],
            ),
        },
    )

    import marivo.analysis.datasources.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = mv.datasources.inspect_table(
        "trino_wh",
        table="orders",
        database="analytics",
    )

    assert metadata.backend_type == "trino"
    assert metadata.database == "analytics"
    assert metadata.comment == "One row per order"
    by_name = {column.name: column for column in metadata.columns}
    assert by_name["amount"].comment == "Gross amount"
    assert by_name["amount"].nullable is True
    assert any("information_schema.columns" in query for query in backend.queries)


def test_inspect_table_clickhouse_adapter_uses_system_tables(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mv.datasources.register(
        "ch_wh",
        backend_type="clickhouse",
        host="clickhouse.example",
        database="analytics",
    )
    backend = _FakeBackend(
        {"order_id": "int64", "amount": "float64", "region": "string"},
        {
            "system.tables": _FakeCursor(
                ["comment"],
                [("One row per order",)],
            ),
            "system.columns": _FakeCursor(
                ["name", "type", "is_nullable", "comment", "position"],
                [
                    ("order_id", "Int64", 0, "Unique order id", 1),
                    ("amount", "Nullable(Float64)", 1, "Gross amount", 2),
                    ("region", "String", 0, "", 3),
                ],
            ),
        },
    )

    import marivo.analysis.datasources.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = mv.datasources.inspect_table("ch_wh", table="orders")

    assert metadata.backend_type == "clickhouse"
    assert metadata.database == "analytics"
    assert metadata.comment == "One row per order"
    by_name = {column.name: column for column in metadata.columns}
    assert by_name["order_id"].nullable is False
    assert by_name["order_id"].comment == "Unique order id"
    assert by_name["amount"].nullable is True
    assert by_name["amount"].comment == "Gross amount"
    assert by_name["region"].comment is None
    assert any("system.tables" in query for query in backend.queries)
    assert any("system.columns" in query for query in backend.queries)
    assert any(warning.kind == "partitions_unavailable" for warning in metadata.warnings)


def test_inspect_table_clickhouse_infers_nullable_from_type(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mv.datasources.register(
        "ch_old",
        backend_type="clickhouse",
        host="clickhouse-old.example",
        database="default",
    )
    backend = _FakeBackend(
        {"order_id": "int64", "amount": "float64"},
        {
            "system.tables": _FakeCursor(
                ["comment"],
                [("Orders table",)],
            ),
            "system.columns": _FakeCursor(
                ["name", "type", "comment", "position"],
                [
                    ("order_id", "Int64", "Primary key", 1),
                    ("amount", "Nullable(Float64)", "Order amount", 2),
                ],
            ),
        },
    )

    import marivo.analysis.datasources.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = mv.datasources.inspect_table("ch_old", table="orders")

    assert metadata.backend_type == "clickhouse"
    by_name = {column.name: column for column in metadata.columns}
    assert by_name["order_id"].nullable is False
    assert by_name["amount"].nullable is True
