"""Real-driver evidence for immutable submissions and native resource ownership."""

from collections.abc import Callable, Iterator
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import ibis
import ibis.expr.types as ir
import pytest
from duckdb import DuckDBPyConnection
from ibis.backends.duckdb import Backend

from marivo.analysis.materialization.duckdb_execution import DuckDBExecutionAdapter
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.execution import Parameter


@pytest.fixture
def native() -> Iterator[Backend]:
    backend = ibis.duckdb.connect()
    try:
        yield backend
    finally:
        backend.disconnect()


def test_compile_once_and_submit_exact_sql_and_parameters(
    native: Backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = DuckDBExecutionAdapter(native)
    expression = ibis.literal(17).name("value").as_table()
    statement = adapter.prepare(expression, role="primary")
    submitted: list[tuple[str, tuple[Parameter, ...] | None]] = []
    connection = native.con

    class Driver:
        def execute(self, sql: str, parameters: tuple[Parameter, ...] | None) -> DuckDBPyConnection:
            submitted.append((sql, parameters))
            result: object = connection.execute(sql, parameters)
            assert isinstance(result, DuckDBPyConnection)
            return result

    def forbidden_compile(*args: object, **kwargs: object) -> str:
        raise AssertionError("execution recompiled an expression")

    with monkeypatch.context() as patch:
        patch.setattr(native, "con", Driver())
        patch.setattr(native, "compile", forbidden_compile)
        assert adapter.prepare(expression, role="primary") == statement
        assert adapter.read_table(statement).to_pylist() == [{"value": 17}]
        scalar = adapter.statement(
            "SELECT ?::DECIMAL(12,2)", role="proof", parameters=(Decimal("19.25"),)
        )
        assert adapter.read_scalar(scalar) == Decimal("19.25")
    assert submitted == [(statement.sql, None), (scalar.sql, scalar.parameters)]


@pytest.mark.parametrize("empty", [False, True])
def test_arrow_schema_conversion_and_no_ambient_default_limit(native: Backend, empty: bool) -> None:
    native.raw_sql(
        "CREATE TABLE input AS SELECT i, 12.25::DECIMAL(12,2) AS amount, TIMESTAMPTZ '2026-01-01 12:00:00+00' AS at, {'id': i} AS identity FROM range(5) t(i)"
    )
    adapter = DuckDBExecutionAdapter(native)
    expression = native.table("input")
    if empty:
        expression = expression.filter(expression.i < 0)
    old = ibis.options.sql.default_limit
    try:
        ibis.options.sql.default_limit = 1
        statement = adapter.prepare(expression, role="primary")
        table = adapter.read_table(statement)
        assert table.num_rows == (0 if empty else 5)
        assert table.schema == statement.schema
        stream = adapter.batches(statement, chunk_size=2)
        try:
            batches = list(stream)
            assert sum(batch.num_rows for batch in batches) == table.num_rows
            assert stream.schema == table.schema
        finally:
            stream.close()
    finally:
        ibis.options.sql.default_limit = old


def test_memtable_preparation_is_pure_reserved_and_not_repeated(
    native: Backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    register = native._register_in_memory_table

    def registered(node: object) -> None:
        events.append("register")
        register(node)

    monkeypatch.setattr(native, "_register_in_memory_table", registered)
    adapter = DuckDBExecutionAdapter(native, reserve=lambda _: events.append("reserve"))
    statement = adapter.prepare(ibis.memtable({"value": [2, 3]}), role="primary")
    assert events == []
    assert adapter.read_table(statement).num_rows == 2
    assert events == ["reserve", "register"]
    assert adapter.read_table(statement).num_rows == 2
    assert events == ["reserve", "register"]


def test_unreserved_hook_is_rejected_before_registration(native: Backend) -> None:
    adapter = DuckDBExecutionAdapter(native)
    statement = adapter.prepare(ibis.memtable({"value": [1]}))
    with pytest.raises(MaterializationError):
        adapter.read_table(statement)
    assert native.list_tables() == []


def test_context_cannot_be_replaced_or_used_after_close(native: Backend) -> None:
    adapter = DuckDBExecutionAdapter(native)
    other = DuckDBExecutionAdapter(native)
    statement = adapter.prepare(ibis.literal(1))
    with pytest.raises(MaterializationError):
        other.read_scalar(statement)
    with pytest.raises(MaterializationError):
        adapter.read_scalar(replace(statement, context=replace(statement.context)))
    with pytest.raises(MaterializationError):
        other.statement("SELECT 1", inputs=(statement,))
    adapter.disconnect()
    with pytest.raises(MaterializationError):
        adapter.read_scalar(statement)


def test_initialization_and_finish_do_not_submit_consistency_transactions(
    native: Backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.materialization.execution import Statement

    adapter = DuckDBExecutionAdapter(native)
    submitted: list[str] = []
    submit = adapter.submit

    def record(statement: Statement) -> DuckDBPyConnection:
        submitted.append(statement.sql)
        return submit(statement)

    monkeypatch.setattr(adapter, "submit", record)
    adapter.initialize()
    assert adapter.read_scalar(adapter.statement("SELECT current_setting('TimeZone')")) == "UTC"
    adapter.finish()
    adapter.finish()
    assert submitted == [
        "SET threads=1",
        "SET TimeZone='UTC'",
        "SELECT current_setting('TimeZone')",
    ]


def test_queries_observe_committed_update_without_shared_snapshot(tmp_path: Path) -> None:
    database = tmp_path / "changing.duckdb"
    writer = ibis.duckdb.connect(database)
    reader = ibis.duckdb.connect(database)
    adapter = DuckDBExecutionAdapter(reader)
    try:
        writer.raw_sql("CREATE TABLE input AS SELECT 1 AS value")
        adapter.initialize()
        statement = adapter.prepare(reader.table("input"))
        assert adapter.read_table(statement).to_pylist() == [{"value": 1}]
        writer.raw_sql("UPDATE input SET value = 2")
        assert adapter.read_table(statement).to_pylist() == [{"value": 2}]
    finally:
        adapter.finish()
        writer.disconnect()


def test_scalar_rejects_missing_and_duplicate_rows(native: Backend) -> None:
    adapter = DuckDBExecutionAdapter(native)
    for sql in ("SELECT 1 WHERE FALSE", "SELECT * FROM range(2)", "SELECT 1, 2"):
        with pytest.raises(MaterializationError):
            adapter.read_scalar(adapter.statement(sql, role="proof"))


def test_composed_fence_preserves_reserved_preparations(
    native: Backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    reservations: list[str] = []
    adapter = DuckDBExecutionAdapter(native, reserve=reservations.append)
    expression = ibis.memtable({"value": [2, 3]})
    statement = adapter.table_statement("frozen", expression)
    assert reservations == []
    with monkeypatch.context() as patch:
        patch.setattr(native, "compile", lambda *_args, **_kwargs: pytest.fail("fence recompiled"))
        adapter.submit(statement)
    assert len(reservations) == 1
    assert adapter.read_scalar(adapter.statement("SELECT sum(value) FROM frozen")) == 5


def test_builtin_numeric_functions_do_not_reserve_per_expression(native: Backend) -> None:
    from marivo.analysis.compiler.driver_numeric import exact_float_sum

    native.raw_sql("CREATE TABLE input AS SELECT 1.25 AS a, 2.5 AS b")
    reservations: list[str] = []
    adapter = DuckDBExecutionAdapter(native, reserve=reservations.append)
    adapter.install_numeric()
    table = native.table("input")
    statement = adapter.prepare(
        table.aggregate(
            a=exact_float_sum(table.a.cast("float64")), b=exact_float_sum(table.b.cast("float64"))
        )
    )
    assert adapter.read_table(statement).to_pylist() == [{"a": 1.25, "b": 2.5}]
    assert reservations == ["__marivo_driver_float_units", "__marivo_driver_float_from_units"]


def test_python_udf_registers_once_for_distinct_argument_nodes(native: Backend) -> None:
    def increment_value(value: int) -> int:
        return value + 1

    increment: Callable[[ir.IntegerValue], ir.IntegerValue] = ibis.udf.scalar.python(
        increment_value
    )

    native.raw_sql("CREATE TABLE input AS SELECT 1::BIGINT AS a, 2::BIGINT AS b")
    table = native.table("input")
    reservations: list[str] = []
    adapter = DuckDBExecutionAdapter(native, reserve=reservations.append)
    statement = adapter.prepare(table.select(a=increment(table.a), b=increment(table.b)))
    assert adapter.read_table(statement).to_pylist() == [{"a": 2, "b": 3}]
    assert len(reservations) == 1


def test_failed_disconnect_does_not_prove_resource_termination(
    native: Backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.materialization.resources import (
        backend_reservation,
        confirm_execution_termination,
        finish_execution,
        forget_local_termination,
    )

    resource = backend_reservation("adapter-test", "source")
    adapter = DuckDBExecutionAdapter(native)
    adapter.initialize()

    def failed_close() -> None:
        raise OSError("no termination receipt")

    with monkeypatch.context() as patch:
        patch.setattr(native, "disconnect", failed_close)
        with pytest.raises(OSError):
            finish_execution(adapter, resource)
    assert not confirm_execution_termination(resource)
    finish_execution(adapter, resource)
    assert confirm_execution_termination(resource)
    forget_local_termination((resource,))


@pytest.mark.parametrize("start", [False, True])
def test_stream_closes_driver_reader_when_unused_or_abandoned(
    native: Backend, monkeypatch: pytest.MonkeyPatch, start: bool
) -> None:
    from marivo.analysis.materialization.duckdb_execution import DuckDBBatchStream

    adapter = DuckDBExecutionAdapter(native)
    stream = adapter.batches(
        adapter.prepare(ibis.range(0, 20).unnest().name("value").as_table()), chunk_size=2
    )
    assert isinstance(stream, DuckDBBatchStream)
    closed: list[str] = []
    underlying = stream._native

    class NativeReader:
        def close(self) -> None:
            closed.append("driver")
            underlying.close()

    monkeypatch.setattr(stream, "_native", NativeReader())
    if start:
        assert next(iter(stream)).num_rows == 2
    stream.close()
    assert closed == ["driver"]


def test_nested_udfs_register_each_dependency_after_its_reservation(
    native: Backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ibis.expr.operations as ops

    def inner_value(value: int) -> int:
        return value + 1

    def outer_value(value: int) -> int:
        return value * 2

    inner: Callable[[ir.IntegerValue], ir.IntegerValue] = ibis.udf.scalar.python(inner_value)
    outer: Callable[[ir.IntegerValue], ir.IntegerValue] = ibis.udf.scalar.python(outer_value)
    events: list[tuple[str, str]] = []
    original = native._register_python_udf

    def registration(node: ops.ScalarUDF) -> Callable[[DuckDBPyConnection], None]:
        function: Callable[[DuckDBPyConnection], None] = original(node)
        name = type(node).__name__

        def register(connection: DuckDBPyConnection) -> None:
            assert ("reserve", name) in events
            assert ("register", name) not in events
            events.append(("register", name))
            function(connection)

        return register

    monkeypatch.setattr(native, "_register_python_udf", registration)
    adapter = DuckDBExecutionAdapter(native, reserve=lambda name: events.append(("reserve", name)))
    statement = adapter.prepare(outer(inner(ibis.literal(2))))
    assert adapter.read_scalar(statement) == 6
    assert len(events) == 4


@pytest.mark.runtime
def test_runtime_diagnostics_match_submitted_fences_assertions_and_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from collections import Counter

    from marivo.analysis.materialization.admission import DatasetRuntime
    from marivo.analysis.materialization.execution import Statement
    from marivo.analysis.observation.sampling import engine_sample
    from marivo.refs import ref
    from tests.lazy_execution_fixtures import make_execution_registry, seed_execution_database

    database = tmp_path / "warehouse.duckdb"
    seed_execution_database(database)
    registry, sidecar = make_execution_registry(database)
    runtime = DatasetRuntime.create(tmp_path, "exact-submissions")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    population = sources.population(ref.entity("sales.customers")).sample(
        engine_sample(target_rows=3, seed=42)
    )
    logical = sources.observe(ref.metric("sales.revenue"), population=population).aggregate()
    original = DuckDBExecutionAdapter.submit
    submitted: list[tuple[str, str]] = []

    def submit(adapter: DuckDBExecutionAdapter, statement: Statement) -> DuckDBPyConnection:
        with monkeypatch.context() as patch:
            patch.setattr(
                adapter._backend,
                "compile",
                lambda *_args, **_kwargs: pytest.fail("submission recompiled"),
            )
            result = original(adapter, statement)
        submitted.append((statement.role, statement.sql))
        return result

    monkeypatch.setattr(DuckDBExecutionAdapter, "submit", submit)
    result = logical.execute()
    assert result.to_pandas().shape[0] == 1
    actual = Counter(submitted)
    for statement, count in Counter(runtime.statistics.statements).items():
        assert actual[statement] >= count
    assert {role for role, _ in submitted} >= {
        "primary",
        "sampling_fence",
        "sampling_validation",
        "validation_batch",
        "source_schema",
        "transfer_guard",
    }


@pytest.mark.runtime
def test_runtime_publishes_after_between_query_update_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.materialization import admission
    from marivo.analysis.materialization.execution import Statement
    from marivo.datasource.backends import (
        BuiltDatasourceBackend,
        EffectiveDatasourceKwargs,
        _build_backend_from_effective,
    )
    from marivo.datasource.ir import DatasourceIR
    from marivo.refs import ref
    from tests.lazy_execution_fixtures import make_execution_registry, seed_execution_database

    database = tmp_path / "warehouse.duckdb"
    seed_execution_database(database)
    registry, sidecar = make_execution_registry(database)
    runtime = admission.DatasetRuntime.create(tmp_path, "changing-source")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    logical = sources.observe(ref.metric("sales.revenue")).aggregate()
    build = _build_backend_from_effective
    original_submit = DuckDBExecutionAdapter.submit
    roles: list[str] = []
    opened: list[bool] = []
    updates: list[bool] = []
    writer = ibis.duckdb.connect(database)

    def open_source(
        datasource: DatasourceIR, effective: EffectiveDatasourceKwargs, *, read_only: bool = False
    ) -> BuiltDatasourceBackend:
        assert read_only
        opened.append(True)
        # DuckDB requires matching access modes for simultaneous file connections.
        # Only this fixture enables the independent writer; Runtime requests read-only.
        return build(datasource, effective, read_only=False)

    def submit(adapter: DuckDBExecutionAdapter, statement: Statement) -> DuckDBPyConnection:
        assert statement.sql not in ("BEGIN TRANSACTION", "ROLLBACK")
        if statement.role == "primary" and not updates:
            assert "validation_batch" in roles
            writer.raw_sql("UPDATE orders SET amount = amount + 10 WHERE id = 1")
            updates.append(True)
        roles.append(statement.role)
        return original_submit(adapter, statement)

    try:
        monkeypatch.setattr(admission, "_build_backend_from_effective", open_source)
        monkeypatch.setattr(DuckDBExecutionAdapter, "submit", submit)
        result = logical.execute()
        rows = result.to_pandas()
        assert rows["revenue"].tolist() == [157.0]
        assert updates == opened == [True]
        assert roles.count("primary") == runtime.statistics.primary_queries == 1
        run = runtime.store.run(result.state.producing_run_ref)
        assert run is not None and run.lifecycle == "succeeded"
        assert runtime.store.resources(runtime.session_ref) == ()
        assert logical.execute().state.artifact_ref == result.state.artifact_ref
        assert roles.count("primary") == 1
    finally:
        writer.disconnect()


@pytest.mark.runtime
@pytest.mark.parametrize("empty_output", [False, True])
def test_invalid_source_is_rejected_even_for_empty_output(
    tmp_path: Path, empty_output: bool
) -> None:
    from marivo.analysis.materialization.admission import DatasetRuntime
    from marivo.analysis.observation.predicates import gt
    from marivo.refs import ref
    from tests.lazy_execution_fixtures import make_execution_registry, seed_execution_database

    database = tmp_path / "warehouse.duckdb"
    seed_execution_database(database)
    writer = ibis.duckdb.connect(database)
    try:
        writer.raw_sql("INSERT INTO orders SELECT * FROM orders WHERE id = 1")
    finally:
        writer.disconnect()
    registry, sidecar = make_execution_registry(database)
    runtime = DatasetRuntime.create(tmp_path, "invalid-source")
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    logical = sources.observe(ref.metric("sales.revenue"))
    if empty_output:
        logical = logical.where(gt(ref.metric("sales.revenue"), 1000))
    with pytest.raises(MaterializationError):
        logical.execute()
    assert runtime.statistics.validation_queries > 0
    assert runtime.statistics.primary_queries == 0
    assert runtime.last_run_ref is not None
    run = runtime.store.run(runtime.last_run_ref)
    assert run is not None and run.lifecycle == "failed"
    assert runtime.store.resources(runtime.session_ref) == ()
    assert not list(runtime.store.layout.session_dir(runtime.session_ref).rglob("*.parquet"))
