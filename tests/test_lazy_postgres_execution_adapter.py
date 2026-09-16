"""Opt-in real PostgreSQL evidence for cursor ownership and complete transfer."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import ibis
import psycopg
import pyarrow as pa
import pytest
from ibis.backends.postgres import Backend
from psycopg import sql
from psycopg.pq import TransactionStatus

from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.postgres_execution import PostgresExecutionAdapter
from tests.multisource_environment import postgres_analysis as pg

pytestmark = [
    pytest.mark.runtime,
    pytest.mark.skipif(
        os.environ.get("MARIVO_POSTGRES_ANALYSIS_TEST") != "1",
        reason="Requires explicitly started isolated PostgreSQL analysis environment",
    ),
]


@pytest.fixture
def native() -> Iterator[Backend]:
    backend = ibis.postgres.connect(
        host=pg.HOST, port=pg.PORT, database=pg.DATABASE, user=pg.READER, password=pg.password()
    )
    try:
        yield backend
    finally:
        backend.disconnect()


@pytest.fixture
def table_name() -> Iterator[str]:
    name = "adapter_" + uuid4().hex
    with pg.connection(admin=True) as admin:
        admin.execute(
            sql.SQL(
                "CREATE TABLE {} AS SELECT i::bigint AS id, 12.25::numeric(12,2) AS amount, "
                "DATE '2026-01-02' AS day, TIMESTAMPTZ '2026-01-02 12:00:00+00' AS at "
                "FROM generate_series(1, 17) AS t(i)"
            ).format(sql.Identifier(name))
        )
        try:
            yield name
        finally:
            admin.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(name)))


def assert_idle(native: Backend) -> None:
    assert native.con.info.transaction_status == TransactionStatus.IDLE
    with native.con.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM pg_cursors WHERE name LIKE 'marivo_%'")
        assert cursor.fetchone() == (0,)
    assert native.con.info.transaction_status == TransactionStatus.IDLE


@pytest.mark.parametrize("empty", [False, True])
def test_complete_batches_params_schema_and_no_default_limit(
    native: Backend, table_name: str, empty: bool
) -> None:
    adapter = PostgresExecutionAdapter(native)
    table = native.table(table_name)
    parameter = ibis.param("int64")
    expression = table.filter(table.id > parameter).order_by("id")
    submitted: list[tuple[str, str]] = []
    previous = ibis.options.sql.default_limit
    try:
        ibis.options.sql.default_limit = 1
        stream = adapter.batches(
            expression,
            chunk_size=3,
            params={parameter: 100 if empty else 0},
            role="primary",
            record=lambda role, statement: submitted.append((role, statement)),
        )
        batches = list(stream)
    finally:
        ibis.options.sql.default_limit = previous
    result = pa.Table.from_batches(batches, schema=stream.schema)
    assert result.schema == expression.schema().to_pyarrow()
    assert [batch.num_rows for batch in batches] == ([] if empty else [3, 3, 3, 3, 3, 2])
    if not empty:
        assert result.column("id").to_pylist() == list(range(1, 18))
        assert result.to_pylist()[0] == {
            "id": 1,
            "amount": Decimal("12.25"),
            "day": date(2026, 1, 2),
            "at": datetime(2026, 1, 2, 12, tzinfo=timezone.utc),
        }
    assert len(submitted) == 1 and submitted[0][0] == "primary"
    assert_idle(native)


@pytest.mark.parametrize("partial", [False, True])
def test_explicit_stream_close_releases_cursor_and_transaction(
    native: Backend, table_name: str, partial: bool
) -> None:
    adapter = PostgresExecutionAdapter(native)
    stream = adapter.batches(native.table(table_name), chunk_size=2)
    iterator = iter(stream)
    assert native.con.info.transaction_status == TransactionStatus.INTRANS
    with native.con.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM pg_cursors WHERE name LIKE 'marivo_%'")
        assert cursor.fetchone() == (1,)
    if partial:
        assert next(iterator).num_rows == 2
    stream.close()
    stream.close()
    assert_idle(native)


def test_statement_parameters_and_context_ownership(native: Backend) -> None:
    adapter = PostgresExecutionAdapter(native)
    statement = adapter.statement("SELECT %s::numeric(12,2)", parameters=(Decimal("19.25"),))
    assert adapter.read_scalar(statement) == Decimal("19.25")
    other = PostgresExecutionAdapter(native)
    with pytest.raises(MaterializationError):
        other.submit(statement)
    with pytest.raises(MaterializationError):
        other.statement("SELECT 1", inputs=(statement,))
    with pytest.raises(MaterializationError):
        adapter.submit(replace(statement, context=replace(statement.context)))
    assert_idle(native)
    adapter.finish()
    with pytest.raises(MaterializationError):
        adapter.submit(statement)


def test_decode_failure_releases_real_cursor(native: Backend, table_name: str) -> None:
    adapter = PostgresExecutionAdapter(native)
    statement = adapter.prepare(native.table(table_name).select("amount"))
    invalid = replace(statement, schema=pa.schema([("amount", pa.list_(pa.string()))]))
    stream = adapter.batches(invalid, chunk_size=2)
    with pytest.raises((pa.ArrowInvalid, pa.ArrowTypeError, TypeError)):
        list(stream)
    assert_idle(native)
    assert adapter.read_scalar(adapter.statement("SELECT 1")) == 1


@pytest.mark.parametrize("fetch", [False, True])
def test_observed_slow_operation_cancellation_cleans_transaction(
    native: Backend, fetch: bool
) -> None:
    adapter = PostgresExecutionAdapter(native)
    statement = adapter.statement("SELECT pg_sleep(20)")
    if fetch:
        statement = replace(statement, schema=pa.schema([("pg_sleep", pa.string())]))
    stream = adapter.batches(statement, chunk_size=2) if fetch else None
    finished = threading.Event()
    failures: list[BaseException] = []

    def run() -> None:
        try:
            if stream is not None:
                list(stream)
            else:
                adapter.submit(statement)
        except BaseException as error:
            failures.append(error)
        finally:
            finished.set()

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    observed = False
    try:
        deadline = time.monotonic() + 5
        with pg.connection(admin=True) as monitor:
            while time.monotonic() < deadline and not finished.is_set():
                activity = monitor.execute(
                    "SELECT state, wait_event, query FROM pg_stat_activity WHERE pid = %s",
                    (native.con.info.backend_pid,),
                ).fetchone()
                if activity and activity[0] == "active" and activity[1] == "PgSleep":
                    assert isinstance(activity[2], str)
                    assert activity[2].startswith("FETCH") if fetch else "pg_sleep" in activity[2]
                    observed = True
                    break
                finished.wait(0.02)
        assert observed, "Cancellation requires observed server-side execution"
        adapter.interrupt()
        assert finished.wait(5), "Cancelled driver operation did not return"
    finally:
        if not finished.is_set():
            adapter.interrupt()
        worker.join(timeout=5)
        if stream is not None:
            stream.close()
    assert len(failures) == 1
    assert isinstance(failures[0], psycopg.errors.QueryCanceled)
    assert_idle(native)
    assert adapter.read_scalar(adapter.statement("SELECT 7")) == 7


def test_original_decode_error_survives_cleanup_error(
    native: Backend, table_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = PostgresExecutionAdapter(native)
    statement = adapter.prepare(native.table(table_name).select("amount"))
    invalid = replace(statement, schema=pa.schema([("amount", pa.list_(pa.string()))]))
    stream = adapter.batches(invalid, chunk_size=2)
    close = stream.close
    cleanup_attempts: list[bool] = []

    def fail_after_close() -> None:
        close()
        cleanup_attempts.append(True)
        raise RuntimeError("cleanup failure")

    monkeypatch.setattr(stream, "close", fail_after_close)
    monkeypatch.setattr(adapter, "batches", lambda *args, **kwargs: stream)
    with pytest.raises(pa.ArrowTypeError):
        adapter.read_table(invalid)
    assert cleanup_attempts
    assert_idle(native)


def test_connection_loss_during_stream_preserves_driver_error(
    native: Backend, table_name: str
) -> None:
    adapter = PostgresExecutionAdapter(native)
    stream = adapter.batches(native.table(table_name), chunk_size=2)
    iterator = iter(stream)
    assert next(iterator).num_rows == 2
    pid = native.con.info.backend_pid
    with pg.connection(admin=True) as admin:
        assert admin.execute("SELECT pg_terminate_backend(%s)", (pid,)).fetchone() == (True,)
    with pytest.raises(psycopg.OperationalError):
        next(iterator)
    assert native.con.closed
    assert not adapter._streams
    adapter.finish()


def test_schema_lookup_uses_postgres_relation_resolution(native: Backend, table_name: str) -> None:
    adapter = PostgresExecutionAdapter(native)
    assert adapter.get_schema("pg_class") == native.get_schema("pg_class", database="pg_catalog")
    assert adapter.get_schema(table_name, database="public") == native.get_schema(
        table_name, database="public"
    )
    with pytest.raises(MaterializationError):
        adapter.get_schema(table_name, database="pg_catalog")
    assert_idle(native)


@pytest.mark.parametrize("prepared", [False, True])
def test_anonymous_struct_transfer_preserves_exact_leaf_values(
    native: Backend, prepared: bool
) -> None:
    name = "struct_" + uuid4().hex
    values = [
        (-(2**63), 'comma, parentheses() "quotes" and \\backslash', None),
        (2**63 - 1, None, 2**63 - 1),
        (2**53 + 1, "", -(2**63)),
    ]
    with pg.connection(admin=True) as admin:
        admin.execute(
            sql.SQL("CREATE TABLE {} (id bigint, caption text, marker bigint)").format(
                sql.Identifier(name)
            )
        )
        try:
            with admin.cursor() as cursor:
                cursor.executemany(
                    sql.SQL("INSERT INTO {} VALUES (%s, %s, %s)").format(sql.Identifier(name)),
                    values,
                )
            adapter = PostgresExecutionAdapter(native)
            table = native.table(name)
            expression = table.order_by("id").select(
                identity=ibis.struct(
                    {
                        "id": table.id,
                        "caption": table.caption,
                        "marker": table.marker,
                    }
                )
            )
            if prepared:
                result = adapter.read_table(adapter.prepare(expression))
            else:
                stream = adapter.batches(expression, chunk_size=2)
                result = pa.Table.from_batches(stream, schema=stream.schema)
            expected = [
                {
                    "identity": {
                        "id": row[0],
                        "caption": row[1],
                        "marker": row[2],
                    }
                }
                for row in sorted(values, key=lambda row: row[0])
            ]
            assert result.schema == expression.schema().to_pyarrow()
            assert result.to_pylist() == expected
            assert_idle(native)
        finally:
            admin.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(name)))


def test_nested_anonymous_record_uses_typed_binary_leaves(native: Backend) -> None:
    adapter = PostgresExecutionAdapter(native)
    schema = pa.schema(
        [
            (
                "identity",
                pa.struct(
                    [
                        ("id", pa.int64()),
                        (
                            "nested",
                            pa.struct([("caption", pa.string()), ("amount", pa.decimal128(18, 2))]),
                        ),
                    ]
                ),
            ),
        ]
    )
    caption = 'comma, parentheses() "quotes" and \\backslash'
    statement = replace(
        adapter.statement(
            "SELECT ROW(%s::bigint, ROW(%s::text, %s::numeric(18,2))) AS identity",
            parameters=(2**63 - 1, caption, Decimal("123.45")),
        ),
        schema=schema,
    )
    assert adapter.read_table(statement).to_pylist() == [
        {"identity": {"id": 2**63 - 1, "nested": {"caption": caption, "amount": Decimal("123.45")}}}
    ]
    assert_idle(native)
