"""Real cursor and exact scalar projection checks for MySQL and SQLite."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator, Sequence
from dataclasses import replace
from datetime import date
from pathlib import Path
from uuid import uuid4

import ibis
import pyarrow as pa
import pytest

from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.execution import Parameter
from marivo.analysis.materialization.mysql_execution import MySQLExecutionAdapter
from marivo.analysis.materialization.scalar_sql_execution import ScalarExecutionAdapter
from marivo.analysis.materialization.sqlite_execution import SQLiteExecutionAdapter

pytestmark = pytest.mark.runtime


@pytest.fixture(params=["sqlite", "mysql"])
def adapter(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ScalarExecutionAdapter]:
    if request.param == "sqlite":
        backend = ibis.sqlite.connect(tmp_path / "adapter.sqlite")
        backend.con.execute("CREATE TABLE rows (id INTEGER, amount REAL, label TEXT, day DATE)")
        backend.con.executemany(
            "INSERT INTO rows VALUES (?,?,?,?)",
            [(2**53 + i, float(i), f"x'{i}", "2026-02-02") for i in range(17)],
        )
        backend.con.commit()
        instance = SQLiteExecutionAdapter(backend)
        instance.initialize()
        try:
            yield instance
        finally:
            instance.disconnect()
    else:
        if os.environ.get("MARIVO_MYSQL_ANALYSIS_TEST") != "1":
            pytest.skip("opt-in MySQL service")
        from tests.multisource_environment import mysql_analysis as mysql

        table = "adapter_" + uuid4().hex
        with mysql.connection(admin=True) as admin, admin.cursor() as cursor:
            cursor.execute(
                f"CREATE TABLE {table}(id BIGINT,amount DOUBLE,label TEXT COLLATE utf8mb4_0900_bin,day DATE) ENGINE=InnoDB"
            )
            cursor.executemany(
                f"INSERT INTO {table} VALUES (%s,%s,%s,%s)",
                [(2**53 + i, float(i), f"x'{i}", "2026-02-02") for i in range(17)],
            )
            native = ibis.mysql.connect(
                host=mysql.HOST,
                port=mysql.PORT,
                database=mysql.DATABASE,
                user=mysql.READER,
                password=mysql.password(),
            )
            instance = MySQLExecutionAdapter(native)
            # The fixture supplies the physical table name without any production alias.
            request.node._scalar_table = table
            try:
                yield instance
            finally:
                instance.disconnect()
                cursor.execute(f"DROP TABLE {table}")


def table_for(adapter: ScalarExecutionAdapter, request: pytest.FixtureRequest):
    name = getattr(request.node, "_scalar_table", "rows")
    return ibis.table(
        {"id": "int64", "amount": "float64", "label": "string", "day": "date"}, name=name
    )


@pytest.mark.parametrize("empty", [False, True])
def test_batches_exact_identity_params_and_no_ambient_limit(
    adapter: ScalarExecutionAdapter, request: pytest.FixtureRequest, empty: bool
) -> None:
    table = table_for(adapter, request)
    parameter = ibis.param("int64")
    filtered = table.filter(table.id >= parameter)
    expression = filtered.select(
        entity_identity=ibis.struct(
            {"label": filtered.label, "id": filtered.id, "day": filtered.day}
        ),
        amount=filtered.amount,
    ).order_by("entity_identity")
    previous = ibis.options.sql.default_limit
    ibis.options.sql.default_limit = 1
    try:
        stream = adapter.batches(
            expression, chunk_size=3, params={parameter: 2**60 if empty else 0}
        )
        batches = list(stream)
    finally:
        ibis.options.sql.default_limit = previous
    result = pa.Table.from_batches(batches, schema=stream.schema)
    assert result.schema == expression.schema().to_pyarrow()
    assert [batch.num_rows for batch in batches] == ([] if empty else [3, 3, 3, 3, 3, 2])
    if not empty:
        assert sorted(row["entity_identity"]["id"] for row in result.to_pylist()) == list(
            range(2**53, 2**53 + 17)
        )
        assert all(row["entity_identity"]["day"] == date(2026, 2, 2) for row in result.to_pylist())
    assert not adapter._streams


def test_partial_close_and_owned_statements(
    adapter: ScalarExecutionAdapter, request: pytest.FixtureRequest
) -> None:
    table = table_for(adapter, request)
    stream = adapter.batches(table, chunk_size=2)
    iterator = iter(stream)
    assert next(iterator).num_rows == 2
    stream.close()
    stream.close()
    assert not adapter._streams
    with pytest.raises(MaterializationError):
        list(stream)
    from marivo.analysis.materialization.execution import ExecutionContext

    statement = replace(adapter.prepare(table), context=ExecutionContext())
    with pytest.raises(MaterializationError, match="foreign"):
        adapter.batches(statement, chunk_size=2)
    assert adapter.read_scalar(table.count()) == 17


def test_decode_failure_closes_cursor(
    adapter: ScalarExecutionAdapter, request: pytest.FixtureRequest
) -> None:
    table = table_for(adapter, request)
    statement = replace(
        adapter.prepare(table.select("label")), schema=pa.schema([("label", pa.int64())])
    )
    with pytest.raises((pa.ArrowInvalid, pa.ArrowTypeError)):
        list(adapter.batches(statement, chunk_size=2))
    assert not adapter._streams
    assert adapter.read_scalar(table.count()) == 17


@pytest.mark.parametrize("fetch", [False, True])
def test_sqlite_interrupt_during_execute_and_fetch(tmp_path: Path, fetch: bool) -> None:
    backend = ibis.sqlite.connect(tmp_path / "interrupt.sqlite")
    adapter = SQLiteExecutionAdapter(backend)
    sql = "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<1000000) SELECT x FROM n"
    if not fetch:
        sql = f"SELECT sum(x) FROM ({sql})"
    statement = replace(adapter.statement(sql), schema=pa.schema([("x", pa.int64())]))
    calls = 0

    def progress() -> int:
        nonlocal calls
        calls += 1
        if calls == 10:
            adapter.interrupt()
        return 0

    try:
        if fetch:
            stream = adapter.batches(statement, chunk_size=100)
            iterator = iter(stream)
            assert next(iterator).num_rows == 100
            backend.con.set_progress_handler(progress, 100)
            with pytest.raises(sqlite3.OperationalError, match="interrupt"):
                list(iterator)
        else:
            backend.con.set_progress_handler(progress, 100)
            with pytest.raises(sqlite3.OperationalError, match="interrupt"):
                adapter.batches(statement, chunk_size=100)
        assert calls >= 10 and not adapter._streams
        backend.con.set_progress_handler(None, 0)
        assert adapter.read_scalar(adapter.statement("SELECT 1")) == 1
    finally:
        adapter.disconnect()


def test_sqlite_nonfinite_and_read_only(tmp_path: Path) -> None:
    backend = ibis.sqlite.connect(tmp_path / "finite.sqlite")
    backend.con.execute("CREATE TABLE values_table(value REAL)")
    backend.con.executemany(
        "INSERT INTO values_table VALUES (?)",
        [
            (None,),
            (float("nan"),),
            (float("inf"),),
            (-float("inf"),),
            (1.7976931348623157e308,),
            (-1.7976931348623157e308,),
            (0.0,),
        ],
    )
    backend.con.commit()
    adapter = SQLiteExecutionAdapter(backend)
    adapter.initialize()
    table = ibis.table({"value": "float64"}, name="values_table")
    result = adapter.read_table(
        table.filter(table.value.notnull() & ~table.value.isnan() & ~table.value.isinf())
    )
    assert result.column(0).to_pylist() == [1.7976931348623157e308, -1.7976931348623157e308, 0.0]
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        adapter.submit(adapter.statement("INSERT INTO values_table VALUES (1)"))
    adapter.disconnect()


def test_mysql_conversion_warning_cannot_return_success(tmp_path: Path) -> None:
    if os.environ.get("MARIVO_MYSQL_ANALYSIS_TEST") != "1":
        pytest.skip("opt-in MySQL service")
    from tests.multisource_environment import mysql_analysis as mysql

    native = ibis.mysql.connect(
        host=mysql.HOST,
        port=mysql.PORT,
        database=mysql.DATABASE,
        user=mysql.READER,
        password=mysql.password(),
    )
    adapter = MySQLExecutionAdapter(native)
    try:
        with pytest.raises(MaterializationError, match="statement warnings"):
            adapter.read_scalar(adapter.statement("SELECT CAST(1000 AS DECIMAL(2,0))"))
        assert adapter.read_scalar(adapter.statement("SELECT 1")) == 1
    finally:
        adapter.disconnect()


def test_mysql_owned_connection_cancel_after_partial_fetch(
    request: pytest.FixtureRequest, adapter: ScalarExecutionAdapter
) -> None:
    if not isinstance(adapter, MySQLExecutionAdapter):
        pytest.skip("MySQL connection cancellation")
    stream = adapter.batches(table_for(adapter, request), chunk_size=2)
    iterator = iter(stream)
    assert next(iterator).num_rows == 2
    import MySQLdb

    try:
        adapter.interrupt()
    except MySQLdb.OperationalError as error:
        # Closing an unbuffered cursor after its exact connection was closed
        # can report lost connection; cleanup still releases the owned handle.
        assert error.args[0] == 2006
    assert adapter._closed and not adapter._streams
    with pytest.raises(MaterializationError, match="closed"):
        adapter.read_scalar(adapter.statement("SELECT 1"))
    adapter.finish()


def test_lost_real_connection_cannot_complete_stream(
    request: pytest.FixtureRequest, adapter: ScalarExecutionAdapter
) -> None:
    stream = adapter.batches(table_for(adapter, request), chunk_size=2)
    iterator = iter(stream)
    assert next(iterator).num_rows == 2
    adapter._backend.disconnect()
    try:
        with pytest.raises(Exception):
            list(iterator)
        assert not adapter._streams
    finally:
        adapter._closed = True


@pytest.mark.parametrize("collation", ['"BINARY"', "NOCASE"])
def test_sqlite_collation_uses_column_metadata_not_sql_text(tmp_path: Path, collation: str) -> None:
    backend = ibis.sqlite.connect(tmp_path / "collation.sqlite")
    backend.con.execute(
        f"CREATE TABLE \"virtual\"(value TEXT COLLATE {collation} DEFAULT 'COLLATE NOCASE')"
    )
    adapter = SQLiteExecutionAdapter(backend)
    try:
        if collation == "NOCASE":
            with pytest.raises(MaterializationError, match="non-binary"):
                adapter.get_schema("virtual")
        else:
            assert adapter.get_schema("virtual") == ibis.schema({"value": "string"})
    finally:
        adapter.disconnect()


def test_slow_execution_keeps_owned_context(adapter: ScalarExecutionAdapter) -> None:
    from time import monotonic, sleep

    if isinstance(adapter, SQLiteExecutionAdapter):
        delayed = False

        def progress() -> int:
            nonlocal delayed
            if not delayed:
                delayed = True
                sleep(0.05)
            return 0

        adapter._sqlite.con.set_progress_handler(progress, 1)
        sql = "SELECT 1"
    else:
        sql = "SELECT 1 + SLEEP(0.05)"
    started = monotonic()
    try:
        assert adapter.read_scalar(adapter.statement(sql)) == 1
        assert monotonic() - started >= 0.04
    finally:
        if isinstance(adapter, SQLiteExecutionAdapter):
            adapter._sqlite.con.set_progress_handler(None, 0)
    assert adapter.read_scalar(adapter.statement("SELECT 2")) == 2


def test_blocked_fetch_retains_stream_until_release(
    adapter: ScalarExecutionAdapter,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from threading import Event, Thread
    from time import sleep

    stream = adapter.batches(table_for(adapter, request), chunk_size=3)
    native = stream._cursor
    entered, released = Event(), Event()

    class GatedCursor:
        def execute(self, sql: str, parameters: tuple[Parameter, ...] = ()) -> object:
            return native.execute(sql, parameters)

        def fetchmany(self, size: int) -> Sequence[tuple[object, ...]]:
            entered.set()
            assert released.wait(5), "test controller did not release the fetch gate"
            return native.fetchmany(size)

        def close(self) -> None:
            native.close()

    def release() -> None:
        assert entered.wait(5)
        sleep(0.05)
        released.set()

    monkeypatch.setattr(stream, "_cursor", GatedCursor())
    controller = Thread(target=release)
    controller.start()
    try:
        iterator = iter(stream)
        assert next(iterator).num_rows == 3
        assert released.is_set() and stream in adapter._streams
        stream.close()
        assert not adapter._streams
        assert adapter.read_scalar(adapter.statement("SELECT 1")) == 1
    finally:
        released.set()
        controller.join(5)
        stream.close()
