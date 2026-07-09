"""Tests for datasource table metadata inspection."""

from __future__ import annotations

import json
from pathlib import Path

import ibis
import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo.datasource.authoring import (
    ClickHouseSpec,
    DatasourceSpec,
    DuckDBSpec,
    MySQLSpec,
    PostgresSpec,
    TrinoSpec,
)
from marivo.datasource.errors import DatasourceMetadataError
from marivo.datasource.metadata import (
    ColumnMetadata,
    MetadataWarning,
    PartitionMetadata,
    TableMetadata,
    TablePhysicalProfile,
    _inspect_source,
)
from marivo.datasource.metadata import (
    inspect_table as _inspect_table,
)
from marivo.render import _DEFAULT_MAX_OUTPUT_BYTES


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
        physical_profile=TablePhysicalProfile(
            row_count=1200,
            row_count_kind="estimate",
            size_bytes=4096,
            size_kind="on_disk",
            source="test.catalog",
            notes=("metadata-only",),
        ),
    )

    payload = metadata.to_dict()

    assert payload["datasource"] == "wh"
    assert payload["database"] == ["analytics", "public"]
    assert payload["columns"][0]["nullable"] is False
    assert payload["partitions"][0]["name"] == "order_date"
    assert payload["physical_profile"]["row_count"] == 1200
    assert payload["physical_profile"]["source"] == "test.catalog"
    assert json.loads(json.dumps(payload))["warnings"][0]["kind"] == "partitions_unavailable"
    assert "row_count" not in payload


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


def test_table_metadata_renders_shared_card_shape_and_uses_default_cap() -> None:
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
            ColumnMetadata(
                name="created_at",
                type="timestamp",
                nullable=None,
                comment=None,
                ordinal_position=2,
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
        is_view=True,
        physical_profile=TablePhysicalProfile(
            row_count=1200,
            row_count_kind="estimate",
            size_bytes=4096,
            size_kind="on_disk",
            source="test.catalog",
        ),
    )

    assert metadata.render() == "\n".join(
        [
            "TableMetadata ref=wh.analytics.public.orders backend=duckdb columns=2",
            "status: view=yes warnings=1 partitions=1",
            "comment: One row per order.",
            "physical profile: rows=1200 row_count_kind=estimate size_bytes=4096 size_kind=on_disk source=test.catalog",
            "columns: column | type | nullable | comment",
            "preview:",
            "order_id | int64 | N | Unique order id.",
            "created_at | timestamp | ? | ",
            "suggested next calls:",
            '- md.inspect_partitions(md.ref("datasource.wh"), md.table("orders")) to list partition values',
            '- md.partition({"order_date": "<value>"}) to scope a scan to order_date',
            "available:",
            "- .render()",
            "- .show()",
        ]
    )


@pytest.fixture
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _spec(name: str, *, backend_type: str, **fields: object) -> DatasourceSpec:
    if backend_type == "duckdb":
        return DuckDBSpec(name=name, **fields)
    if backend_type == "trino":
        return TrinoSpec(name=name, **fields)
    if backend_type == "mysql":
        return MySQLSpec(name=name, **fields)
    if backend_type == "postgres":
        return PostgresSpec(name=name, **fields)
    if backend_type == "clickhouse":
        return ClickHouseSpec(name=name, **fields)
    raise AssertionError(f"unexpected backend_type: {backend_type}")


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

    metadata = _inspect_table("wh", table="orders")

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

    view_md = _inspect_source("wh", source=ms.table("v_orders"))
    assert view_md.is_view is True
    assert view_md.view_definition is not None
    assert "SELECT" in view_md.view_definition.upper()

    base_md = _inspect_source("wh", source=ms.table("orders"))
    assert base_md.is_view is False
    assert base_md.view_definition is None


