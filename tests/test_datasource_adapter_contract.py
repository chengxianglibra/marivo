"""Expression identity, native submission and owned stream tests."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import ibis
import pyarrow as pa
import pytest

from marivo.datasource.adapters import (
    CompiledRead,
    PhysicalRequirement,
    SourceBatchStream,
    SourceSession,
    SourceSubmission,
    _exact_array,
    provider_for,
    provider_names,
)
from marivo.datasource.errors import (
    DatasourceBackendTypeUnsupportedError,
    DatasourceSourceCapabilityError,
)
from marivo.datasource.ir import AiContextIR, DatasourceIR, DatasourceSourceLocation, TableSourceIR


def _datasource(backend: str) -> DatasourceIR:
    return DatasourceIR(
        semantic_id="source",
        name="source",
        backend_type=backend,
        fields={},
        env_refs={},
        ai_context=AiContextIR(),
        python_symbol="source",
        location=DatasourceSourceLocation("source.py", 1),
    )


@pytest.fixture(params=["duckdb", "sqlite"])
def session(request: pytest.FixtureRequest, tmp_path: Path) -> SourceSession:
    backend_name = request.param
    database = tmp_path / f"source.{backend_name}"
    backend = (
        ibis.duckdb.connect(database) if backend_name == "duckdb" else ibis.sqlite.connect(database)
    )
    backend.raw_sql("CREATE TABLE facts (id BIGINT, amount BIGINT)")
    backend.raw_sql("INSERT INTO facts VALUES (9007199254740993, 2), (9007199254740994, 3)")
    result = SourceSession(provider_for(backend_name), _datasource(backend_name), backend)
    try:
        yield result
    finally:
        result.close()


def _read(session: SourceSession) -> CompiledRead:
    bound = session.bind(TableSourceIR("facts"), source_identity="facts@v1")
    qualified = session.qualify(
        bound, PhysicalRequirement("basic.rows", 1, frozenset({"scan", "filter", "project"}))
    )
    table = bound.relation
    expression = table.filter(table.amount >= 2).select(table.id, table.amount)
    return session.compile(
        qualified,
        expression,
        purpose="basic.rows",
        expected_schema=expression.schema().to_pyarrow(),
    )


def test_all_six_providers_resolve_without_opening() -> None:
    assert provider_names() == ("duckdb", "sqlite", "trino", "mysql", "postgres", "clickhouse")
    assert tuple(provider_for(name).name for name in provider_names()) == provider_names()
    with pytest.raises(DatasourceBackendTypeUnsupportedError):
        provider_for("unknown")


def test_nested_decode_rejects_bool_coercion() -> None:
    schema_field = pa.field("identity", pa.struct([pa.field("active", pa.bool_())]))
    with pytest.raises(DatasourceSourceCapabilityError):
        _exact_array([{"active": 1}], schema_field)


def test_common_analysis_adapter_exposes_no_text_statement() -> None:
    from marivo.analysis.materialization.execution import ExecutionAdapter

    assert "statement" not in vars(ExecutionAdapter)


def test_selected_provider_does_not_import_unselected_modules() -> None:
    code = """
import builtins
import sys
original_import = builtins.__import__
drivers = ('psycopg', 'pymysql', 'trino', 'clickhouse_connect')
def reject_optional_drivers(name, *args, **kwargs):
    assert not any(name == driver or name.startswith(driver + '.') for driver in drivers), name
    return original_import(name, *args, **kwargs)
builtins.__import__ = reject_optional_drivers
from marivo.datasource.adapters import provider_for
assert provider_for('sqlite').name == 'sqlite'
for name in ('duckdb', 'postgres', 'mysql', 'trino', 'clickhouse'):
    assert 'marivo.datasource.engines.' + name not in sys.modules
for name in ('postgres', 'mysql', 'trino', 'clickhouse'):
    assert provider_for(name).name == name
