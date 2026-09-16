"""Concrete PostgreSQL boundary failures remain actionable before source I/O."""

from dataclasses import replace
from typing import get_args

import ibis
import pyarrow as pa
import pytest
from ibis.backends.postgres import Backend

from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.execution import resolve_execution
from marivo.analysis.materialization.postgres_execution import (
    PostgresExecutionAdapter,
    _transport_row,
    bind_postgres,
)
from marivo.analysis.operators.registry import BackendName, backend_execution
from marivo.datasource.engines.postgres import PROFILE


def test_backend_declarations_have_matching_runtime_factories() -> None:
    for name in get_args(BackendName):
        declaration = backend_execution(name)
        runtime = resolve_execution(name)
        if declaration is None:
            assert runtime is None
        else:
            assert runtime is not None
            assert declaration.retained_import == (runtime.open_retained is not None)


def test_foreign_closed_and_parameter_failures_teach_different_repairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native = Backend()
    adapter = PostgresExecutionAdapter(native, run_ref="run_errors")
    statement = adapter.statement("SELECT 1")
    other = PostgresExecutionAdapter(Backend(), run_ref="run_other")
    with pytest.raises(MaterializationError) as foreign:
        other.submit(statement)
    assert foreign.value.run_ref == "run_other"
    assert foreign.value.received == "the statement belongs to a different execution context"
    assert foreign.value.expected == "a statement prepared by this execution context"
    assert (
        foreign.value.repair is not None and "Prepare the statement" in foreign.value.repair.action
    )
    for scalar in (False, True):
        with pytest.raises(MaterializationError) as parameters:
            if scalar:
                adapter.read_scalar(statement, params={ibis.param("int64"): 1})
            else:
                adapter.batches(statement, params={ibis.param("int64"): 1}, chunk_size=1)
        assert parameters.value.run_ref == "run_errors"
        assert parameters.value.received == "params was supplied alongside a prepared Statement"
        assert parameters.value.repair is not None
        assert "Statement.parameters" in parameters.value.repair.action
    monkeypatch.setattr(native, "disconnect", lambda: None)
    adapter.finish()
    with pytest.raises(MaterializationError) as closed:
        adapter.submit(statement)
    assert closed.value.received == "the PostgreSQL execution context is closed"
    assert closed.value.repair is not None and "new action-owned" in closed.value.repair.action


def test_invalid_binding_identifies_type_and_preserves_run() -> None:
    def no_preparation(name: str) -> None:
        raise AssertionError("PostgreSQL requested preparation")

    with pytest.raises(MaterializationError) as invalid:
        bind_postgres(object(), reserve=no_preparation, run_ref="run_binding")
    assert invalid.value.run_ref == "run_binding"
    assert invalid.value.received == "the binding supplied a different backend type: object"
    assert invalid.value.repair is not None
    assert "PostgreSQL connection owner" in invalid.value.repair.action


def test_unsupported_operations_and_missing_timezone_identify_the_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PostgresExecutionAdapter(Backend())
    with pytest.raises(MaterializationError) as parquet:
        adapter.read_parquet("private-path-canary", table_name="input")
    assert parquet.value.received == "an unsupported physical operation was requested: read_parquet"
    assert "private-path-canary" not in str(parquet.value)
    monkeypatch.setattr(
        "marivo.datasource.engines.require_profile_for_backend_type",
        lambda name: replace(PROFILE, timezone_probe_sql=None),
    )
    with pytest.raises(MaterializationError) as timezone:
        adapter.timezone()
    assert timezone.value.received == "the registered PostgreSQL profile has no timezone query"
    assert timezone.value.repair is not None and "Restore" in timezone.value.repair.action


def test_malformed_identity_records_report_shape_without_row_contents() -> None:
    schema = pa.schema([("identity", pa.struct([("id", pa.int64())]))])
    with pytest.raises(MaterializationError) as malformed:
        _transport_row(schema, ("private-value-canary",), run_ref="run_decode")
    assert malformed.value.run_ref == "run_decode"
    assert malformed.value.stage == "output_validation"
    assert (
        malformed.value.received is not None
        and "field=identity; type=str" in malformed.value.received
    )
    assert "private-value-canary" not in str(malformed.value)
