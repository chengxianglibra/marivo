"""Opt-in physical qualification for the governed basic source reader."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pyarrow as pa
import pytest

from marivo.datasource.adapters import PhysicalRequirement, SourceSession, provider_for
from marivo.datasource.engines.base import MetadataInspectRequest
from marivo.datasource.errors import DatasourceSourceCapabilityError
from marivo.datasource.ir import (
    AiContextIR,
    DatasourceIR,
    DatasourceSourceLocation,
    TableSourceIR,
)
from marivo.datasource.metadata import TableMetadata

pytestmark = pytest.mark.runtime


def _datasource(
    backend: str,
    fields: Mapping[str, object],
    env_refs: Mapping[str, str] | None = None,
) -> DatasourceIR:
    return DatasourceIR(
        semantic_id="source_adapter",
        name="source_adapter",
        backend_type=backend,
        fields=dict(fields),
        env_refs=dict(env_refs or {}),
        ai_context=AiContextIR(),
        python_symbol="source_adapter",
        location=DatasourceSourceLocation("source_adapter.py", 1),
    )


def _assert_table_read(
    session: SourceSession,
    source: TableSourceIR,
    *,
    identity: str,
    expected: list[dict[str, object]],
    expected_metadata: Callable[[TableMetadata], None] | None = None,
) -> None:
    bound = session.bind(source, source_identity=identity)
    metadata = session.provider.metadata.inspect_table(
        MetadataInspectRequest(
            datasource=session.datasource.name,
            backend=session._backend,
            table=source.table,
            database=source.database,
            table_expr=bound.relation,
            include_partitions=True,
            datasource_ir=session.datasource,
        )
    )
    assert {"id", "amount"} <= {column.name for column in metadata.columns}
    if expected_metadata is not None:
        expected_metadata(metadata)
    qualified = session.qualify(
        bound,
        PhysicalRequirement("source_adapter.basic", 1, frozenset({"scan", "filter", "project"})),
    )
    expression = bound.relation.select("id", "amount").order_by("id")
    read = session.compile(
        qualified,
        expression,
        purpose="source_adapter.basic",
        expected_schema=expression.schema().to_pyarrow(),
    )
    stream = session.batches(read, chunk_size=1)
    assert pa.Table.from_batches(stream, schema=stream.schema).to_pylist() == expected
    assert session.submissions[-1].sql == read.sql
    assert session.submissions[-1].state == "succeeded"

    empty_expression = expression.filter(expression.id < 0)
    empty = session.compile(
        qualified,
        empty_expression,
        purpose="source_adapter.empty",
        expected_schema=empty_expression.schema().to_pyarrow(),
    )
    empty_stream = session.batches(empty, chunk_size=1)
    assert list(empty_stream) == []
    assert empty_stream.schema.equals(empty.schema, check_metadata=False)

    early = session.batches(read, chunk_size=1)
    assert next(iter(early)).num_rows == 1
    early.close()
    early_submission = session.submissions[-1]
    assert early_submission.state == "closed_early"
    assert early_submission.cursor_state == "closed"
    assert not session._streams


def _expect_postgres_table_facts(metadata: TableMetadata) -> None:
    assert metadata.is_view is False
    assert metadata.view_definition is None
    assert metadata.partition_state == "none"
    by_name = {column.name: column for column in metadata.columns}
    assert by_name["id"].nullable is True
    assert metadata.primary_keys == ()
    assert not any(warning.kind == "nullable_unavailable" for warning in metadata.warnings)


def _expect_postgres_view_facts(metadata: TableMetadata) -> None:
    assert metadata.is_view is True
    assert metadata.view_definition is not None
    assert "SELECT" in metadata.view_definition.upper()


def _expect_mysql_table_facts(metadata: TableMetadata) -> None:
    assert metadata.is_view is False
    assert metadata.view_definition is None
    assert metadata.partition_state == "none"
    by_name = {column.name: column for column in metadata.columns}
    assert by_name["id"].nullable is True
    assert not any(warning.kind == "nullable_unavailable" for warning in metadata.warnings)


def _expect_mysql_view_facts(metadata: TableMetadata) -> None:
    assert metadata.is_view is True
    # A SELECT-only account sees the view kind but MySQL requires SHOW VIEW
    # privilege to disclose VIEW_DEFINITION, which stays unavailable here.
    assert metadata.view_definition is None


def _expect_trino_table_facts(metadata: TableMetadata) -> None:
    assert metadata.is_view is False
    assert metadata.view_definition is None
    by_name = {column.name: column for column in metadata.columns}
    assert by_name["id"].nullable is True
    assert not any(warning.kind == "view_unavailable" for warning in metadata.warnings)


def _expect_trino_view_facts(metadata: TableMetadata) -> None:
    assert metadata.is_view is True
    assert metadata.view_definition is not None


def _expect_clickhouse_table_facts(metadata: TableMetadata) -> None:
    assert metadata.is_view is False
    assert metadata.view_definition is None
    assert metadata.partition_state == "none"
    # The SELECT-only reader account cannot read system.parts, so the physical
    # profile is disclosed as unavailable rather than fabricated.
    assert metadata.physical_profile is None
    assert any(
        warning.kind == "metadata_query_failed" and "physical profile" in warning.message
        for warning in metadata.warnings
    )
    assert any(warning.kind == "projectable_columns_unavailable" for warning in metadata.warnings)
    # ClickHouse has no primary-key concept; the absence stays disclosed.
    assert any(warning.kind == "primary_keys_unavailable" for warning in metadata.warnings)


def _expect_clickhouse_view_facts(metadata: TableMetadata) -> None:
    assert metadata.is_view is True
    assert metadata.view_definition is not None


@pytest.mark.skipif(
    os.environ.get("MARIVO_POSTGRES_ANALYSIS_TEST") != "1", reason="opt-in PostgreSQL service"
)
def test_postgres_table_view_namespace_and_exact_decimal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from psycopg import sql

    from tests.datasource.environment import postgres_analysis as pg

    name = "source_adapter_" + uuid4().hex
    view = name + "_view"
    monkeypatch.setenv("MARIVO_SOURCE_ADAPTER_POSTGRES_PASSWORD", pg.password())
    with pg.connection(admin=True) as admin:
        admin.execute(
            sql.SQL("CREATE TABLE {} (id BIGINT, amount NUMERIC(18, 2))").format(
                sql.Identifier(name)
            )
        )
        admin.execute(
            sql.SQL("INSERT INTO {} VALUES (1, 10.25), (2, 20.50)").format(sql.Identifier(name))
        )
        admin.execute(
            sql.SQL("CREATE VIEW {} AS SELECT id, amount FROM {}").format(
                sql.Identifier(view), sql.Identifier(name)
            )
        )
        try:
            datasource = _datasource(
                "postgres",
                {"host": pg.HOST, "port": pg.PORT, "database": pg.DATABASE, "user": pg.READER},
                {"password": "MARIVO_SOURCE_ADAPTER_POSTGRES_PASSWORD"},
            )
            with provider_for("postgres").open(datasource) as session:
                expected = [
                    {"id": 1, "amount": Decimal("10.25")},
                    {"id": 2, "amount": Decimal("20.50")},
                ]
                _assert_table_read(
                    session,
                    TableSourceIR(name, database="public"),
                    identity=name,
                    expected=expected,
                    expected_metadata=_expect_postgres_table_facts,
                )
                _assert_table_read(
                    session,
                    TableSourceIR(view, database="public"),
                    identity=view,
                    expected=expected,
                    expected_metadata=_expect_postgres_view_facts,
                )
                assert session.interrupt() == "remote_unknown"
                assert all(item.connection_disconnected for item in session.submissions)
        finally:
            admin.execute(sql.SQL("DROP VIEW IF EXISTS {}").format(sql.Identifier(view)))
            admin.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(name)))


@pytest.mark.skipif(
    os.environ.get("MARIVO_MYSQL_ANALYSIS_TEST") != "1", reason="opt-in MySQL service"
)
def test_mysql_table_view_and_exact_decimal(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.datasource.environment import mysql_analysis as mysql

    name = "source_adapter_" + uuid4().hex
    view = name + "_view"
    monkeypatch.setenv("MARIVO_SOURCE_ADAPTER_MYSQL_PASSWORD", mysql.password())
    with mysql.connection(admin=True) as admin, admin.cursor() as cursor:
        cursor.execute(f"CREATE TABLE {name} (id BIGINT, amount DECIMAL(18,2)) ENGINE=InnoDB")
        cursor.execute(f"INSERT INTO {name} VALUES (1,10.25),(2,20.50)")
        cursor.execute(f"CREATE VIEW {view} AS SELECT id, amount FROM {name}")
        try:
            datasource = _datasource(
                "mysql",
                {
                    "host": mysql.HOST,
                    "port": mysql.PORT,
                    "database": mysql.DATABASE,
                    "user": mysql.READER,
                },
                {"password": "MARIVO_SOURCE_ADAPTER_MYSQL_PASSWORD"},
            )
            with provider_for("mysql").open(datasource) as session:
                expected = [
                    {"id": 1, "amount": Decimal("10.25")},
                    {"id": 2, "amount": Decimal("20.50")},
                ]
                _assert_table_read(
                    session,
                    TableSourceIR(name),
                    identity=name,
                    expected=expected,
                    expected_metadata=_expect_mysql_table_facts,
                )
                _assert_table_read(
                    session,
                    TableSourceIR(view),
                    identity=view,
                    expected=expected,
                    expected_metadata=_expect_mysql_view_facts,
                )
                assert session.interrupt() == "remote_unknown"
                assert all(item.connection_disconnected for item in session.submissions)
        finally:
            cursor.execute(f"DROP VIEW IF EXISTS {view}")
            cursor.execute(f"DROP TABLE IF EXISTS {name}")


@pytest.mark.skipif(
    os.environ.get("MARIVO_MYSQL_ANALYSIS_TEST") != "1", reason="opt-in MySQL service"
)
def test_mysql_invalid_date_and_read_only_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    import MySQLdb

    from tests.datasource.environment import mysql_analysis as mysql

    name = "source_adapter_" + uuid4().hex
    monkeypatch.setenv("MARIVO_SOURCE_ADAPTER_MYSQL_PASSWORD", mysql.password())
    with mysql.connection(admin=True) as admin, admin.cursor() as cursor:
        cursor.execute(f"CREATE TABLE {name} (id BIGINT, day DATE) ENGINE=InnoDB")
        cursor.execute("SET SESSION sql_mode='ALLOW_INVALID_DATES'")
        cursor.execute(f"INSERT INTO {name} VALUES (1, '0000-00-00'), (2, '2026-09-26')")
        try:
            datasource = _datasource(
                "mysql",
                {
                    "host": mysql.HOST,
                    "port": mysql.PORT,
                    "database": mysql.DATABASE,
                    "user": mysql.READER,
                },
                {"password": "MARIVO_SOURCE_ADAPTER_MYSQL_PASSWORD"},
            )
            with provider_for("mysql").open(datasource) as session:
                binding = session.bind(TableSourceIR(name), source_identity=name)
                qualified = session.qualify(
                    binding,
                    PhysicalRequirement("source_adapter.invalid_date", 1, frozenset({"scan"})),
                )
                expression = binding.relation.select("day")
                read = session.compile(
                    qualified,
                    expression,
                    purpose="source_adapter.invalid_date",
                    expected_schema=expression.schema().to_pyarrow(),
                )
                with pytest.raises(DatasourceSourceCapabilityError):
                    list(session.batches(read, chunk_size=1))
                assert session.submissions[-1].state == "failed"
                valid = binding.relation.filter(binding.relation.id == 2).select("day")
                valid_read = session.compile(
                    qualified,
                    valid,
                    purpose="source_adapter.valid_date",
                    expected_schema=valid.schema().to_pyarrow(),
                )
                valid_stream = session.batches(valid_read, chunk_size=1)
                assert pa.Table.from_batches(
                    valid_stream, schema=valid_stream.schema
                ).to_pylist() == [{"day": date(2026, 9, 26)}]
            with (
                mysql.connection() as reader,
                reader.cursor() as read_cursor,
                pytest.raises(MySQLdb.OperationalError),
            ):
                read_cursor.execute("CREATE TABLE source_adapter_forbidden (id INT)")
        finally:
            cursor.execute(f"DROP TABLE IF EXISTS {name}")


@pytest.mark.skipif(
    os.environ.get("MARIVO_TRINO_ANALYSIS_TEST") != "1", reason="opt-in Trino service"
)
def test_trino_iceberg_and_non_iceberg_forms() -> None:
    from tests.datasource.environment import trino_analysis as trino

    trino.setup()
    trino.setup_non_iceberg()
    name = "source_adapter_" + uuid4().hex
    view = name + "_view"
    expected = [{"id": 1, "amount": Decimal("10.25")}]
    for catalog in ("iceberg", "noniceberg"):
        with trino.connection(admin=True, catalog=catalog) as admin:
            cursor = admin.cursor()
            try:
                cursor.execute(
                    f"CREATE TABLE {catalog}.analysis.{name} (id BIGINT, amount DECIMAL(9, 2))"
                ).fetchall()
                cursor.execute(
                    f"INSERT INTO {catalog}.analysis.{name} VALUES (1, DECIMAL '10.25')"
                ).fetchall()
                cursor.execute(
                    f"CREATE VIEW {catalog}.analysis.{view} AS SELECT id, amount FROM {catalog}.analysis.{name}"
                ).fetchall()
                datasource = _datasource(
                    "trino",
                    {
                        "host": "127.0.0.1",
                        "port": 18080,
                        "catalog": catalog,
                        "schema": "analysis",
                        "user": "analysis_reader",
                        "timezone": "UTC",
                    },
                )
                with provider_for("trino").open(datasource) as session:
                    _assert_table_read(
                        session,
                        TableSourceIR(name, database=(catalog, "analysis")),
                        identity=f"{catalog}.{name}",
                        expected=expected,
                        expected_metadata=_expect_trino_table_facts,
                    )
                    _assert_table_read(
                        session,
                        TableSourceIR(view, database=(catalog, "analysis")),
                        identity=f"{catalog}.{view}",
                        expected=expected,
                        expected_metadata=_expect_trino_view_facts,
                    )
                    assert session.interrupt() == "remote_unknown"
                    assert all(item.connection_disconnected for item in session.submissions)
            finally:
                cursor.execute(f"DROP VIEW IF EXISTS {catalog}.analysis.{view}").fetchall()
                cursor.execute(f"DROP TABLE IF EXISTS {catalog}.analysis.{name}").fetchall()
                cursor.close()


@pytest.mark.skipif(
    os.environ.get("MARIVO_TRINO_ANALYSIS_TEST") != "1", reason="opt-in Trino service"
)
def test_trino_iceberg_partition_metadata_uses_bound_read() -> None:
    from marivo.datasource.engines.base import PartitionProbeRequest
    from marivo.datasource.engines.trino import inspect_partition_values
    from tests.datasource.environment import trino_analysis as trino

    name = "source_adapter_part_" + uuid4().hex
    with trino.connection(admin=True) as admin:
        cursor = admin.cursor()
        try:
            cursor.execute(
                f"CREATE TABLE iceberg.analysis.{name} (id BIGINT, day DATE) "
                "WITH (partitioning = ARRAY['day'])"
            ).fetchall()
            cursor.execute(
                f"INSERT INTO iceberg.analysis.{name} VALUES "
                "(1, DATE '2026-09-26'), (2, DATE '2026-09-27')"
            ).fetchall()
            datasource = _datasource(
                "trino",
                {
                    "host": "127.0.0.1",
                    "port": 18080,
                    "catalog": "iceberg",
                    "schema": "analysis",
                    "user": "analysis_reader",
                    "timezone": "UTC",
                },
            )
            with provider_for("trino").open(datasource) as owner:
                request = PartitionProbeRequest(
                    backend=owner._backend,
                    datasource_ir=datasource,
                    source=TableSourceIR(name, database=("iceberg", "analysis")),
                    partition_columns=("day",),
                    limit=3,
                    order="asc",
                )
                result = inspect_partition_values(request)
                assert result.value_source == "metadata"
                assert result.rows == (
                    {"day": date(2026, 9, 26)},
                    {"day": date(2026, 9, 27)},
                )
        finally:
            cursor.execute(f"DROP TABLE IF EXISTS iceberg.analysis.{name}").fetchall()
            cursor.close()


@pytest.mark.skipif(
    os.environ.get("MARIVO_CLICKHOUSE_ANALYSIS_TEST") != "1", reason="opt-in ClickHouse service"
)
def test_clickhouse_mergetree_exact_decimal(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.datasource.environment import clickhouse_analysis as ch
    from tests.datasource.environment.credentials import password

    ch.setup()
    name = "source_adapter_" + uuid4().hex
    view = name + "_view"
    monkeypatch.setenv("MARIVO_SOURCE_ADAPTER_CLICKHOUSE_PASSWORD", password())
    with ch.connection(admin=True) as admin:
        admin.command(
            f"CREATE TABLE {name} (id Int64, amount Decimal(18,2)) ENGINE=MergeTree ORDER BY id"
        )
        admin.command(f"INSERT INTO {name} VALUES (1,10.25),(2,20.50)")
        admin.command(f"CREATE VIEW {view} AS SELECT id, amount FROM {name}")
        try:
            datasource = _datasource(
                "clickhouse",
                {
                    "host": "127.0.0.1",
                    "port": 18123,
                    "database": "qualification",
                    "user": "analysis_reader",
                },
                {"password": "MARIVO_SOURCE_ADAPTER_CLICKHOUSE_PASSWORD"},
            )
            with provider_for("clickhouse").open(datasource) as session:
                _assert_table_read(
                    session,
                    TableSourceIR(name, database="qualification"),
                    identity=name,
                    expected=[
                        {"id": 1, "amount": Decimal("10.25")},
                        {"id": 2, "amount": Decimal("20.50")},
                    ],
                    expected_metadata=_expect_clickhouse_table_facts,
                )
                _assert_table_read(
                    session,
                    TableSourceIR(view, database="qualification"),
                    identity=view,
                    expected=[
                        {"id": 1, "amount": Decimal("10.25")},
                        {"id": 2, "amount": Decimal("20.50")},
                    ],
                    expected_metadata=_expect_clickhouse_view_facts,
                )
                assert session.interrupt() == "remote_unknown"
                assert all(item.connection_disconnected for item in session.submissions)
        finally:
            admin.command(f"DROP VIEW IF EXISTS {view}")
            admin.command(f"DROP TABLE IF EXISTS {name}")


@pytest.mark.skipif(
    os.environ.get("MARIVO_CLICKHOUSE_ANALYSIS_TEST") != "1", reason="opt-in ClickHouse service"
)
def test_clickhouse_partition_catalog_uses_bound_read(monkeypatch: pytest.MonkeyPatch) -> None:
    from clickhouse_connect.driver.exceptions import DatabaseError

    from marivo.datasource.engines.base import PartitionProbeRequest
    from marivo.datasource.engines.clickhouse import inspect_partition_values
    from tests.datasource.environment import clickhouse_analysis as ch
    from tests.datasource.environment.credentials import password

    ch.setup()
    name = "source_adapter_part_" + uuid4().hex
    monkeypatch.setenv("MARIVO_SOURCE_ADAPTER_CLICKHOUSE_PASSWORD", password())
    with ch.connection(admin=True) as admin:
        admin.command(
            f"CREATE TABLE {name} (id Int64, dt String) "
            "ENGINE=MergeTree PARTITION BY dt ORDER BY id"
        )
        admin.command(f"INSERT INTO {name} VALUES (1,'20260926'),(2,'20260927')")
        try:
            datasource = _datasource(
                "clickhouse",
                {
                    "host": "127.0.0.1",
                    "port": 18123,
                    "database": "qualification",
                    "user": "analysis_reader",
                },
                {"password": "MARIVO_SOURCE_ADAPTER_CLICKHOUSE_PASSWORD"},
            )
            with provider_for("clickhouse").open(datasource) as owner:
                request = PartitionProbeRequest(
                    backend=owner._backend,
                    datasource_ir=datasource,
                    source=TableSourceIR(name, database="qualification"),
                    partition_columns=("dt",),
                    limit=3,
                    order="asc",
                )
                with pytest.raises(DatabaseError, match="ACCESS_DENIED"):
                    inspect_partition_values(request)
            qualified = _datasource(
                "clickhouse",
                {**datasource.fields, "user": "qualifier"},
                datasource.env_refs,
            )
            with provider_for("clickhouse").open(qualified) as owner:
                request = PartitionProbeRequest(
                    backend=owner._backend,
                    datasource_ir=qualified,
                    source=TableSourceIR(name, database="qualification"),
                    partition_columns=("dt",),
                    limit=3,
                    order="asc",
                )
                result = inspect_partition_values(request)
                assert result.value_source == "system_catalog"
                assert result.rows == ({"dt": "20260926"}, {"dt": "20260927"})
        finally:
            admin.command(f"DROP TABLE IF EXISTS {name}")


@pytest.mark.skipif(
    os.environ.get("MARIVO_CLICKHOUSE_CLUSTER_TEST") != "1", reason="opt-in ClickHouse cluster"
)
def test_clickhouse_distributed_reads_both_shards(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.datasource.environment import clickhouse_analysis as ch
    from tests.datasource.environment.credentials import password

    ch.setup_cluster()
    suffix = "source_adapter_" + uuid4().hex
    monkeypatch.setenv("MARIVO_SOURCE_ADAPTER_CLICKHOUSE_PASSWORD", password())
    ch.create_cluster_tables(suffix)
    try:
        datasource = _datasource(
            "clickhouse",
            {
                "host": "127.0.0.1",
                "port": 18201,
                "database": "qualification_cluster",
                "user": "analysis_reader",
            },
            {"password": "MARIVO_SOURCE_ADAPTER_CLICKHOUSE_PASSWORD"},
        )
        with provider_for("clickhouse").open(datasource) as session:
            _assert_table_read(
                session,
                TableSourceIR(f"orders_{suffix}_distributed", database="qualification_cluster"),
                identity=suffix,
                expected=[{"id": value, "amount": Decimal(f"{value}.25")} for value in range(5)],
            )
            assert session.interrupt() == "remote_unknown"
            assert all(item.connection_disconnected for item in session.submissions)
    finally:
        ch.drop_cluster_tables(suffix)
