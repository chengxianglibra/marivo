"""Default, service-free regression tests for exact scalar transport boundaries."""

from datetime import date
from decimal import Decimal
from unittest.mock import Mock

import ibis
import pyarrow as pa
import pytest

from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.execution import ExecutionContext
from marivo.analysis.materialization.scalar_projection import _name, project
from marivo.analysis.materialization.scalar_sql_execution import (
    ScalarBatchStream,
    ScalarExecutionAdapter,
    ScalarStatement,
    _cell,
)
from marivo.analysis.materialization.submissions import ObservedExecution


@pytest.mark.parametrize("value", ["20260202", "2026-W06-1", "2026-02-30", "0000-01-01"])
def test_invalid_date_has_structured_repair(value: str) -> None:
    with pytest.raises(MaterializationError) as caught:
        _cell(value, pa.date32(), run_ref="run:decode")
    assert caught.value.stage == "output_validation"
    assert caught.value.run_ref == "run:decode"
    assert "YYYY-MM-DD" in str(caught.value)


@pytest.mark.parametrize("value", ["1.5", "NaN", "Infinity", "-Infinity"])
def test_invalid_decimal_integer_has_structured_repair(value: str) -> None:
    with pytest.raises(MaterializationError) as caught:
        _cell(Decimal(value), pa.int64(), run_ref="run:decode")
    assert caught.value.stage == "output_validation"
    assert caught.value.run_ref == "run:decode"


def test_valid_cells_preserve_exact_values() -> None:
    assert _cell("9999-12-31", pa.date32()) == date(9999, 12, 31)
    assert _cell(Decimal(2**63 - 1), pa.int64()) == 2**63 - 1
    assert _cell(None, pa.date32()) is None


@pytest.mark.parametrize("nested", [False, True])
def test_identity_projection_guard_is_structured(nested: bool) -> None:
    table = ibis.table({"id": "int64"}, name="rows")
    identity = ibis.struct({"id": table.id})
    expression = (
        table.select(identity=ibis.struct({"nested": identity}))
        if nested
        else table.select(identity=identity, **{_name("identity", "id"): table.id})
    )
    with pytest.raises(MaterializationError) as caught:
        project(expression, run_ref="run:compile")
    assert caught.value.stage == "compilation"
    assert caught.value.run_ref == "run:compile"


@pytest.mark.parametrize("failure", ["execute", "fetch", "date"])
def test_transport_preserves_failure_closes_and_never_retries(failure: str) -> None:
    adapter = Mock(spec=ScalarExecutionAdapter)
    adapter._run_ref = "run:stream"
    adapter._streams = set()
    observer = ObservedExecution()
    adapter.submission = observer.submission
    adapter.decode_cell = lambda raw, dtype: _cell(raw, dtype, run_ref=adapter._run_ref)
    cursor = adapter.cursor.return_value
    original = OSError("injected driver failure")
    if failure == "execute":
        cursor.execute.side_effect = original
    elif failure == "fetch":
        cursor.fetchmany.side_effect = original
    else:
        cursor.fetchmany.return_value = [("2026-02-30",)]
    cursor.close.side_effect = OSError("secondary cleanup failure")
    statement = ScalarStatement(
        "SELECT day FROM rows WHERE id=?",
        (7,),
        pa.schema([("day", pa.date32())]),
        "query",
        ExecutionContext(),
        columns=((0,),),
    )
    with pytest.raises(MaterializationError if failure == "date" else OSError) as caught:
        stream = ScalarBatchStream(adapter, statement, 3)
        adapter._streams.add(stream)
        list(stream)
    if failure != "date":
        assert caught.value is original
    else:
        assert caught.value.run_ref == "run:stream"
    assert observer._last_submission is not None
    assert observer._last_submission.state == "failed"
    cursor.execute.assert_called_once_with(statement.sql, (7,))
    cursor.close.assert_called_once()
    assert not adapter._streams


@pytest.mark.parametrize("malformed", [False, True])
def test_exact_struct_batches_and_truncated_driver_row(malformed: bool) -> None:
    adapter = Mock(spec=ScalarExecutionAdapter)
    adapter._run_ref = "run:identity"
    adapter._streams = set()
    observer = ObservedExecution()
    adapter.submission = observer.submission
    adapter.decode_cell = lambda raw, dtype: _cell(raw, dtype, run_ref=adapter._run_ref)
    cursor = adapter.cursor.return_value
    cursor.fetchmany.side_effect = [
        [(Decimal(2**63 - 1),) if malformed else (Decimal(2**63 - 1), "9999-12-31")],
        [],
    ]
    schema = pa.schema([("identity", pa.struct([("id", pa.int64()), ("day", pa.date32())]))])
    statement = ScalarStatement(
        "SELECT id, day FROM rows", (), schema, "query", ExecutionContext(), columns=((0, 1),)
    )
    stream = ScalarBatchStream(adapter, statement, 2)
    adapter._streams.add(stream)
    if malformed:
        with pytest.raises(IndexError):
            list(stream)
    else:
        table = pa.Table.from_batches(list(stream), schema=schema)
        assert table.to_pylist() == [{"identity": {"id": 2**63 - 1, "day": date(9999, 12, 31)}}]
    cursor.execute.assert_called_once()
    cursor.close.assert_called_once()
    assert not adapter._streams
