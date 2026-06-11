"""Tests for datasource table metadata inspection."""

from __future__ import annotations

import json
from pathlib import Path

import ibis
import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo.datasource.errors import DatasourceMetadataError
from marivo.datasource.metadata import (
    ColumnMetadata,
    MetadataWarning,
    PartitionMetadata,
    TableMetadata,
)


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


def test_table_metadata_to_dict_includes_view_fields() -> None:
    base = TableMetadata(
        datasource="wh",
        table="orders",
        database=None,
        backend_type="duckdb",
        comment=None,
        columns=(),
        partitions=(),
        warnings=(),
    )
    assert base.is_view is False
    assert base.view_definition is None
    assert base.to_dict()["is_view"] is False
    assert base.to_dict()["view_definition"] is None

    view = TableMetadata(
        datasource="wh",
        table="v_orders",
        database=None,
        backend_type="duckdb",
        comment=None,
        columns=(),
        partitions=(),
        warnings=(),
        is_view=True,
        view_definition="SELECT order_id FROM orders",
    )
    payload = view.to_dict()
    assert payload["is_view"] is True
    assert payload["view_definition"] == "SELECT order_id FROM orders"
    assert json.loads(json.dumps(payload))["is_view"] is True


@pytest.fixture
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _spec(name: str, *, backend_type: str, **fields: object) -> md.DatasourceSpec:
    return md.DatasourceSpec(name=name, backend_type=backend_type, **fields)


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


def _create_duckdb_with_view(path: Path) -> None:
    con = ibis.duckdb.connect(str(path))
    con.raw_sql("CREATE TABLE orders (order_id INTEGER NOT NULL, amount DOUBLE)")
    con.raw_sql("CREATE VIEW v_orders AS SELECT order_id, amount FROM orders")
    con.disconnect()


def _create_duckdb_with_same_name_table_and_view(path: Path) -> None:
    con = ibis.duckdb.connect(str(path))
    con.raw_sql("CREATE SCHEMA base_schema")
    con.raw_sql("CREATE SCHEMA view_schema")
    con.raw_sql("CREATE TABLE base_schema.orders (order_id INTEGER NOT NULL, amount DOUBLE)")
    con.raw_sql("CREATE VIEW view_schema.orders AS SELECT order_id, amount FROM base_schema.orders")
    con.disconnect()


def _create_duckdb_with_default_table_and_same_name_view(path: Path) -> None:
    con = ibis.duckdb.connect(str(path))
    con.raw_sql("CREATE SCHEMA view_schema")
    con.raw_sql("CREATE TABLE orders (order_id INTEGER NOT NULL, amount DOUBLE)")
    con.raw_sql("CREATE VIEW view_schema.orders AS SELECT order_id, amount FROM main.orders")
    con.disconnect()


def test_inspect_table_duckdb_returns_comments_and_nullable(project_root: Path) -> None:
    db_path = project_root / "warehouse.duckdb"
    _create_metadata_duckdb(db_path)
    md.register(_spec("wh", backend_type="duckdb", path=str(db_path)))

    metadata = md.inspect_table("wh", table="orders")

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


def test_inspect_source_duckdb_detects_view(project_root: Path) -> None:
    db_path = project_root / "warehouse.duckdb"
    _create_duckdb_with_view(db_path)
    md.register(_spec("wh", backend_type="duckdb", path=str(db_path)))

    view_md = md.inspect_source("wh", source=ms.table("v_orders"))
    assert view_md.is_view is True
    assert view_md.view_definition is not None
    assert "SELECT" in view_md.view_definition.upper()

    base_md = md.inspect_source("wh", source=ms.table("orders"))
    assert base_md.is_view is False
    assert base_md.view_definition is None


def test_inspect_source_duckdb_uses_database_for_view_detection(
    project_root: Path,
) -> None:
    db_path = project_root / "warehouse.duckdb"
    _create_duckdb_with_same_name_table_and_view(db_path)
    md.register(_spec("wh", backend_type="duckdb", path=str(db_path)))

    table_md = md.inspect_table(
        "wh",
        table="orders",
        database="base_schema",
    )
    assert table_md.is_view is False
    assert table_md.view_definition is None

    source_table_md = md.inspect_source(
        "wh",
        source=ms.table("orders", database="base_schema"),
    )
    assert source_table_md.is_view is False
    assert source_table_md.view_definition is None

    view_md = md.inspect_source(
        "wh",
        source=ms.table("orders", database="view_schema"),
    )
    assert view_md.is_view is True
    assert view_md.view_definition is not None
    assert "BASE_SCHEMA.ORDERS" in view_md.view_definition.upper()