def test_inspect_source_duckdb_uses_database_for_view_detection(
    project_root: Path,
) -> None:
    db_path = project_root / "warehouse.duckdb"
    _create_duckdb_with_same_name_table_and_view(db_path)
    md.register(_spec("wh", backend_type="duckdb", path=str(db_path)))

    table_md = _inspect_table(
        "wh",
        table="orders",
        database="base_schema",
    )
    assert table_md.is_view is False
    assert table_md.view_definition is None

    source_table_md = _inspect_source(
        "wh",
        source=ms.table("orders", database="base_schema"),
    )
    assert source_table_md.is_view is False
    assert source_table_md.view_definition is None

    view_md = _inspect_source(
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

    table_md = _inspect_table("wh", table="orders")
    assert table_md.is_view is False
    assert table_md.view_definition is None

    view_md = _inspect_source(
        "wh",
        source=ms.table("orders", database="view_schema"),
    )
    assert view_md.is_view is True
    assert view_md.view_definition is not None
    assert "MAIN.ORDERS" in view_md.view_definition.upper()


def test_inspect_table_missing_datasource_raises(project_root: Path) -> None:
    with pytest.raises(DatasourceMetadataError) as exc_info:
        _inspect_table("missing", table="orders")

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

    metadata = _inspect_table("mysql_wh", table="mart.orders")

    assert metadata.backend_type == "mysql"
    assert metadata.comment == "One row per order"
    by_name = {column.name: column for column in metadata.columns}
    assert by_name["order_id"].nullable is False
    assert by_name["order_id"].comment == "Unique order id"
    assert by_name["amount"].nullable is True
    assert any("SHOW FULL COLUMNS" in query for query in backend.queries)


def test_inspect_table_mysql_populates_physical_profile(
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
                ["TABLE_COMMENT", "TABLE_ROWS", "DATA_LENGTH", "INDEX_LENGTH"],
                [("One row per order", 1200, 4096, 1024)],
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

    metadata = _inspect_table("mysql_wh", table="mart.orders")

    assert metadata.physical_profile == TablePhysicalProfile(
        row_count=1200,
        row_count_kind="estimate",
        size_bytes=5120,
        size_kind="data_plus_index",
        source="mysql.information_schema.tables",
    )


def test_public_inspect_partitions_mysql_discovers_field_and_uses_bounded_sample(
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
        {"order_id": "int64", "dt": "string", "amount": "float64"},
        {
            "information_schema.tables": _FakeCursor(["TABLE_COMMENT"], [("Orders",)]),
            "SHOW FULL COLUMNS": _FakeCursor(
                ["Field", "Type", "Null", "Comment"],
                [
                    ("order_id", "bigint", "NO", "Unique order id"),
                    ("dt", "varchar(8)", "NO", "Partition date"),
                    ("amount", "double", "YES", "Gross amount"),
                ],
            ),
            "information_schema.PARTITIONS": _FakeCursor(
                ["PARTITION_EXPRESSION"],
                [("`dt`",)],
            ),
            "partition_sample": _FakeCursor(["dt"], [("20260629",), ("20260628",)]),
        },
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    result = md.inspect_partitions(
        md.ref("datasource.mysql_wh"),
        md.table("orders"),
        project_root=project_root,
    )

    rendered = result.render()
    fallback_query = next(query for query in backend.queries if "partition_sample" in query)
    assert "source=bounded_sample_distinct" in rendered
    assert 'md.partition({"dt": "20260629"})' in rendered
    assert "START TRANSACTION READ ONLY" in backend.queries
    assert "WITH partition_sample AS" in fallback_query
    assert fallback_query.index("LIMIT 100") < fallback_query.index("SELECT DISTINCT")


def test_public_inspect_partitions_postgres_discovers_field_and_uses_bounded_sample(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PG_USER", "reader")
    md.register(
        _spec(
            "pg_wh",
            backend_type="postgres",
            host="localhost",
            user_env="PG_USER",
            database="mart",
            schema="analytics",
        )
    )
    backend = _FakeBackend(
        {"order_id": "int64", "dt": "string", "amount": "float64"},
        {
            "information_schema.columns": _FakeCursor(
                ["column_name", "data_type", "is_nullable", "ordinal_position"],
                [
                    ("order_id", "bigint", "NO", 1),
                    ("dt", "text", "NO", 2),
                    ("amount", "double precision", "YES", 3),
                ],
            ),
            "pg_get_partkeydef": _FakeCursor(["partition_key"], [("RANGE (dt)",)]),
            "partition_sample": _FakeCursor(["dt"], [("20260629",), ("20260628",)]),
        },
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    result = md.inspect_partitions(
        md.ref("datasource.pg_wh"),
        md.table("orders", database="analytics"),
        project_root=project_root,
    )

    rendered = result.render()
    fallback_query = next(query for query in backend.queries if "partition_sample" in query)
    assert "source=bounded_sample_distinct" in rendered
    assert 'md.partition({"dt": "20260629"})' in rendered
    assert "BEGIN READ ONLY" in backend.queries
    assert "WITH partition_sample AS" in fallback_query
    assert fallback_query.index("LIMIT 100") < fallback_query.index("SELECT DISTINCT")


def test_public_inspect_partitions_postgres_discovers_multi_column_partition_key(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PG_USER", "reader")
    md.register(
        _spec(
            "pg_wh",
            backend_type="postgres",
            host="localhost",
            user_env="PG_USER",
            database="mart",
            schema="analytics",
        )
    )
    backend = _FakeBackend(
        {"order_id": "int64", "log_date": "string", "log_hour": "string"},
        {
            "information_schema.columns": _FakeCursor(
                ["column_name", "data_type", "is_nullable", "ordinal_position"],
                [
                    ("order_id", "bigint", "NO", 1),
                    ("log_date", "text", "NO", 2),
                    ("log_hour", "text", "NO", 3),
                ],
            ),
            "pg_get_partkeydef": _FakeCursor(
                ["partition_key"],
                [("RANGE (log_date, log_hour)",)],
            ),
            "partition_sample": _FakeCursor(
                ["log_date", "log_hour"],
                [("20260629", "15"), ("20260629", "14")],
            ),
        },
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    result = md.inspect_partitions(
        md.ref("datasource.pg_wh"),
        md.table("orders", database="analytics"),
        project_root=project_root,
    )

    rendered = result.render()
    assert "partition columns: log_date, log_hour" in rendered
    assert 'md.partition({"log_date": "20260629", "log_hour": "15"})' in rendered


def test_inspect_table_postgres_populates_physical_profile(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PG_USER", "reader")
    md.register(
        _spec(
            "pg_wh",
            backend_type="postgres",
            host="localhost",
            user_env="PG_USER",
            database="mart",
            schema="analytics",
        )
    )
    backend = _FakeBackend(
        {"order_id": "int64", "amount": "float64"},
        {
            "obj_description": _FakeCursor(["comment"], [("Orders",)]),
            "information_schema.columns": _FakeCursor(
                ["column_name", "data_type", "is_nullable", "ordinal_position"],
                [
                    ("order_id", "bigint", "NO", 1),
                    ("amount", "double precision", "YES", 2),
                ],
            ),
            "pg_total_relation_size": _FakeCursor(
                ["reltuples", "total_relation_size"],
                [(1200.0, 8192)],
            ),
        },
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = _inspect_table("pg_wh", table="orders", database="analytics")

    assert metadata.physical_profile == TablePhysicalProfile(
        row_count=1200,
        row_count_kind="estimate",
        size_bytes=8192,
        size_kind="on_disk",
        source="postgres.pg_class",
    )


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
            _FakeCursor(["PARTITION_EXPRESSION"], []),
            _FakeCursor(["TABLE_TYPE"], [("VIEW",)]),
            _FakeCursor(["VIEW_DEFINITION"], [("select order_id from mart.orders",)]),
        ],
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = _inspect_table("mysql_wh", table="v_orders")

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

    metadata = _inspect_source(
        "duck_wh",
        source=ms.parquet("/data/orders/*.parquet", hive_partitioning=True),
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
            "information_schema.columns": _FakeCursor(
                ["column_name", "data_type", "is_nullable", "ordinal_position"],
                [
                    ("order_id", "bigint", "NO", 1),
                    ("amount", "double", "YES", 2),
                ],
            ),
            "SHOW COLUMNS FROM": _FakeCursor(
                ["Column", "Type", "Extra", "Comment"],
                [
                    ("order_id", "bigint", "", "Unique order id"),
                    ("amount", "double", "", "Gross amount"),
                ],
            ),
            "SHOW CREATE TABLE": _FakeCursor(
                ["Create Table"],
                [
                    (
                        "CREATE TABLE hive.analytics.orders (order_id bigint)\nCOMMENT 'One row per order'",
                    )
                ],
            ),
        },
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = _inspect_table(
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
    assert not any(
        query.startswith("SELECT comment FROM information_schema.tables")
        for query in backend.queries
    )
    assert any("SHOW COLUMNS FROM" in query for query in backend.queries)


def test_inspect_table_trino_splits_dotted_database_for_metadata_sql(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    md.register(
        _spec(
            "trino_wh",
            backend_type="trino",
            host="trino.example",
            catalog="hive",
        )
    )
    backend = _FakeBackend(
        {"log_date": "string"},
        {
            "information_schema.columns": _FakeCursor(
                ["column_name", "data_type", "is_nullable", "ordinal_position"],
                [("log_date", "varchar", "NO", 1)],
            ),
            "SHOW COLUMNS FROM": _FakeCursor(
                ["Column", "Type", "Extra", "Comment"],
                [("log_date", "varchar", "", "Log date")],
            ),
            "SHOW CREATE TABLE": _FakeCursor(
                ["Create Table"],
                [
                    (
                        "CREATE TABLE hive.iceberg_inf.dwd_olap_trino_query_info_i_hr (\n"
                        "   log_date varchar\n"
                        ")\nWITH (\n"
                        "   partitioned_by = ARRAY['log_date']\n"
                        ")",
                    )
                ],
            ),
        },
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = _inspect_table(
        "trino_wh",
        table="dwd_olap_trino_query_info_i_hr",
        database="hive.iceberg_inf",
    )

    assert backend.table_calls == [("dwd_olap_trino_query_info_i_hr", "hive.iceberg_inf")]
    assert any("SHOW CREATE TABLE" in query for query in backend.queries), backend.queries
    assert metadata.partitions == (
        PartitionMetadata(name="log_date", type="varchar", transform=None, comment=None),
    )
    assert any(
        'SHOW CREATE TABLE "hive"."iceberg_inf"."dwd_olap_trino_query_info_i_hr"' in query
        for query in backend.queries
    )
    assert any(
        'SHOW STATS FOR "hive"."iceberg_inf"."dwd_olap_trino_query_info_i_hr"' in query
        for query in backend.queries
    )
    assert not any('"hive"."hive.iceberg_inf"' in query for query in backend.queries)


def test_inspect_table_trino_keeps_two_part_database_tuple_for_metadata_sql(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    md.register(
        _spec(
            "trino_wh",
            backend_type="trino",
            host="trino.example",
            catalog="hive",
        )
    )
    backend = _FakeBackend(
        {"log_date": "string"},
        {
            "information_schema.columns": _FakeCursor(
                ["column_name", "data_type", "is_nullable", "ordinal_position"],
                [("log_date", "varchar", "NO", 1)],
            ),
            "SHOW COLUMNS FROM": _FakeCursor(
                ["Column", "Type", "Extra", "Comment"],
                [("log_date", "varchar", "", "Log date")],
            ),
            "SHOW CREATE TABLE": _FakeCursor(
                ["Create Table"],
                [
                    (
                        "CREATE TABLE hive.iceberg_inf.dwd_olap_trino_query_info_i_hr (\n"
                        "   log_date varchar\n"
                        ")\nWITH (\n"
                        "   partitioned_by = ARRAY['log_date']\n"
                        ")",
                    )
                ],
            ),
        },
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    _inspect_table(
        "trino_wh",
        table="dwd_olap_trino_query_info_i_hr",
        database=("hive", "iceberg_inf"),
    )

    assert any(
        'SHOW CREATE TABLE "hive"."iceberg_inf"."dwd_olap_trino_query_info_i_hr"' in query
        for query in backend.queries
    )
    assert not any('"hive"."hive.iceberg_inf"' in query for query in backend.queries)


def test_inspect_table_trino_populates_physical_profile_from_show_stats(
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
            "information_schema.columns": _FakeCursor(
                ["column_name", "data_type", "is_nullable", "ordinal_position"],
                [
                    ("order_id", "bigint", "NO", 1),
                    ("amount", "double", "YES", 2),
                ],
            ),
            "SHOW COLUMNS FROM": _FakeCursor(
                ["Column", "Type", "Extra", "Comment"],
                [("order_id", "bigint", "", None), ("amount", "double", "", None)],
            ),
            "SHOW STATS FOR": _FakeCursor(
                ["column_name", "data_size", "row_count"],
                [
                    ("order_id", 800, None),
                    ("amount", 1200, None),
                    (None, None, 1200),
                ],
            ),
        },
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = _inspect_table("trino_wh", table="orders")

    assert metadata.physical_profile == TablePhysicalProfile(
        row_count=1200,
        row_count_kind="estimate",
        size_bytes=2000,
        size_kind="table_stats",
        source="trino.show_stats",
    )
    assert any("SHOW STATS FOR" in query for query in backend.queries)


def test_inspect_table_trino_stats_failure_is_warning_only(
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
            "information_schema.columns": _FakeCursor(
                ["column_name", "data_type", "is_nullable", "ordinal_position"],
                [("order_id", "bigint", "NO", 1)],
            ),
            "SHOW COLUMNS FROM": _FakeCursor(
                ["Column", "Type", "Extra", "Comment"],
                [("order_id", "bigint", "", None)],
            ),
            "SHOW CREATE TABLE": _FakeCursor(
                ["Create Table"],
                [("CREATE TABLE hive.analytics.orders (order_id bigint)\nCOMMENT 'Orders'",)],
            ),
        },
        raise_on_tokens=["SHOW STATS FOR"],
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = _inspect_table("trino_wh", table="orders")

    assert metadata.comment == "Orders"
    assert metadata.physical_profile is None
    assert any(
        "trino physical profile query failed" in warning.message for warning in metadata.warnings
    )


def test_inspect_table_trino_keeps_column_comments_when_table_comments_unavailable(
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
            "information_schema.columns": _FakeCursor(
                ["column_name", "data_type", "is_nullable", "ordinal_position"],
                [
                    ("order_id", "bigint", "NO", 1),
                    ("amount", "double", "YES", 2),
                ],
            ),
            "SHOW COLUMNS FROM": _FakeCursor(
                ["Column", "Type", "Extra", "Comment"],
                [
                    ("order_id", "bigint", "", "Order id"),
                    ("amount", "double", "", "Amount"),
                ],
            ),
        },
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = _inspect_table("trino_wh", table="orders")

    by_name = {column.name: column for column in metadata.columns}
    assert by_name["order_id"].comment == "Order id"
    assert by_name["amount"].comment == "Amount"
    warning_kinds = {warning.kind for warning in metadata.warnings}
    assert "table_comments_unavailable" in warning_kinds
    assert "comments_unavailable" not in warning_kinds
    assert "metadata_query_failed" not in warning_kinds


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
            _FakeCursor(
                ["column_name", "data_type", "is_nullable", "ordinal_position"],
                [("order_id", "bigint", "NO", 1)],
            ),
            _FakeCursor(
                ["Column", "Type", "Extra", "Comment"],
                [("order_id", "bigint", "", "Unique order id")],
            ),
            _FakeCursor(["table_type"], [("VIEW",)]),
            _FakeCursor(["view_definition"], [("SELECT order_id FROM analytics.orders",)]),
        ],
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = _inspect_table("trino_wh", table="v_orders")

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
            "information_schema.columns": _FakeCursor(
                ["column_name", "data_type", "is_nullable", "ordinal_position"],
                [("order_id", "bigint", "NO", 1)],
            ),
            "SHOW COLUMNS FROM": _FakeCursor(
                ["Column", "Type", "Extra", "Comment"],
                [("order_id", "bigint", "", "Unique order id")],
            ),
            "SHOW CREATE TABLE": _FakeCursor(
                ["Create Table"],
                [("CREATE TABLE hive.analytics.orders (order_id bigint)\nCOMMENT 'Orders'",)],
            ),
        },
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = _inspect_table("trino_wh", table="orders")

    assert metadata.backend_type == "trino"
    assert metadata.database is None
    assert backend.table_calls == [("orders", None)]
    assert metadata.comment == "Orders"
    assert any("table_catalog = 'hive'" in query for query in backend.queries)
    assert any("table_schema = 'analytics'" in query for query in backend.queries)
    assert any("table_name = 'orders'" in query for query in backend.queries)


def test_inspect_table_trino_falls_back_when_comment_columns_are_unavailable(
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
            "information_schema.columns": _FakeCursor(
                ["column_name", "data_type", "is_nullable", "ordinal_position"],
                [
                    ("order_id", "bigint", "NO", 1),
                    ("amount", "double", "YES", 2),
                ],
            ),
        },
        raise_on_tokens=["SHOW COLUMNS FROM"],
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = _inspect_table("trino_wh", table="orders")

    by_name = {column.name: column for column in metadata.columns}
    assert by_name["order_id"].type == "bigint"
    assert by_name["order_id"].nullable is False
    assert by_name["amount"].type == "double"
    assert by_name["amount"].comment is None
    assert any("SHOW COLUMNS FROM" in query for query in backend.queries)
    assert any(
        "SELECT column_name, data_type, is_nullable, ordinal_position" in query
        for query in backend.queries
    )
    assert any(warning.kind == "column_comments_unavailable" for warning in metadata.warnings)
    assert not any(warning.kind == "comments_unavailable" for warning in metadata.warnings)
    assert not any(warning.kind == "metadata_query_failed" for warning in metadata.warnings)


def test_inspect_table_trino_hive_partitioned_by_from_show_create(
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
        {"order_id": "int64", "dt": "string", "region": "string"},
        {
            "information_schema.columns": _FakeCursor(
                ["column_name", "data_type", "is_nullable", "comment", "ordinal_position"],
                [
                    ("order_id", "bigint", "NO", None, 1),
                    ("dt", "varchar", "NO", None, 2),
                    ("region", "varchar", "YES", None, 3),
                ],
            ),
            "SHOW CREATE TABLE": _FakeCursor(
                ["Create Table"],
                [
                    (
                        "CREATE TABLE hive.analytics.orders (\n"
                        "   order_id bigint,\n"
                        "   dt varchar,\n"
                        "   region varchar\n"
                        ")\nWITH (\n"
                        "   partitioned_by = ARRAY['dt', 'region']\n"
                        ")",
                    )
                ],
            ),
        },
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = _inspect_table("trino_wh", table="orders")

    assert metadata.partitions == (
        PartitionMetadata(name="dt", type="varchar", transform=None, comment=None),
        PartitionMetadata(name="region", type="varchar", transform=None, comment=None),
    )
    assert not any(warning.kind == "partitions_unavailable" for warning in metadata.warnings)


def test_public_inspect_partitions_trino_lists_bounded_partition_tuples(
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
        {"order_id": "int64", "log_date": "string", "log_hour": "string"},
        {
            "information_schema.columns": _FakeCursor(
                ["column_name", "data_type", "is_nullable", "comment", "ordinal_position"],
                [
                    ("order_id", "bigint", "NO", None, 1),
                    ("log_date", "varchar", "NO", None, 2),
                    ("log_hour", "varchar", "NO", None, 3),
                ],
            ),
            "SHOW CREATE TABLE": _FakeCursor(
                ["Create Table"],
                [
                    (
                        "CREATE TABLE hive.analytics.orders (\n"
                        "   order_id bigint,\n"
                        "   log_date varchar,\n"
                        "   log_hour varchar\n"
                        ")\nWITH (\n"
                        "   partitioned_by = ARRAY['log_date', 'log_hour']\n"
                        ")",
                    )
                ],
            ),
            "$partitions": _FakeCursor(
                ["log_date", "log_hour"],
                [("20260629", "15"), ("20260629", "14"), ("20260628", "23")],
            ),
        },
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    result = md.inspect_partitions(
        md.ref("datasource.trino_wh"),
        md.table("orders"),
        project_root=project_root,
    )

    assert isinstance(result, md.DatasourceResult)
    rendered = result.render()
    assert "PartitionInspectionResult" in rendered
    assert "source=metadata" in rendered
    assert "log_date=20260629" in rendered
    assert "log_hour=15" in rendered
    assert 'md.partition({"log_date": "20260629", "log_hour": "15"})' in rendered
    assert any("$partitions" in query for query in backend.queries)
    assert any("LIMIT 100" in query for query in backend.queries)


def test_public_inspect_partitions_does_not_accept_limit_parameter(
    project_root: Path,
) -> None:
    md.register(_spec("trino_wh", backend_type="trino", host="trino.example", catalog="hive"))

    with pytest.raises(TypeError, match="limit"):
        md.inspect_partitions(  # type: ignore[call-arg]
            md.ref("datasource.trino_wh"),
            md.table("orders"),
            limit=2,
            project_root=project_root,
        )


def test_public_inspect_partitions_skips_incomplete_partition_tuples(
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
        {"order_id": "int64", "log_date": "string", "log_hour": "string"},
        {
            "information_schema.columns": _FakeCursor(
                ["column_name", "data_type", "is_nullable", "comment", "ordinal_position"],
                [
                    ("order_id", "bigint", "NO", None, 1),
                    ("log_date", "varchar", "YES", None, 2),
                    ("log_hour", "varchar", "YES", None, 3),
                ],
            ),
            "SHOW CREATE TABLE": _FakeCursor(
                ["Create Table"],
                [
                    (
                        "CREATE TABLE hive.analytics.orders (\n"
                        "   order_id bigint,\n"
                        "   log_date varchar,\n"
                        "   log_hour varchar\n"
                        ")\nWITH (\n"
                        "   partitioned_by = ARRAY['log_date', 'log_hour']\n"
                        ")",
                    )
                ],
            ),
            "$partitions": _FakeCursor(
                ["log_date", "log_hour"],
                [("20260629", None), ("20260629", "15")],
            ),
        },
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    result = md.inspect_partitions(
        md.ref("datasource.trino_wh"),
        md.table("orders"),
        project_root=project_root,
    )

    rendered = result.render()
    assert 'md.partition({"log_date": "20260629"})' not in rendered
    assert 'md.partition({"log_date": "20260629", "log_hour": "15"})' in rendered
    assert "incomplete partition rows omitted=1" in rendered


def test_public_inspect_partitions_trino_fallback_limits_before_distinct(
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
        {"order_id": "int64", "log_date": "string", "log_hour": "string"},
        {
            "information_schema.columns": _FakeCursor(
                ["column_name", "data_type", "is_nullable", "comment", "ordinal_position"],
                [
                    ("order_id", "bigint", "NO", None, 1),
                    ("log_date", "varchar", "NO", None, 2),
                    ("log_hour", "varchar", "NO", None, 3),
                ],
            ),
            "SHOW CREATE TABLE": _FakeCursor(
                ["Create Table"],
                [
                    (
                        "CREATE TABLE hive.analytics.orders (\n"
                        "   order_id bigint,\n"
                        "   log_date varchar,\n"
                        "   log_hour varchar\n"
                        ")\nWITH (\n"
                        "   partitioned_by = ARRAY['log_date', 'log_hour']\n"
                        ")",
                    )
                ],
            ),
            "partition_sample": _FakeCursor(
                ["log_date", "log_hour"],
                [("20260629", "15"), ("20260629", "14")],
            ),
        },
        raise_on_tokens=["$partitions"],
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    result = md.inspect_partitions(
        md.ref("datasource.trino_wh"),
        md.table("orders"),
        project_root=project_root,
    )

    rendered = result.render()
    assert "source=bounded_sample_distinct" in rendered, (rendered, backend.queries)
    fallback_query = next((query for query in backend.queries if "partition_sample" in query), None)
    assert fallback_query is not None, (rendered, backend.queries)
    assert "SELECT DISTINCT" in fallback_query
    assert "LIMIT 100" in fallback_query
    assert fallback_query.index("LIMIT 100") < fallback_query.index("SELECT DISTINCT")
    assert (
        'SELECT DISTINCT "log_date", "log_hour" FROM "hive"."analytics"."orders"'
        not in fallback_query
    )
    assert "sample of the first 100 rows" in rendered


def test_public_inspect_partitions_clickhouse_transformed_partition_unavailable(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    md.register(_spec("ch_wh", backend_type="clickhouse", host="ch.example", database="analytics"))
    backend = _FakeBackend(
        {"timestamp": "datetime", "value": "float64"},
        {
            "system.tables": _FakeQueryResult(
                ("comment", "partition_key", "engine", "engine_full"),
                [("Events", "toYYYYMM(timestamp)", "MergeTree", "")],
            ),
            "system.columns": _FakeQueryResult(
                ("name", "type", "is_nullable", "comment", "position"),
                [("timestamp", "DateTime", 0, "", 1), ("value", "Float64", 0, "", 2)],
            ),
        },
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    result = md.inspect_partitions(
        md.ref("datasource.ch_wh"),
        md.table("events", database="analytics"),
        project_root=project_root,
    )

    rendered = result.render()
    assert "Partition values unavailable" in rendered
    assert "without scanning data" in rendered
    assert "md.raw_sql" in rendered
    assert not any("SELECT partition AS" in query for query in backend.queries)


def test_public_inspect_partitions_clickhouse_bare_partition_uses_system_parts(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    md.register(_spec("ch_wh", backend_type="clickhouse", host="ch.example", database="analytics"))
    backend = _FakeBackend(
        {"dt": "string", "value": "float64"},
        {
            "system.tables": _FakeQueryResult(
                ("comment", "partition_key", "engine", "engine_full"),
                [("Events", "dt", "MergeTree", "")],
            ),
            "system.columns": _FakeQueryResult(
                ("name", "type", "is_nullable", "comment", "position"),
                [("dt", "String", 0, "", 1), ("value", "Float64", 0, "", 2)],
            ),
            "system.parts": _FakeQueryResult(
                ("dt",),
                [("20260629",), ("20260628",)],
            ),
        },
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    result = md.inspect_partitions(
        md.ref("datasource.ch_wh"),
        md.table("events", database="analytics"),
        project_root=project_root,
    )

    rendered = result.render()
    assert "source=system_catalog" in rendered
    assert 'md.partition({"dt": "20260629"})' in rendered
    assert any("system.parts" in query for query in backend.queries)


def test_public_inspect_partitions_clickhouse_distributed_uses_local_system_parts(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    md.register(_spec("ch_wh", backend_type="clickhouse", host="ch.example", database="analytics"))
    backend = _FakeBackend(
        {"dt": "string", "value": "float64"},
        {
            "WHERE name = 'events_dist'": _FakeQueryResult(
                ("comment", "partition_key", "engine", "engine_full"),
                [
                    (
                        "Events",
                        "",
                        "Distributed",
                        "Distributed('cluster', 'analytics', 'events_local', rand())",
                    )
                ],
            ),
            "WHERE name = 'events_local'": _FakeQueryResult(
                ("partition_key",),
                [("dt",)],
            ),
            "system.columns": _FakeQueryResult(
                ("name", "type", "is_nullable", "comment", "position"),
                [("dt", "String", 0, "", 1), ("value", "Float64", 0, "", 2)],
            ),
            "system.parts": _FakeQueryResult(
                ("dt",),
                [("20260629",), ("20260628",)],
            ),
        },
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    result = md.inspect_partitions(
        md.ref("datasource.ch_wh"),
        md.table("events_dist", database="analytics"),
        project_root=project_root,
    )

    rendered = result.render()
    parts_query = next(query for query in backend.queries if "system.parts" in query)
    assert "source=system_catalog" in rendered
    assert "table = 'events_local'" in parts_query
    assert 'md.partition({"dt": "20260629"})' in rendered


def test_inspect_table_trino_iceberg_partitioning_from_show_create(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    md.register(
        _spec(
            "iceberg_wh",
            backend_type="trino",
            host="trino.example",
            catalog="iceberg",
            schema="analytics",
        )
    )
    backend = _FakeBackend(
        {"created_at": "timestamp", "user_id": "int64", "amount": "float64"},
        {
            "information_schema.columns": _FakeCursor(
                ["column_name", "data_type", "is_nullable", "comment", "ordinal_position"],
                [
                    ("created_at", "timestamp(6)", "NO", None, 1),
                    ("user_id", "bigint", "NO", None, 2),
                    ("amount", "double", "YES", None, 3),
                ],
            ),
            "SHOW CREATE TABLE": _FakeCursor(
                ["Create Table"],
                [
                    (
                        "CREATE TABLE iceberg.analytics.events (\n"
                        "   created_at timestamp(6),\n"
                        "   user_id bigint,\n"
                        "   amount double\n"
                        ")\nWITH (\n"
                        "   partitioning = ARRAY['month(created_at)', 'bucket(user_id, 16)']\n"
                        ")",
                    )
                ],
            ),
        },
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = _inspect_table("iceberg_wh", table="events")

    assert metadata.partitions == (
        PartitionMetadata(name="created_at", type="timestamp(6)", transform="month", comment=None),
        PartitionMetadata(name="user_id", type="bigint", transform="bucket", comment=None),
    )
    assert not any(warning.kind == "partitions_unavailable" for warning in metadata.warnings)


def test_inspect_table_trino_without_schema_returns_schema_only(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    md.register(_spec("trino_wh", backend_type="trino", host="trino.example", catalog="hive"))
    backend = _FakeBackend({"order_id": "int64"}, {})

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = _inspect_table("trino_wh", table="orders")

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

    metadata = _inspect_table("ch_wh", table="analytics.orders")

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


def test_inspect_table_clickhouse_populates_physical_profile_from_system_parts(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    md.register(
        _spec("ch_profile", backend_type="clickhouse", host="ch.example", database="analytics")
    )
    backend = _FakeBackend(
        {"event_id": "string", "value": "float64"},
        {
            "system.tables": _FakeQueryResult(
                ("comment", "partition_key", "engine", "engine_full"),
                [("Events", "", "MergeTree", "")],
            ),
            "system.columns": _FakeQueryResult(
                ("name", "type", "is_nullable", "comment", "position"),
                [("event_id", "String", 0, "", 1), ("value", "Float64", 0, "", 2)],
            ),
            "sum(rows)": _FakeQueryResult(
                ("row_count", "size_bytes"),
                [(1200, 8192)],
            ),
        },
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = _inspect_table("ch_profile", table="analytics.events")

    assert metadata.physical_profile == TablePhysicalProfile(
        row_count=1200,
        row_count_kind="metadata",
        size_bytes=8192,
        size_kind="on_disk",
        source="clickhouse.system_parts",
    )
    assert any("system.parts" in query and "sum(rows)" in query for query in backend.queries)


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

    metadata = _inspect_table("ch_view", table="v_orders")

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

    metadata = _inspect_table("ch_old", table="default.orders")

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

    metadata = _inspect_table("ch_qr", table="analytics.orders")

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
            database="sample_web_monitor",
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

    metadata = _inspect_table("ch_22_3", table="sample_web_monitor.ads_web_main_box_rt")

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

    metadata = _inspect_table("ch_pk", table="analytics.events")

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

    metadata = _inspect_table("ch_bare", table="analytics.events")

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

    metadata = _inspect_table("ch_comp", table="analytics.events")

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

        metadata = _inspect_table(f"ch_{label}", table="analytics.events")
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

    metadata = _inspect_table("ch_unp", table="analytics.events")

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

    metadata = _inspect_table("ch_dist", table="analytics.events")

    assert len(metadata.partitions) == 1
    assert metadata.partitions[0] == PartitionMetadata(
        name="time_iso", type="DateTime", transform="toYYYYMMDD", comment=None
    )


def test_inspect_table_clickhouse_distributed_profile_notes_local_metadata(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    md.register(
        _spec("ch_dist_profile", backend_type="clickhouse", host="ch.example", database="analytics")
    )
    backend = _FakeBackend(
        {"event_id": "string", "dt": "string"},
        {},
        sequential_results=[
            _FakeQueryResult(
                ("comment", "partition_key", "engine", "engine_full"),
                [
                    (
                        "Events",
                        "",
                        "Distributed",
                        "Distributed('cluster1', 'analytics', 'events_local', rand())",
                    )
                ],
            ),
            _FakeQueryResult(
                ("name", "type", "is_nullable", "comment", "position"),
                [("event_id", "String", 0, "", 1), ("dt", "String", 0, "", 2)],
            ),
            _FakeQueryResult(("partition_key",), [("dt",)]),
            _FakeQueryResult(("row_count", "size_bytes"), [(1200, 8192)]),
        ],
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = _inspect_table("ch_dist_profile", table="analytics.events_dist")

    assert metadata.physical_profile == TablePhysicalProfile(
        row_count=1200,
        row_count_kind="metadata",
        size_bytes=8192,
        size_kind="on_disk",
        source="clickhouse.system_parts",
        notes=(
            "resolved Distributed table to analytics.events_local; profile is not cluster-wide",
        ),
    )


def test_inspect_table_clickhouse_distributed_dereference_failure(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Distributed table with unparseable engine_full has no local physical profile."""
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
            "sum(rows)": _FakeQueryResult(
                ("row_count", "size_bytes"),
                [(0, 0)],
            ),
        },
    )

    import marivo.datasource.metadata as metadata_mod

    monkeypatch.setattr(metadata_mod._backends, "build_backend", lambda _datasource: backend)

    metadata = _inspect_table("ch_dist_fail", table="analytics.events")

    assert metadata.partitions == ()
    assert metadata.physical_profile is None
    assert not any(w.kind == "partitions_unavailable" for w in metadata.warnings)
    assert any(
        "clickhouse distributed physical profile dereference failed" in warning.message
        for warning in metadata.warnings
    )


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

    metadata = _inspect_table("ch_fallback", table="analytics.orders")

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

    metadata = _inspect_table("wh", table="orders")

    assert metadata.table == "orders"
    assert backend.table_calls == [("orders", None)]


# ---------------------------------------------------------------------------
# TableMetadata render / show / repr
# ---------------------------------------------------------------------------


def _make_table_metadata(**overrides: object) -> TableMetadata:
    defaults: dict[str, object] = {
        "datasource": "wh",
        "table": "orders",
        "database": None,
        "backend_type": "duckdb",
        "comment": None,
        "columns": (
            ColumnMetadata(
                name="order_id", type="int64", nullable=False, comment=None, ordinal_position=1
            ),
            ColumnMetadata(
                name="amount", type="float64", nullable=True, comment="USD", ordinal_position=2
            ),
            ColumnMetadata(
                name="region", type="varchar", nullable=True, comment=None, ordinal_position=3
            ),
        ),
        "partitions": (),
        "warnings": (),
    }
    defaults.update(overrides)
    return TableMetadata(**defaults)


def test_table_metadata_repr_is_bounded() -> None:
    metadata = _make_table_metadata()
    r = repr(metadata)
    assert r.startswith("<TableMetadata ref=")
    assert "call .show() to inspect>" in r


def test_table_metadata_render_includes_column_table() -> None:
    metadata = _make_table_metadata()
    rendered = metadata.render()
    assert rendered.startswith("TableMetadata ref=wh.orders backend=duckdb columns=3")
    assert "order_id" in rendered
    assert "int64" in rendered
    assert "float64" in rendered
    assert "available:" in rendered


def test_table_metadata_render_uses_shared_output_cap() -> None:
    metadata = _make_table_metadata(
        comment="x" * 200,
        columns=(
            ColumnMetadata(
                name="amount",
                type="float64",
                nullable=True,
                comment="y" * 200,
                ordinal_position=1,
            ),
        ),
    )

    with pytest.raises(ValueError, match="max_output_bytes is too small"):
        metadata.render(max_output_bytes=120)

    rendered = metadata.render(max_output_bytes=None)
    assert "amount | float64 | Y | " in rendered
    assert "output truncated" not in rendered


def test_table_metadata_long_comment_default_render_is_bounded() -> None:
    metadata = _make_table_metadata(comment="x" * (_DEFAULT_MAX_OUTPUT_BYTES * 2))

    rendered = metadata.render()

    assert len(rendered.encode("utf-8")) <= _DEFAULT_MAX_OUTPUT_BYTES
    assert "output truncated" in rendered
    assert "available:" in rendered


def test_table_metadata_render_shows_comment_and_view() -> None:
    metadata = _make_table_metadata(
        comment="One row per order",
        is_view=True,
        view_definition="SELECT * FROM raw_orders",
    )
    rendered = metadata.render()
    assert "comment: One row per order" in rendered
    assert "view=yes" in rendered


def test_table_metadata_render_shows_partitions_and_warnings() -> None:
    metadata = _make_table_metadata(
        partitions=(PartitionMetadata(name="dt", type="date", transform="identity", comment=None),),
        warnings=(MetadataWarning(kind="comments_unavailable", message="no comments"),),
    )
    rendered = metadata.render()
    assert "partitions=1" in rendered
    assert "warnings=1" in rendered


def test_table_metadata_render_with_partitions_suggests_partition_calls() -> None:
    metadata = _make_table_metadata(
        partitions=(PartitionMetadata(name="dt", type="date", transform="identity", comment=None),),
    )

    rendered = metadata.render()

    assert "md.inspect_partitions(" in rendered
    assert "md.partition(" in rendered
    assert "dt" in rendered


def test_table_metadata_render_without_partitions_omits_partition_calls() -> None:
    metadata = _make_table_metadata(partitions=())

    rendered = metadata.render()

    assert "md.inspect_partitions(" not in rendered
    assert "md.partition(" not in rendered


def test_table_metadata_render_no_status_when_sparse() -> None:
    metadata = _make_table_metadata()
    rendered = metadata.render()
    assert "status:" not in rendered


def test_table_metadata_show_prints(capsys: pytest.CaptureFixture[str]) -> None:
    metadata = _make_table_metadata()
    metadata.show()
    captured = capsys.readouterr()
    assert captured.out.startswith("TableMetadata ref=wh.orders")


def test_table_metadata_satisfies_agent_result_protocol() -> None:
    from marivo.render import AgentResult

    metadata = _make_table_metadata()
    assert isinstance(metadata, AgentResult)


def _create_duckdb_with_constraints(path: Path) -> None:
    con = ibis.duckdb.connect(str(path))
    con.raw_sql(
        "CREATE TABLE orders ("
        "order_id INTEGER NOT NULL, "
        "customer_id INTEGER NOT NULL, "
        "amount DOUBLE, "
        "PRIMARY KEY (order_id), "
        "UNIQUE (customer_id))"
    )
    con.disconnect()


def test_inspect_table_duckdb_populates_primary_keys_and_unique(
    project_root: Path,
) -> None:
    db_path = project_root / "warehouse.duckdb"
    _create_duckdb_with_constraints(db_path)
    md.register(_spec("wh", backend_type="duckdb", path=str(db_path)))

    metadata = _inspect_table("wh", table="orders")

    assert metadata.primary_keys == ("order_id",)
    assert len(metadata.unique_constraints) == 1
    uc = metadata.unique_constraints[0]
    assert uc.columns == ("customer_id",)
    assert uc.kind == "unique"
    assert not any(w.kind == "primary_keys_unavailable" for w in metadata.warnings)


def test_inspect_table_duckdb_populates_physical_profile_from_estimated_size(
    project_root: Path,
) -> None:
    db_path = project_root / "warehouse.duckdb"
    con = ibis.duckdb.connect(str(db_path))
    con.raw_sql("CREATE TABLE orders AS SELECT range AS order_id FROM range(12)")
    con.disconnect()
    md.register(_spec("wh", backend_type="duckdb", path=str(db_path)))

    metadata = _inspect_table("wh", table="orders")

    assert metadata.physical_profile == TablePhysicalProfile(
        row_count=12,
        row_count_kind="estimate",
        size_bytes=None,
        size_kind="unknown",
        source="duckdb.duckdb_tables",
    )


def test_table_metadata_to_dict_includes_key_constraints() -> None:
    from marivo.datasource.metadata import UniqueConstraintMetadata

    metadata = TableMetadata(
        datasource="wh",
        table="orders",
        database=None,
        backend_type="duckdb",
        comment=None,
        columns=(),
        partitions=(),
        warnings=(),
        primary_keys=("order_id",),
        unique_constraints=(
            UniqueConstraintMetadata(name=None, columns=("customer_id",), kind="unique"),
        ),
    )
    payload = metadata.to_dict()
    assert payload["primary_keys"] == ["order_id"]
    assert payload["unique_constraints"][0]["columns"] == ["customer_id"]
    assert payload["physical_profile"] is None
    assert "row_count" not in payload
    assert json.loads(json.dumps(payload))["primary_keys"] == ["order_id"]


def test_non_duckdb_backend_emits_primary_keys_unavailable_warning(
    project_root: Path,
) -> None:
    from marivo.datasource.metadata import _with_primary_key_capability_warning

    metadata = TableMetadata(
        datasource="wh",
        table="orders",
        database=None,
        backend_type="clickhouse",
        comment=None,
        columns=(),
        partitions=(),
        warnings=(),
    )
    result = _with_primary_key_capability_warning(metadata)
    assert any(w.kind == "primary_keys_unavailable" for w in result.warnings)
    # DuckDB metadata is passed through unchanged.
    duck = TableMetadata(
        datasource="wh",
        table="orders",
        database=None,
        backend_type="duckdb",
        comment=None,
        columns=(),
        partitions=(),
        warnings=(),
    )
    assert _with_primary_key_capability_warning(duck) is duck


def test_duckdb_constraint_query_failure_is_warning(project_root: Path) -> None:
    # A table without constraints still inspects cleanly with empty pk/uq.
    db_path = project_root / "warehouse.duckdb"
    con = ibis.duckdb.connect(str(db_path))
    con.raw_sql("CREATE TABLE plain (a INTEGER, b VARCHAR)")
    con.disconnect()
    md.register(_spec("wh", backend_type="duckdb", path=str(db_path)))

    metadata = _inspect_table("wh", table="plain")
    assert metadata.primary_keys == ()
    assert metadata.unique_constraints == ()