"""
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)


def test_compiled_read_submits_exact_ibis_sql_and_preserves_int64(session: SourceSession) -> None:
    read = _read(session)
    stream = session.batches(read, chunk_size=1)
    assert stream.schema.equals(read.schema, check_metadata=False)
    rows = pa.Table.from_batches(stream, schema=stream.schema).to_pylist()
    assert rows == [
        {"id": 9007199254740993, "amount": 2},
        {"id": 9007199254740994, "amount": 3},
    ]
    assert [
        (item.purpose, item.source_identity, item.expression_identity, item.sql, item.state)
        for item in session.submissions
    ] == [("basic.rows", "facts@v1", id(read.expression.op()), read.sql, "succeeded")]
    assert not session._streams


def test_basic_group_and_count_use_the_bound_expression(session: SourceSession) -> None:
    bound = session.bind(TableSourceIR("facts"), source_identity="facts@v1")
    qualified = session.qualify(
        bound,
        PhysicalRequirement("basic.group", 1, frozenset({"scan", "group", "count"})),
    )
    table = bound.relation
    expression = table.group_by(table.amount).aggregate(rows=table.id.count()).order_by("amount")
    read = session.compile(
        qualified,
        expression,
        purpose="basic.group",
        expected_schema=expression.schema().to_pyarrow(),
    )
    stream = session.batches(read, chunk_size=2)
    assert pa.Table.from_batches(stream, schema=stream.schema).to_pylist() == [
        {"amount": 2, "rows": 1},
        {"amount": 3, "rows": 1},
    ]


def test_compiled_read_rejects_forgery_foreign_session_and_unbound_source(
    session: SourceSession, tmp_path: Path
) -> None:
    read = _read(session)
    with pytest.raises(DatasourceSourceCapabilityError):
        session.batches(replace(read, sql="SELECT 0"), chunk_size=1)
    object.__setattr__(read, "sql", "SELECT 0")
    with pytest.raises(DatasourceSourceCapabilityError):
        session.batches(read, chunk_size=1)
    object.__setattr__(read, "sql", session._issued[id(read)][1].sql)
    object.__setattr__(read, "params", ((ibis.literal(1), 2),))
    with pytest.raises(DatasourceSourceCapabilityError):
        session.batches(read, chunk_size=1)
    other_backend = ibis.duckdb.connect(tmp_path / "other.duckdb")
    other = SourceSession(provider_for("duckdb"), _datasource("duckdb"), other_backend)
    try:
        with pytest.raises(DatasourceSourceCapabilityError):
            other.batches(read, chunk_size=1)
        bound = session.bind(TableSourceIR("facts"), source_identity="facts@v2")
        qualified = session.qualify(
            bound, PhysicalRequirement("basic.count", 1, frozenset({"count"}))
        )
        unrelated = ibis.table({"id": "int64"}, name="other")
        with pytest.raises(DatasourceSourceCapabilityError):
            session.compile(
                qualified,
                unrelated.count(),
                purpose="basic.count",
                expected_schema=unrelated.count().as_table().schema().to_pyarrow(),
            )
        mixed = bound.relation.join(unrelated, bound.relation.id == unrelated.id)
        with pytest.raises(DatasourceSourceCapabilityError, match="additional physical input"):
            session.compile(
                qualified,
                mixed,
                purpose="basic.join",
                expected_schema=mixed.schema().to_pyarrow(),
            )
    finally:
        other.close()


def test_early_close_and_empty_result_keep_fixed_schema(session: SourceSession) -> None:
    read = _read(session)
    stream = session.batches(read, chunk_size=1)
    assert next(iter(stream)).num_rows == 1
    stream.close()
    assert not session._streams
    assert session.submissions[-1].state == "closed_early"
    with pytest.raises(DatasourceSourceCapabilityError):
        list(stream)

    bound = session.bind(TableSourceIR("facts"), source_identity="facts@v1")
    qualified = session.qualify(
        bound, PhysicalRequirement("basic.empty", 1, frozenset({"scan", "filter"}))
    )
    expression = bound.relation.filter(bound.relation.id < 0)
    empty = session.compile(
        qualified,
        expression,
        purpose="basic.empty",
        expected_schema=expression.schema().to_pyarrow(),
    )
    empty_stream = session.batches(empty, chunk_size=1)
    assert list(empty_stream) == []
    assert empty_stream.schema.equals(empty.schema, check_metadata=False)


def test_interrupt_reports_only_local_close(session: SourceSession) -> None:
    stream = session.batches(_read(session), chunk_size=1)
    assert session.interrupt() == "local_closed"
    assert not session._streams
    with pytest.raises(DatasourceSourceCapabilityError):
        list(stream)


def test_fetch_failure_closes_cursor_and_records_failure(session: SourceSession) -> None:
    class FailingCursor:
        closed = False

        def fetchmany(self, _size: int) -> list[tuple[object, ...]]:
            raise RuntimeError("driver read failed")

        def close(self) -> None:
            self.closed = True

    cursor = FailingCursor()
    submission = SourceSubmission("basic.rows", "facts@v1", 1, "compiled SQL")
    stream = SourceBatchStream(session, cursor, pa.schema([("id", pa.int64())]), 1, submission)
    session._streams.add(stream)
    with pytest.raises(RuntimeError, match="driver read failed"):
        list(stream)
    assert cursor.closed
    assert submission.state == "failed"
    assert not session._streams


@pytest.mark.parametrize(
    ("value", "arrow_type"),
    [
        (1.5, pa.int64()),
        (2**63, pa.int64()),
        (1.1, pa.float32()),
        (1.25, pa.decimal128(10, 2)),
    ],
)
def test_lossy_driver_values_fail_and_close(
    session: SourceSession, value: object, arrow_type: pa.DataType
) -> None:
    class ValueCursor:
        closed = False
        reads = 0

        def fetchmany(self, _size: int) -> list[tuple[object, ...]]:
            self.reads += 1
            return [(value,)] if self.reads == 1 else []

        def close(self) -> None:
            self.closed = True

    cursor = ValueCursor()
    submission = SourceSubmission("basic.rows", "facts@v1", 1, "compiled SQL")
    stream = SourceBatchStream(session, cursor, pa.schema([("value", arrow_type)]), 1, submission)
    session._streams.add(stream)
    with pytest.raises(DatasourceSourceCapabilityError):
        list(stream)
    assert cursor.closed
    assert submission.state == "failed"
    assert not session._streams


def test_decimal_decode_preserves_exact_value(session: SourceSession) -> None:
    class DecimalCursor:
        reads = 0

        def fetchmany(self, _size: int) -> list[tuple[object, ...]]:
            self.reads += 1
            return [(Decimal("12.34"),)] if self.reads == 1 else []

        def close(self) -> None:
            return None

    stream = SourceBatchStream(
        session,
        DecimalCursor(),
        pa.schema([("value", pa.decimal128(10, 2))]),
        1,
        SourceSubmission("basic.rows", "facts@v1", 1, "compiled SQL"),
    )
    session._streams.add(stream)
    assert pa.Table.from_batches(stream, schema=stream.schema).to_pylist() == [
        {"value": Decimal("12.34")}
    ]


def test_unverified_remote_provider_has_no_basic_route() -> None:
    # A relation stub cannot grant a remote physical qualification.
    backend = Mock()
    backend.name = "postgres"
    backend.table.return_value = ibis.table({"id": "int64"}, name="facts")
    session = SourceSession(provider_for("postgres"), _datasource("postgres"), backend)
    try:
        bound = session.bind(TableSourceIR("facts"), source_identity="facts@v1")
        with pytest.raises(DatasourceSourceCapabilityError, match=r"pending R1\.2"):
            session.qualify(bound, PhysicalRequirement("basic.rows", 1, frozenset({"scan"})))
        assert session.interrupt() == "remote_unknown"
    finally:
        session.close()


def test_session_rejects_backend_from_another_provider(tmp_path: Path) -> None:
    backend = ibis.duckdb.connect(tmp_path / "other.duckdb")
    try:
        with pytest.raises(DatasourceSourceCapabilityError):
            SourceSession(provider_for("postgres"), _datasource("postgres"), backend)
    finally:
        backend.disconnect()