def test_inspect_table_duckdb_unqualified_uses_default_schema_for_view_detection(
    project_root: Path,
) -> None:
    db_path = project_root / "warehouse.duckdb"
    _create_duckdb_with_default_table_and_same_name_view(db_path)
    md.register(_spec("wh", backend_type="duckdb", path=str(db_path)))

    table_md = md.inspect_table("wh", table="orders")
    assert table_md.is_view is False
    assert table_md.view_definition is None

    view_md = md.inspect_source(
        "wh",
        source=ms.table("orders", database="view_schema"),
    )
    assert view_md.is_view is True
    assert view_md.view_definition is not None
    assert "MAIN.ORDERS" in view_md.view_definition.upper()


def test_inspect_table_missing_datasource_raises(project_root: Path) -> None:
    with pytest.raises(DatasourceMetadataError) as exc_info:
        md.inspect_table("missing", table="orders")

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


class _FakeQueryResult:
    """Mimics clickhouse_connect.QueryResult (column_names + result_rows, no description/fetchall)."""

    def __init__(
        self, column_names: tuple[str, ...], result_rows: list[tuple[object, ...]]
    ) -> None:
        self.column_names = column_names
        self.result_rows = result_rows


class _FakeBackend:
    def __init__(
        self,
        schema: dict[str, str],
        query_results: dict[str, _FakeCursor | _FakeQueryResult],
        sequential_results: list[_FakeCursor | _FakeQueryResult] | None = None,
        raise_on_tokens: list[str] | None = None,
    ) -> None:
        self.schema = schema
        self.query_results = query_results
        self.sequential_results: list[_FakeCursor | _FakeQueryResult] = list(
            sequential_results or []
        )
        self.raise_on_tokens: list[str] = list(raise_on_tokens or [])
        self.queries: list[str] = []
        self.table_calls: list[tuple[str, object]] = []

    def table(self, table: str, database: object = None):
        self.table_calls.append((table, database))
        return _FakeTable(self.schema)

    def raw_sql(self, sql: str):
        self.queries.append(sql)
        for token in self.raise_on_tokens:
            if token in sql:
                raise Exception(f"Missing columns: {token!r}")
        if self.sequential_results:
            return self.sequential_results.pop(0)
        for token, cursor in self.query_results.items():
            if token in sql:
                return cursor
        return _FakeCursor([], [])


class _FakeFileBackend:
    def __init__(self) -> None:
        self.reads: list[tuple[str, dict[str, object]]] = []

    def read_parquet(self, path: str, **options: object):
        self.reads.append((path, options))
        return _FakeTable({"order_id": "int64", "amount": "float64"})


