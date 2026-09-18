"""Service-free Native transport ownership and error regression checks."""

from dataclasses import replace
from threading import Event, Thread
from unittest.mock import Mock

import ibis
import pytest
from ibis.backends.clickhouse import Backend

from marivo.analysis.materialization.clickhouse_execution import ClickHouseExecutionAdapter
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.operators.clickhouse_support import supported_type


def adapter_and_stream(rows=()):
    backend = Mock(spec=Backend)
    backend.con = Mock()
    native = Mock()
    native.__enter__ = Mock(return_value=iter(rows))
    native.__exit__ = Mock()
    backend.con.query_rows_stream.return_value = native
    return ClickHouseExecutionAdapter(backend), native


@pytest.mark.parametrize(
    "kind",
    [
        "timestamp('UTC', 9)",
        "array<int64>",
        "decimal(39,0)",
        "decimal(4,5)",
    ],
)
def test_unqualified_types(kind: str) -> None:
    assert not supported_type(kind)


def test_statement_ownership_and_closed_cursor() -> None:
    first, _ = adapter_and_stream()
    second, _ = adapter_and_stream()
    with pytest.raises(MaterializationError):
        second.submit(first.statement("SELECT 1"))
    second._clickhouse.con.query_rows_stream.assert_not_called()
    cursor = first.cursor(stream=False)
    cursor.close()
    with pytest.raises(MaterializationError):
        cursor.execute("SELECT 1")


@pytest.mark.parametrize("phase", ["submit", "fetch", "decode"])
def test_original_failure_survives_cleanup(phase: str) -> None:
    original = OSError("original transport failure")

    def broken():
        raise original
        yield

    adapter, native = adapter_and_stream([(float("nan"),)] if phase == "decode" else broken())
    if phase == "submit":
        adapter._clickhouse.con.query_rows_stream.side_effect = original
    native.__exit__.side_effect = OSError("cleanup failure")
    with pytest.raises(MaterializationError if phase == "decode" else OSError) as caught:
        adapter.submit(adapter.statement("SELECT 1"))
    if phase != "decode":
        assert caught.value is original


def test_late_response_after_interrupt_is_closed() -> None:
    adapter, native = adapter_and_stream([(1,)])
    entered, release = Event(), Event()

    def submit(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return native

    adapter._clickhouse.con.query_rows_stream.side_effect = submit
    errors = []

    def run():
        try:
            adapter.submit(adapter.statement("SELECT 1"))
        except BaseException as error:
            errors.append(error)

    worker = Thread(target=run)
    worker.start()
    try:
        assert entered.wait(5)
        adapter.interrupt()
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], MaterializationError)
    native.__exit__.assert_called_once()
    assert not adapter._cursors


def test_fetch_interrupt_and_partial_stream_cleanup() -> None:
    adapter, native = adapter_and_stream([(1,), (2,)])
    statement = replace(
        adapter.statement("SELECT id"), schema=ibis.schema({"id": "int64"}).to_pyarrow()
    )
    stream = adapter.batches(statement, chunk_size=1)
    iterator = iter(stream)
    assert next(iterator).num_rows == 1
    adapter.interrupt()
    native.__exit__.assert_called_once()
    assert not adapter._streams and not adapter._cursors
    with pytest.raises(MaterializationError):
        next(iterator)


@pytest.mark.parametrize("rows", [[(2**63,)], [("wrong type",)], [(1, 2), ()]])
def test_invalid_native_values_cannot_publish(rows) -> None:
    adapter, native = adapter_and_stream(rows)
    statement = replace(
        adapter.statement("SELECT id"), schema=ibis.schema({"id": "int64"}).to_pyarrow()
    )
    with pytest.raises((MaterializationError, IndexError)):
        adapter.read_table(statement)
    native.__exit__.assert_called_once()


def test_exact_decimal_identity_and_large_cell() -> None:
    from decimal import Decimal

    adapter, _ = adapter_and_stream([(Decimal("1234567890123456789012.123456"), "x" * 300000)])
    from marivo.analysis.materialization.scalar_sql_execution import ScalarStatement

    statement = ScalarStatement(
        "SELECT identity",
        (),
        ibis.schema({"identity": "struct<id:decimal(28,6),label:string>"}).to_pyarrow(),
        "primary",
        adapter._context,
        columns=((0, 1),),
    )
    table = adapter.read_table(statement)
    assert table.to_pylist() == [
        {"identity": {"id": Decimal("1234567890123456789012.123456"), "label": "x" * 300000}}
    ]


def test_interrupt_during_blocked_fetch() -> None:
    entered, released = Event(), Event()

    def rows():
        entered.set()
        assert released.wait(5)
        yield (1,)

    adapter, native = adapter_and_stream(rows())
    native.__exit__.side_effect = lambda *args: released.set()
    errors = []

    def read():
        try:
            adapter.submit(adapter.statement("SELECT 1"))
        except BaseException as error:
            errors.append(error)

    worker = Thread(target=read)
    worker.start()
    try:
        assert entered.wait(5)
        adapter.interrupt()
    finally:
        released.set()
        worker.join(5)
    assert not worker.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], MaterializationError)
    assert not adapter._cursors
    native.__exit__.assert_called_once()
