"""Service-free ownership and failure probes for Trino's concrete cursor boundary."""

from threading import Event, Thread
from unittest.mock import Mock

import pytest
from ibis.backends.trino import Backend

from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.trino_execution import TrinoExecutionAdapter


@pytest.mark.parametrize("operation", ["execute", "fetch"])
def test_interrupt_owns_cursor_even_during_first_page(operation: str) -> None:
    backend = Mock(spec=Backend)
    backend.con = Mock()
    native = backend.con.cursor.return_value
    entered, released = Event(), Event()
    failure = OSError("blocked driver interrupted")

    def blocked(*args: object) -> None:
        entered.set()
        assert released.wait(5)
        raise failure

    getattr(native, "execute" if operation == "execute" else "fetchmany").side_effect = blocked
    native.close.side_effect = released.set
    adapter = TrinoExecutionAdapter(backend)
    errors: list[BaseException] = []

    def run() -> None:
        try:
            adapter.submit(adapter.statement("SELECT 1"))
        except BaseException as error:
            errors.append(error)

    worker = Thread(target=run)
    worker.start()
    try:
        assert entered.wait(5)
        assert len(adapter._cursors) == 1
        adapter.interrupt()
    finally:
        released.set()
        worker.join(5)
    assert not worker.is_alive()
    assert errors == [failure]
    native.close.assert_called_once()
    backend.disconnect.assert_called_once()
    assert not adapter._cursors


@pytest.mark.parametrize("failure", ["execute", "fetch", "decode"])
def test_original_failure_survives_lost_cancel(failure: str) -> None:
    backend = Mock(spec=Backend)
    backend.con = Mock()
    native = backend.con.cursor.return_value
    original = OSError("transport failed")
    if failure == "execute":
        native.execute.side_effect = original
    elif failure == "fetch":
        native.fetchmany.side_effect = original
    else:
        native.fetchmany.return_value = [(float("nan"),)]
    native.close.side_effect = OSError("cancel acknowledgement lost")
    adapter = TrinoExecutionAdapter(backend)
    with pytest.raises(MaterializationError if failure == "decode" else OSError) as caught:
        adapter.submit(adapter.statement("SELECT 1"))
    if failure != "decode":
        assert caught.value is original
    assert len(adapter._cursors) == 1
    with pytest.raises(OSError, match="cancel acknowledgement"):
        adapter.interrupt()
    backend.disconnect.assert_called_once()
    assert adapter._closed


def test_foreign_statement_rejected_before_cursor_creation() -> None:
    backend = Mock(spec=Backend)
    backend.con = Mock()
    first, second = TrinoExecutionAdapter(backend), TrinoExecutionAdapter(backend)
    with pytest.raises(MaterializationError):
        second.submit(first.statement("SELECT 1"))
    backend.con.cursor.assert_not_called()


@pytest.mark.parametrize(
    "kind",
    ["int8", "int16", "array<int64>", "decimal(39,0)", "decimal(4,5)"],
)
def test_unqualified_declared_types_are_not_supported(kind: str) -> None:
    from marivo.analysis.operators.trino_support import supported_type

    assert not supported_type(kind)


def test_disconnect_closes_partial_streams() -> None:
    import ibis

    backend = Mock(spec=Backend)
    backend.con = Mock()
    native = backend.con.cursor.return_value
    native.fetchmany.return_value = [(1,)]
    adapter = TrinoExecutionAdapter(backend)
    # Use an already-owned Statement to exercise real stream ownership without a compiler mock.
    from dataclasses import replace

    statement = replace(
        adapter.statement("SELECT 1"), schema=ibis.schema({"id": "int64"}).to_pyarrow()
    )
    stream = adapter.batches(statement, chunk_size=1)
    iterator = iter(stream)
    assert next(iterator).num_rows == 1
    adapter.disconnect()
    assert not adapter._streams and not adapter._cursors
    native.close.assert_called_once()
    with pytest.raises(MaterializationError):
        list(stream)


def test_closed_cursor_cannot_submit_unowned_work() -> None:
    backend = Mock(spec=Backend)
    backend.con = Mock()
    adapter = TrinoExecutionAdapter(backend)
    cursor = adapter.cursor(stream=False)
    cursor.close()
    with pytest.raises(MaterializationError, match="cursor is closed"):
        cursor.execute("SELECT 1")
    with pytest.raises(MaterializationError, match="cursor is closed"):
        cursor.fetchmany(1)
    backend.con.cursor.return_value.execute.assert_not_called()