def test_inspect_table_mysql_adapter_uses_information_schema(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MYSQL_USER", "reader")
    md.register(
        _spec(
            "mysql_wh",
            backend_type="mysql",
            host="localhost",
            user_env="MYSQL_USER",
            database="mart",
        )
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

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = md.inspect_table("mysql_wh", table="mart.orders")

    assert metadata.backend_type == "mysql"
    assert metadata.comment == "One row per order"
    by_name = {column.name: column for column in metadata.columns}
    assert by_name["order_id"].nullable is False
    assert by_name["order_id"].comment == "Unique order id"
    assert by_name["amount"].nullable is True
    assert any("SHOW FULL COLUMNS" in query for query in backend.queries)


def test_inspect_table_mysql_uses_datasource_database_for_view_detection(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MYSQL_USER", "reader")
    md.register(
        _spec(
            "mysql_wh",
            backend_type="mysql",
            host="localhost",
            user_env="MYSQL_USER",
            database="mart",
        )
    )
    backend = _FakeBackend(
        {"order_id": "int64"},
        {},
        sequential_results=[
            _FakeCursor(["TABLE_COMMENT"], [("View of orders",)]),
            _FakeCursor(
                ["Field", "Type", "Null", "Comment"],
                [("order_id", "bigint", "NO", "Unique order id")],
            ),
            _FakeCursor(["TABLE_TYPE"], [("VIEW",)]),
            _FakeCursor(["VIEW_DEFINITION"], [("select order_id from mart.orders",)]),
        ],
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = md.inspect_table("mysql_wh", table="v_orders")

    assert metadata.backend_type == "mysql"
    assert metadata.database is None
    assert metadata.is_view is True
    assert metadata.view_definition == "select order_id from mart.orders"
    assert any(
        "SELECT TABLE_TYPE FROM information_schema.tables" in query
        and "table_schema = 'mart'" in query
        for query in backend.queries
    )
    assert any(
        "SELECT VIEW_DEFINITION FROM information_schema.views" in query
        and "table_schema = 'mart'" in query
        for query in backend.queries
    )


def test_inspect_source_file_derives_table_name_from_path(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    md.register(_spec("duck_wh", backend_type="duckdb", path=":memory:"))
    backend = _FakeFileBackend()

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = md.inspect_source(
        "duck_wh",
        source=ms.file("/data/orders/*.parquet", format="parquet", hive_partitioning=True),
    )

    assert metadata.table == "orders"
    assert backend.reads == [("/data/orders/*.parquet", {"hive_partitioning": True})]


def test_inspect_table_trino_adapter_uses_information_schema(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    md.register(
        _spec(
            "trino_wh",
            backend_type="trino",
            host="trino.example",
            catalog="hive",
            schema="analytics",
        )
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

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = md.inspect_table(
        "trino_wh",
        table="orders",
        database="analytics",
    )

    assert metadata.backend_type == "trino"
    assert metadata.table == "orders"
    assert metadata.database == "analytics"
    assert metadata.comment == "One row per order"
    assert backend.table_calls == [("orders", "analytics")]
    by_name = {column.name: column for column in metadata.columns}
    assert by_name["amount"].comment == "Gross amount"
    assert by_name["amount"].nullable is True
    assert any("table_catalog = 'hive'" in query for query in backend.queries)
    assert any("table_schema = 'analytics'" in query for query in backend.queries)
    assert any("table_name = 'orders'" in query for query in backend.queries)
    assert any("information_schema.columns" in query for query in backend.queries)


def test_inspect_table_trino_detects_view_definition(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    md.register(
        _spec(
            "trino_wh",
            backend_type="trino",
            host="trino.example",
            catalog="hive",
            schema="analytics",
        )
    )
    backend = _FakeBackend(
        {"order_id": "int64"},
        {},
        sequential_results=[
            _FakeCursor(["comment"], [("View of orders",)]),
            _FakeCursor(
                ["column_name", "data_type", "is_nullable", "comment", "ordinal_position"],
                [("order_id", "bigint", "NO", "Unique order id", 1)],
            ),
            _FakeCursor(["table_type"], [("VIEW",)]),
            _FakeCursor(["view_definition"], [("SELECT order_id FROM analytics.orders",)]),
        ],
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = md.inspect_table("trino_wh", table="v_orders")

    assert metadata.backend_type == "trino"
    assert metadata.is_view is True
    assert metadata.view_definition == "SELECT order_id FROM analytics.orders"
    assert any(
        "SELECT table_type FROM information_schema.tables" in query
        and "table_schema = 'analytics'" in query
        for query in backend.queries
    )
    assert any(
        "SELECT view_definition FROM information_schema.views" in query
        and "table_schema = 'analytics'" in query
        for query in backend.queries
    )


def test_inspect_table_trino_uses_datasource_schema_when_database_omitted(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    md.register(
        _spec(
            "trino_wh",
            backend_type="trino",
            host="trino.example",
            catalog="hive",
            schema="analytics",
        )
    )
    backend = _FakeBackend(
        {"order_id": "int64"},
        {
            "information_schema.tables": _FakeCursor(["comment"], [("Orders",)]),
            "information_schema.columns": _FakeCursor(
                ["column_name", "data_type", "is_nullable", "comment", "ordinal_position"],
                [("order_id", "bigint", "NO", "Unique order id", 1)],
            ),
        },
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = md.inspect_table("trino_wh", table="orders")

    assert metadata.backend_type == "trino"
    assert metadata.database is None
    assert backend.table_calls == [("orders", None)]
    assert metadata.comment == "Orders"
    assert any("table_catalog = 'hive'" in query for query in backend.queries)
    assert any("table_schema = 'analytics'" in query for query in backend.queries)
    assert any("table_name = 'orders'" in query for query in backend.queries)


def test_inspect_table_trino_without_schema_returns_schema_only(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    md.register(_spec("trino_wh", backend_type="trino", host="trino.example", catalog="hive"))
    backend = _FakeBackend({"order_id": "int64"}, {})

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = md.inspect_table("trino_wh", table="orders")

    assert metadata.backend_type == "trino"
    assert metadata.columns[0].name == "order_id"
    assert backend.table_calls == [("orders", None)]
    assert backend.queries == []
    assert any(warning.kind == "schema_only_fallback" for warning in metadata.warnings)


def test_inspect_table_clickhouse_adapter_uses_system_tables(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    md.register(
        _spec(
            "ch_wh",
            backend_type="clickhouse",
            host="clickhouse.example",
            database="analytics",
        )
    )
    backend = _FakeBackend(
        {"order_id": "int64", "amount": "float64", "region": "string", "created_at": "timestamp"},
        {
            "system.tables": _FakeCursor(
                ["comment", "partition_key", "engine", "engine_full"],
                [("One row per order", "toYYYYMM(created_at)", "MergeTree", "")],
            ),
            "system.columns": _FakeCursor(
                ["name", "type", "is_nullable", "comment", "position"],
                [
                    ("order_id", "Int64", 0, "Unique order id", 1),
                    ("amount", "Nullable(Float64)", 1, "Gross amount", 2),
                    ("region", "String", 0, "", 3),
                    ("created_at", "DateTime", 0, "", 4),
                ],
            ),
        },
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = md.inspect_table("ch_wh", table="analytics.orders")

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
    assert not any(warning.kind == "partitions_unavailable" for warning in metadata.warnings)
    assert len(metadata.partitions) == 1
    assert metadata.partitions[0].name == "created_at"
    assert metadata.partitions[0].transform == "toYYYYMM"
    assert metadata.partitions[0].type == "DateTime"


@pytest.mark.parametrize("engine", ["View", "MaterializedView"])
def test_inspect_table_clickhouse_detects_view_definition(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine: str,
) -> None:
    md.register(
        _spec("ch_view", backend_type="clickhouse", host="clickhouse.example", database="analytics")
    )
    backend = _FakeBackend(
        {"order_id": "int64"},
        {},
        sequential_results=[
            _FakeCursor(
                ["comment", "partition_key", "engine", "engine_full"],
                [("View of orders", "", engine, "")],
            ),
            _FakeCursor(
                ["name", "type", "is_nullable", "comment", "position"],
                [("order_id", "Int64", 0, "Unique order id", 1)],
            ),
            _FakeCursor(
                ["create_table_query"],
                [(f"CREATE {engine} analytics.v_orders AS SELECT order_id FROM orders",)],
            ),
        ],
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = md.inspect_table("ch_view", table="v_orders")

    assert metadata.backend_type == "clickhouse"
    assert metadata.is_view is True
    assert metadata.view_definition == (
        f"CREATE {engine} analytics.v_orders AS SELECT order_id FROM orders"
    )
    assert any("SELECT create_table_query FROM system.tables" in query for query in backend.queries)


def test_inspect_table_clickhouse_infers_nullable_from_type(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    md.register(
        _spec(
            "ch_old",
            backend_type="clickhouse",
            host="clickhouse-old.example",
            database="default",
        )
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

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = md.inspect_table("ch_old", table="default.orders")

    assert metadata.backend_type == "clickhouse"
    by_name = {column.name: column for column in metadata.columns}
    assert by_name["order_id"].nullable is False
    assert by_name["amount"].nullable is True


def test_inspect_table_clickhouse_query_result(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ClickHouse raw_sql returns QueryResult (not DB-API cursor) — _cursor_rows must handle it."""
    md.register(
        _spec(
            "ch_qr",
            backend_type="clickhouse",
            host="clickhouse.example",
            database="analytics",
        )
    )
    backend = _FakeBackend(
        {"order_id": "int64", "amount": "float64", "region": "string"},
        {
            "system.tables": _FakeQueryResult(
                ("comment",),
                [("One row per order",)],
            ),
            "system.columns": _FakeQueryResult(
                ("name", "type", "is_nullable", "comment", "position"),
                [
                    ("order_id", "Int64", 0, "Unique order id", 1),
                    ("amount", "Nullable(Float64)", 1, "Gross amount", 2),
                    ("region", "String", 0, "", 3),
                ],
            ),
        },
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = md.inspect_table("ch_qr", table="analytics.orders")

    assert metadata.backend_type == "clickhouse"
    assert metadata.comment == "One row per order"
    by_name = {column.name: column for column in metadata.columns}
    assert by_name["order_id"].nullable is False
    assert by_name["order_id"].comment == "Unique order id"
    assert by_name["amount"].nullable is True
    assert by_name["amount"].comment == "Gross amount"
    assert by_name["region"].comment is None


def test_inspect_table_clickhouse_no_is_nullable_empty_comments(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ClickHouse ≤22.5: is_nullable missing, comment column exists but values are empty strings."""
    md.register(
        _spec(
            "ch_22_3",
            backend_type="clickhouse",
            host="clickhouse-old.example",
            database="bilibili_web_monitor",
        )
    )
    backend = _FakeBackend(
        {"event_id": "string", "lag_count": "float64"},
        {
            "system.tables": _FakeQueryResult(
                ("comment",),
                [("",)],
            ),
            "system.columns": _FakeQueryResult(
                ("name", "type", "comment", "position"),
                [
                    ("event_id", "String", "", 1),
                    ("lag_count", "Nullable(Float64)", "", 2),
                ],
            ),
        },
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = md.inspect_table("ch_22_3", table="bilibili_web_monitor.ads_web_main_box_rt")

    assert metadata.backend_type == "clickhouse"
    assert metadata.comment is None
    by_name = {column.name: column for column in metadata.columns}
    assert by_name["event_id"].type == "String"
    assert by_name["event_id"].nullable is False
    assert by_name["event_id"].comment is None
    assert by_name["lag_count"].type == "Nullable(Float64)"
    assert by_name["lag_count"].nullable is True
    assert by_name["lag_count"].comment is None
    assert any(warning.kind == "comments_unavailable" for warning in metadata.warnings)


def test_inspect_table_clickhouse_partition_key_parsed(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MergeTree with toYYYYMMDD(time_iso) partition — transform extracted."""
    md.register(_spec("ch_pk", backend_type="clickhouse", host="ch.example", database="analytics"))
    backend = _FakeBackend(
        {"event_id": "string", "time_iso": "datetime", "lag_count": "float64"},
        {
            "system.tables": _FakeQueryResult(
                ("comment", "partition_key", "engine", "engine_full"),
                [("", "toYYYYMMDD(time_iso)", "ReplicatedMergeTree", "")],
            ),
            "system.columns": _FakeQueryResult(
                ("name", "type", "is_nullable", "comment", "position"),
                [
                    ("event_id", "String", 0, "", 1),
                    ("time_iso", "DateTime", 0, "", 2),
                    ("lag_count", "Nullable(Float64)", 1, "", 3),
                ],
            ),
        },
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = md.inspect_table("ch_pk", table="analytics.events")

    assert metadata.partitions == (
        PartitionMetadata(name="time_iso", type="DateTime", transform="toYYYYMMDD", comment=None),
    )
    assert not any(w.kind == "partitions_unavailable" for w in metadata.warnings)


def test_inspect_table_clickhouse_partition_key_bare_column(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bare column partition key — no transform."""
    md.register(
        _spec("ch_bare", backend_type="clickhouse", host="ch.example", database="analytics")
    )
    backend = _FakeBackend(
        {"timestamp": "datetime", "value": "float64"},
        {
            "system.tables": _FakeQueryResult(
                ("comment", "partition_key", "engine", "engine_full"),
                [("Events", "timestamp", "MergeTree", "")],
            ),
            "system.columns": _FakeQueryResult(
                ("name", "type", "is_nullable", "comment", "position"),
                [("timestamp", "DateTime", 0, "", 1), ("value", "Float64", 0, "", 2)],
            ),
        },
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = md.inspect_table("ch_bare", table="analytics.events")

    assert metadata.partitions == (
        PartitionMetadata(name="timestamp", type="DateTime", transform=None, comment=None),
    )


def test_inspect_table_clickhouse_partition_key_composite(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Composite partition key — platform + toYYYYMM(timestamp)."""
    md.register(
        _spec("ch_comp", backend_type="clickhouse", host="ch.example", database="analytics")
    )
    backend = _FakeBackend(
        {"platform": "int64", "timestamp": "datetime", "value": "float64"},
        {
            "system.tables": _FakeQueryResult(
                ("comment", "partition_key", "engine", "engine_full"),
                [("Events", "platform, toYYYYMM(timestamp)", "MergeTree", "")],
            ),
            "system.columns": _FakeQueryResult(
                ("name", "type", "is_nullable", "comment", "position"),
                [
                    ("platform", "Int64", 0, "", 1),
                    ("timestamp", "DateTime", 0, "", 2),
                    ("value", "Float64", 0, "", 3),
                ],
            ),
        },
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = md.inspect_table("ch_comp", table="analytics.events")

    assert len(metadata.partitions) == 2
    assert metadata.partitions[0] == PartitionMetadata(
        name="platform", type="Int64", transform=None, comment=None
    )
    assert metadata.partitions[1] == PartitionMetadata(
        name="timestamp", type="DateTime", transform="toYYYYMM", comment=None
    )


def test_inspect_table_clickhouse_partition_key_empty_and_tuple(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty partition_key and tuple() both produce empty partitions."""
    for pk, label in [("tuple()", "tuple"), ("", "empty")]:
        md.register(
            _spec(f"ch_{label}", backend_type="clickhouse", host="ch.example", database="analytics")
        )
        backend = _FakeBackend(
            {"event_id": "string"},
            {
                "system.tables": _FakeQueryResult(
                    ("comment", "partition_key", "engine", "engine_full"),
                    [("Events", pk, "MergeTree", "")],
                ),
                "system.columns": _FakeQueryResult(
                    ("name", "type", "is_nullable", "comment", "position"),
                    [("event_id", "String", 0, "", 1)],
                ),
            },
        )

        import marivo.datasource.metadata as metadata_mod

        monkeypatch.setattr(
            metadata_mod._backends, "build_backend", lambda _datasource, b=backend: b
        )

        metadata = md.inspect_table(f"ch_{label}", table="analytics.events")
        assert metadata.partitions == (), f"partition_key={pk!r} should yield empty partitions"


def test_inspect_table_clickhouse_partition_key_unparseable(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unparseable expression intDiv(uid, 100) — stored as raw transform string."""
    md.register(_spec("ch_unp", backend_type="clickhouse", host="ch.example", database="analytics"))
    backend = _FakeBackend(
        {"uid": "int64", "value": "float64"},
        {
            "system.tables": _FakeQueryResult(
                ("comment", "partition_key", "engine", "engine_full"),
                [("Events", "intDiv(uid, 100)", "MergeTree", "")],
            ),
            "system.columns": _FakeQueryResult(
                ("name", "type", "is_nullable", "comment", "position"),
                [("uid", "Int64", 0, "", 1), ("value", "Float64", 0, "", 2)],
            ),
        },
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = md.inspect_table("ch_unp", table="analytics.events")

    assert len(metadata.partitions) == 1
    assert metadata.partitions[0].name == "uid"
    assert metadata.partitions[0].transform == "intDiv(uid, 100)"
    assert metadata.partitions[0].type == "Int64"


def test_inspect_table_clickhouse_distributed_dereferences_local_table(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Distributed table dereferences to local table for partition metadata."""
    md.register(
        _spec("ch_dist", backend_type="clickhouse", host="ch.example", database="analytics")
    )
    backend = _FakeBackend(
        {"event_id": "string", "time_iso": "datetime", "lag_count": "float64"},
        {},
        sequential_results=[
            # 1st query: system.tables for Distributed table
            _FakeQueryResult(
                ("comment", "partition_key", "engine", "engine_full"),
                [
                    (
                        "",
                        "",
                        "Distributed",
                        "Distributed('cluster1', 'analytics', 'events_local', rand())",
                    )
                ],
            ),
            # 2nd query: system.columns for column metadata
            _FakeQueryResult(
                ("name", "type", "is_nullable", "comment", "position"),
                [
                    ("event_id", "String", 0, "", 1),
                    ("time_iso", "DateTime", 0, "", 2),
                    ("lag_count", "Nullable(Float64)", 1, "", 3),
                ],
            ),
            # 3rd query: system.tables for local table dereference
            _FakeQueryResult(
                ("comment", "partition_key", "engine", "engine_full"),
                [("Local events", "toYYYYMMDD(time_iso)", "ReplicatedMergeTree", "")],
            ),
        ],
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = md.inspect_table("ch_dist", table="analytics.events")

    assert len(metadata.partitions) == 1
    assert metadata.partitions[0] == PartitionMetadata(
        name="time_iso", type="DateTime", transform="toYYYYMMDD", comment=None
    )


def test_inspect_table_clickhouse_distributed_dereference_failure(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Distributed table with unparseable engine_full → empty partitions + warning."""
    md.register(
        _spec("ch_dist_fail", backend_type="clickhouse", host="ch.example", database="analytics")
    )
    backend = _FakeBackend(
        {"event_id": "string"},
        {
            "system.tables": _FakeQueryResult(
                ("comment", "partition_key", "engine", "engine_full"),
                [("", "", "Distributed", "some_garbage_not_matching_regex")],
            ),
            "system.columns": _FakeQueryResult(
                ("name", "type", "is_nullable", "comment", "position"),
                [("event_id", "String", 0, "", 1)],
            ),
        },
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = md.inspect_table("ch_dist_fail", table="analytics.events")

    assert metadata.partitions == ()
    assert not any(w.kind == "partitions_unavailable" for w in metadata.warnings)


def test_inspect_table_clickhouse_system_tables_fallback(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expanded system.tables query fails → fallback to comment-only query."""
    md.register(
        _spec("ch_fallback", backend_type="clickhouse", host="ch.example", database="analytics")
    )
    backend = _FakeBackend(
        {"event_id": "string"},
        {
            # Fallback query: SELECT comment FROM system.tables (no partition_key columns)
            "system.tables": _FakeCursor(["comment"], [("Orders table",)]),
            "system.columns": _FakeQueryResult(
                ("name", "type", "is_nullable", "comment", "position"),
                [("event_id", "String", 0, "", 1)],
            ),
        },
        # Expanded query (with partition_key) raises — triggers the fallback
        raise_on_tokens=["partition_key"],
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = md.inspect_table("ch_fallback", table="analytics.orders")

    assert metadata.comment == "Orders table"
    assert metadata.partitions == ()
    assert not any(w.kind == "partitions_unavailable" for w in metadata.warnings)


def test_inspect_table_trino_short_name_is_not_rejected(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    md.register(_spec("wh", backend_type="trino", host="h", catalog="c"))
    backend = _FakeBackend({"order_id": "int64"}, {})

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = md.inspect_table("wh", table="orders")

    assert metadata.table == "orders"
    assert backend.table_calls == [("orders", None)]


def test_inspect_table_disconnects_backend(tmp_path, monkeypatch) -> None:
    from marivo.datasource import backends as backends_mod
    from marivo.datasource import metadata
    from marivo.datasource.authoring import DatasourceSpec
    from marivo.datasource.store import save_one

    (tmp_path / ".marivo").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MARIVO_PROJECT_ROOT", raising=False)
    db_path = tmp_path / "t.duckdb"
    ir = save_one(DatasourceSpec(name="tiny", backend_type="duckdb", path=str(db_path)))
    seed = backends_mod.build_backend(ir)
    seed.raw_sql("CREATE TABLE t AS SELECT 1 AS a")
    seed.disconnect()

    closed: list[bool] = []
    real_build = backends_mod.build_backend

    def tracking_build(datasource_ir):
        backend = real_build(datasource_ir)
        real_disconnect = backend.disconnect

        def spy_disconnect() -> None:
            closed.append(True)
            real_disconnect()

        monkeypatch.setattr(backend, "disconnect", spy_disconnect, raising=False)
        return backend

    monkeypatch.setattr(metadata._backends, "build_backend", tracking_build)
    result = metadata.inspect_table("tiny", table="t")
    assert result.columns
    assert closed == [True]
