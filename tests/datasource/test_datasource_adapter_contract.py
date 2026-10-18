"""Expression binding and Ibis pandas execution contracts."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import NoReturn
from unittest.mock import Mock

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from marivo.datasource.adapters import (
    TIMESTAMP_UNIT_METADATA_KEY,
    CompiledRead,
    PhysicalRequirement,
    SourceIR,
    SourceSession,
    provider_for,
    provider_names,
)
from marivo.datasource.errors import (
    DatasourceBackendTypeUnsupportedError,
    DatasourceSourceCapabilityError,
)
from marivo.datasource.ir import (
    AiContextIR,
    CsvSourceIR,
    DatasourceIR,
    DatasourceSourceLocation,
    JsonSourceIR,
    ParquetSourceIR,
    SourceParamIR,
    TableSourceIR,
)
from tests.support.execution_logs import execution_records


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


@pytest.fixture
def session(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[SourceSession]:
    backend_name: object = getattr(request, "param", "duckdb")
    assert isinstance(backend_name, str) and backend_name == "duckdb"
    backend = ibis.duckdb.connect(tmp_path / f"source.{backend_name}")
    backend.raw_sql("CREATE TABLE facts (id BIGINT, amount BIGINT)")
    backend.raw_sql("INSERT INTO facts VALUES (10, 2), (11, 3)")
    backend.raw_sql("CREATE VIEW facts_view AS SELECT id, amount FROM facts")
    result = SourceSession(
        provider_for(backend_name), _datasource(backend_name), backend, project_root=tmp_path
    )
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


@pytest.mark.parametrize("backend", ["sqlite", "sqlite3"])
def test_removed_backend_refuses_before_provider_loading(
    backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.datasource import engines

    def forbidden_load(_backend: str) -> NoReturn:
        raise AssertionError("Removed datasource reached provider loading")

    monkeypatch.setattr(engines, "_load_profile", forbidden_load)
    with pytest.raises(DatasourceBackendTypeUnsupportedError) as caught:
        provider_for(backend)
    error = caught.value
    assert error.expected and error.received == backend and error.repair is not None
    assert error.repair.candidates == ("clickhouse", "duckdb", "mysql", "postgres", "trino")


def test_all_providers_resolve_without_opening() -> None:
    assert provider_names() == ("duckdb", "trino", "mysql", "postgres", "clickhouse")
    assert tuple(provider_for(name).name for name in provider_names()) == provider_names()
    with pytest.raises(DatasourceBackendTypeUnsupportedError):
        provider_for("unknown")


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
assert provider_for('duckdb').name == 'duckdb'
for name in ('postgres', 'mysql', 'trino', 'clickhouse'):
    assert 'marivo.datasource.engines.' + name not in sys.modules
for name in ('postgres', 'mysql', 'trino', 'clickhouse'):
    assert provider_for(name).name == name
"""
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)


@pytest.mark.parametrize("unit", ("ms", "us", "ns"))
def test_parquet_timestamp_facts_retain_file_unit(tmp_path: Path, unit: str) -> None:
    path = tmp_path / "instants.parquet"
    pq.write_table(pa.table({"point": pa.array([1, 2, 7], type=pa.timestamp(unit))}), path)
    with SourceSession(
        provider_for("duckdb"), _datasource("duckdb"), ibis.duckdb.connect(tmp_path / "facts.db")
    ) as source:
        bound = source.bind(ParquetSourceIR(path=str(path)), source_identity="instants")
        field = bound.facts.schema.field("point")
        assert field.type == bound.relation.schema().to_pyarrow().field("point").type
        assert field.metadata == {TIMESTAMP_UNIT_METADATA_KEY: unit.encode("ascii")}
        assert pq.read_schema(path).field("point").type == pa.timestamp(unit)


def test_compiled_read_executes_through_ibis_and_returns_pandas(
    session: SourceSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    bound = session.bind(TableSourceIR("facts"), source_identity="facts@v1")
    qualified = session.qualify(
        bound, PhysicalRequirement("basic.parameter", 1, frozenset({"scan", "filter", "project"}))
    )
    parameter = ibis.param("int64")
    expression = bound.relation.filter(bound.relation.amount >= parameter).select(
        bound.relation.id, bound.relation.amount
    )
    read = session.compile(
        qualified,
        expression,
        params={parameter: 2},
        purpose="basic.parameter",
        expected_schema=expression.schema().to_pyarrow(),
    )
    backend_execute = Mock(wraps=session._backend.to_pyarrow)
    monkeypatch.setattr(session._backend, "to_pyarrow", backend_execute)
    backend_compile = Mock(wraps=session._backend.compile)
    monkeypatch.setattr(session._backend, "compile", backend_compile)

    result = session.execute(read)

    assert isinstance(result, pd.DataFrame)
    assert result.to_dict(orient="records") == [
        {"id": 10, "amount": 2},
        {"id": 11, "amount": 3},
    ]
    assert backend_compile.call_count == 1
    assert backend_execute.call_count == 1
    (table,) = backend_execute.call_args.args
    assert table.op() == read.expression.as_table().op()
    assert backend_execute.call_args.kwargs == {"params": {parameter: 2}, "limit": None}
    assert session.submissions[-1].sql == read.sql
    assert session.submissions[-1].source_identity == "facts@v1"
    assert session.submissions[-1].expression_identity == id(read.expression.op())
    assert session.submissions[-1].state == "succeeded"

    records = [
        item
        for item in execution_records(session._log_root)
        if item.get("purpose") == "basic.parameter"
    ]
    assert [item["event"] for item in records] == ["query.submitted", "query.completed"]
    assert records[0]["sql"] == read.sql
    assert records[0]["query_id"] == records[1]["query_id"]
    assert records[0]["source_identity"] == records[1]["source_identity"] == "facts@v1"
    assert records[1]["state"] == "succeeded"
    assert records[1]["consumed_rows"] == 2


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
    assert session.execute(read).to_dict(orient="records") == [
        {"amount": 2, "rows": 1},
        {"amount": 3, "rows": 1},
    ]


def test_local_view_is_a_separate_bound_source(session: SourceSession) -> None:
    bound = session.bind(TableSourceIR("facts_view"), source_identity="view@v1")
    qualified = session.qualify(
        bound, PhysicalRequirement("basic.view", 1, frozenset({"scan", "project"}))
    )
    expression = bound.relation.select("id").order_by("id")
    read = session.compile(
        qualified,
        expression,
        purpose="basic.view",
        expected_schema=expression.schema().to_pyarrow(),
    )
    assert session.execute(read)["id"].tolist() == [10, 11]


def test_projection_identity_and_multiple_bound_tables(session: SourceSession) -> None:
    source = TableSourceIR("facts", columns=(("value", "amount"),))
    first = session.bind(source, source_identity="first")
    assert session.bind(source, source_identity="first") is first
    assert first.relation.columns == ("value",)
    with pytest.raises(DatasourceSourceCapabilityError, match="identity reused"):
        session.bind(TableSourceIR("facts"), source_identity="first")
    second = session.bind(TableSourceIR("facts"), source_identity="second")
    requirements = (
        session.qualify(first, PhysicalRequirement("authoring.preview", 1, frozenset({"scan"}))),
        session.qualify(second, PhysicalRequirement("authoring.preview", 1, frozenset({"scan"}))),
    )
    expression = first.relation.cross_join(second.relation).select(first.relation.value)
    read = session.compile(
        requirements,
        expression,
        purpose="authoring.preview",
        expected_schema=expression.schema().to_pyarrow(),
    )
    assert read.source_identity == "first|second"
    assert len(session.execute(read)) == 4


def test_duckdb_file_bindings_preserve_projection_and_json_params(tmp_path: Path) -> None:
    csv_path = tmp_path / "facts.csv"
    csv_path.write_text("id,amount\n1,2\n2,3\n")
    parquet_path = tmp_path / "facts.parquet"
    pq.write_table(pa.table({"id": [1, 2], "amount": [2, 3]}), parquet_path)
    json_path = tmp_path / "facts.json"
    json_path.write_text('[{"id":1,"amount":2},{"id":2,"amount":3}]')
    backend = ibis.duckdb.connect(tmp_path / "files.duckdb")
    with SourceSession(provider_for("duckdb"), _datasource("duckdb"), backend) as file_session:
        cases: tuple[tuple[SourceIR, dict[str, int] | None], ...] = (
            (CsvSourceIR(str(csv_path), columns=(("value", "amount"),)), None),
            (ParquetSourceIR(str(parquet_path), columns=("amount",)), None),
            (
                JsonSourceIR(
                    str(json_path),
                    columns=(("value", "amount"),),
                    query_params=(("page", SourceParamIR("page")),),
                ),
                {"page": 1},
            ),
        )
        for index, (source, params) in enumerate(cases):
            identity = f"file-{index}"
            bound = file_session.bind(source, source_identity=identity, source_params=params)
            frame = file_session.collect_bounded(
                bound.relation,
                source_identities=(identity,),
                purpose="authoring.sample",
                max_rows=2,
            )
            assert len(frame) == 2
        with pytest.raises(ValueError, match="missing"):
            file_session.bind(cases[2][0], source_identity="missing-param")


def test_compiled_read_rejects_forgery_foreign_session_and_unbound_source(
    session: SourceSession, tmp_path: Path
) -> None:
    read = _read(session)
    with pytest.raises(DatasourceSourceCapabilityError):
        session.execute(replace(read, sql="SELECT 0"))
    object.__setattr__(read, "sql", "SELECT 0")
    with pytest.raises(DatasourceSourceCapabilityError):
        session.execute(read)
    object.__setattr__(read, "sql", session._issued[id(read)][1].sql)
    object.__setattr__(read, "params", ((ibis.literal(1), 2),))
    with pytest.raises(DatasourceSourceCapabilityError):
        session.execute(read)
    other_backend = ibis.duckdb.connect(tmp_path / "other.duckdb")
    other = SourceSession(provider_for("duckdb"), _datasource("duckdb"), other_backend)
    try:
        with pytest.raises(DatasourceSourceCapabilityError):
            other.execute(read)
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


def test_empty_execution_keeps_the_compiled_columns(session: SourceSession) -> None:
    bound = session.bind(TableSourceIR("facts"), source_identity="facts@v1")
    qualified = session.qualify(
        bound, PhysicalRequirement("basic.empty", 1, frozenset({"scan", "filter"}))
    )
    expression = bound.relation.filter(bound.relation.id < 0)
    read = session.compile(
        qualified,
        expression,
        purpose="basic.empty",
        expected_schema=expression.schema().to_pyarrow(),
    )
    result = session.execute(read)
    assert result.empty
    assert tuple(result.columns) == tuple(read.schema.names)
    assert all(pd.api.types.is_integer_dtype(dtype) for dtype in result.dtypes)


def test_execute_failure_is_recorded_as_a_failed_query(
    session: SourceSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    read = _read(session)
    monkeypatch.setattr(
        session._backend, "to_pyarrow", Mock(side_effect=RuntimeError("read failed"))
    )
    with pytest.raises(RuntimeError, match="read failed"):
        session.execute(read)
    assert session.submissions[-1].state == "failed"
    records = [
        item for item in execution_records(session._log_root) if item.get("purpose") == "basic.rows"
    ]
    assert [item["event"] for item in records] == ["query.submitted", "query.completed"]
    assert records[0]["sql"] == read.sql
    assert records[1]["state"] == "failed"
    assert records[1]["error_type"] == "RuntimeError"


def test_borrowed_session_leaves_backend_owned_by_original(session: SourceSession) -> None:
    borrowed = SourceSession(
        session.provider,
        session.datasource,
        session._backend,
        owns_backend=False,
        project_root=session._log_root,
    )
    borrowed_read = _read(borrowed)
    assert len(borrowed.execute(borrowed_read)) == 2
    borrowed.close()
    assert borrowed._closed
    assert len(session.execute(_read(session))) == 2


def _key_read(source: SourceSession, keys: ir.Table) -> CompiledRead:
    bound = source.bind(TableSourceIR("facts"), source_identity="facts@keys")
    qualified = source.qualify(
        bound, PhysicalRequirement("basic.keys", 1, frozenset({"scan", "join", "project"}))
    )
    expression = bound.relation.limit(1).cross_join(keys).select(*keys.columns)
    return source.compile(
        qualified,
        expression,
        purpose="basic.keys",
        expected_schema=expression.schema().to_pyarrow(),
    )


def test_private_key_relation_keeps_arrow_schema_and_native_values(
    session: SourceSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    instant = datetime(2026, 10, 18, 1, 2, 3, 456789, tzinfo=timezone.utc)
    data = pa.table(
        {
            "key": pa.array([2**53 + 1, None], type=pa.int64()),
            "decimal": pa.array([Decimal("123456789.1250"), None], type=pa.decimal128(20, 4)),
            "instant": pa.array([instant, None], type=pa.timestamp("us", tz="UTC")),
            "flag": pa.array([False, None], type=pa.bool_()),
            "label": pa.array(["", None], type=pa.string()),
        }
    )
    execute = Mock(wraps=session._backend.to_pyarrow)
    monkeypatch.setattr(session._backend, "to_pyarrow", execute)
    keys = session._bind_key_relation(data)
    second = session._bind_key_relation(data)
    node = keys.op()
    assert isinstance(node, ops.InMemoryTable)
    assert node.name.startswith("_marivo_keys_") and node.name != second.op().name
    assert node.data.to_pyarrow(keys.schema()) is data
    assert keys.schema() == ibis.Schema.from_pyarrow(data.schema)
    execute.assert_not_called()
    assert session.submissions == []

    frame = session.execute(_key_read(session, keys))
    assert frame["key"].tolist() == [2**53 + 1, pd.NA]
    assert frame["decimal"].iloc[0] == Decimal("123456789.1250")
    assert frame["instant"].iloc[0] == instant
    assert not frame["flag"].iloc[0]
    assert frame["label"].iloc[0] == ""
    assert frame.iloc[1].isna().all()
    assert len(execute.call_args_list) == 1


def test_private_key_relation_requires_bound_source_and_exact_session_owner(
    session: SourceSession,
) -> None:
    data = pa.table({"key": pa.array([10], type=pa.int64())})
    owned = session._bind_key_relation(data)
    bound = session.bind(TableSourceIR("facts"), source_identity="facts@v1")
    qualified = session.qualify(
        bound, PhysicalRequirement("basic.keys", 1, frozenset({"scan", "join"}))
    )
    with pytest.raises(DatasourceSourceCapabilityError, match="unbound expression"):
        session.compile(
            qualified,
            owned,
            purpose="basic.keys",
            expected_schema=owned.schema().to_pyarrow(),
        )
    rebuilt = owned.op().copy().to_expr()
    assert rebuilt.op() is not owned.op()
    assert session.execute(_key_read(session, rebuilt))["key"].tolist() == [10]
    with SourceSession(
        session.provider, session.datasource, session._backend, owns_backend=False
    ) as foreign:
        foreign_keys = foreign._bind_key_relation(data)
        arbitrary = ibis.memtable(data)
        changed_data = ibis.memtable(pa.table({"key": [11]}))
        forged_name = changed_data.op().copy(name=owned.op().name).to_expr()
        for keys in (foreign_keys, arbitrary, forged_name):
            with pytest.raises(DatasourceSourceCapabilityError, match="additional physical input"):
                _key_read(session, keys)


def test_private_key_relation_accepts_typed_empty_and_rejects_null_type(
    session: SourceSession,
) -> None:
    empty = session._bind_key_relation(pa.table({"key": pa.array([], type=pa.int64())}))
    assert session.execute(_key_read(session, empty)).empty
    with pytest.raises(DatasourceSourceCapabilityError, match="concrete Arrow types"):
        session._bind_key_relation(pa.table({"key": [None]}))
    session.close()
    with pytest.raises(DatasourceSourceCapabilityError, match="closed source session"):
        session._bind_key_relation(pa.table({"key": pa.array([], type=pa.int64())}))


@pytest.mark.parametrize("fail", (False, True))
def test_private_key_relations_close_on_borrowed_success_and_failure(
    session: SourceSession, monkeypatch: pytest.MonkeyPatch, fail: bool
) -> None:
    backend = session._backend
    drop = Mock(wraps=backend.drop_view)
    monkeypatch.setattr(backend, "drop_view", drop)
    execute = backend.to_pyarrow
    with (
        pytest.raises(RuntimeError, match="read failed") if fail else nullcontext(),
        SourceSession(
            session.provider, session.datasource, backend, owns_backend=False
        ) as borrowed,
    ):
        keys = borrowed._bind_key_relation(pa.table({"key": [10]}))
        unused = borrowed._bind_key_relation(pa.table({"key": [11]}))
        read = _key_read(borrowed, keys)
        borrowed.execute(read)
        assert keys.op().name in backend.list_tables()
        if fail:
            monkeypatch.setattr(
                backend, "to_pyarrow", Mock(side_effect=RuntimeError("read failed"))
            )
            borrowed.execute(read)
    assert {call.args[0] for call in drop.call_args_list} == {
        keys.op().name,
        unused.op().name,
    }
    assert all(call.kwargs == {"force": True} for call in drop.call_args_list)
    assert keys.op().name not in backend.list_tables()
    assert unused.op().name not in backend.list_tables()
    assert not borrowed._backend_disconnected
    monkeypatch.setattr(backend, "to_pyarrow", execute)
    assert len(session.execute(_read(session))) == 2


def test_private_key_relation_cleanup_precedes_owned_connection_release(
    session: SourceSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = Mock()
    drop = Mock(wraps=session._backend.drop_view)
    disconnect = Mock(wraps=session._backend.disconnect)
    calls.attach_mock(drop, "drop")
    calls.attach_mock(disconnect, "disconnect")
    monkeypatch.setattr(session._backend, "drop_view", drop)
    monkeypatch.setattr(session._backend, "disconnect", disconnect)
    keys = session._bind_key_relation(pa.table({"key": [10]}))
    session.execute(_key_read(session, keys))
    session.close()
    assert [call[0] for call in calls.mock_calls] == ["drop", "disconnect"]
    drop.assert_called_once_with(keys.op().name, force=True)
    assert session._backend_disconnected


def test_private_key_relation_rejects_non_duckdb_provider() -> None:
    backend = Mock()
    backend.name = "postgres"
    with SourceSession(
        provider_for("postgres"), _datasource("postgres"), backend, owns_backend=False
    ) as source:
        with pytest.raises(DatasourceSourceCapabilityError) as caught:
            source._bind_key_relation(pa.table({"key": [10]}))
        assert caught.value.received == "postgres"
        assert "DuckDB" in caught.value.expected
    backend.execute.assert_not_called()
    backend.drop_view.assert_not_called()


def test_stub_provider_cannot_grant_basic_route() -> None:
    backend = Mock()
    backend.name = "postgres"
    backend.table.return_value = ibis.table({"id": "int64"}, name="facts")
    session = SourceSession(provider_for("postgres"), _datasource("postgres"), backend)
    try:
        bound = session.bind(TableSourceIR("facts"), source_identity="facts@v1")
        with pytest.raises(DatasourceSourceCapabilityError, match="not a live Ibis backend"):
            session.qualify(bound, PhysicalRequirement("basic.rows", 1, frozenset({"scan"})))
    finally:
        session.close()


def test_session_rejects_backend_from_another_provider(tmp_path: Path) -> None:
    backend = ibis.duckdb.connect(tmp_path / "other.duckdb")
    try:
        with pytest.raises(DatasourceSourceCapabilityError):
            SourceSession(provider_for("postgres"), _datasource("postgres"), backend)
    finally:
        backend.disconnect()


def test_join_union_require_explicit_local_engine_qualification(session: SourceSession) -> None:
    bound = session.bind(TableSourceIR("facts"), source_identity="source_operation-operations")
    requirement = PhysicalRequirement(
        "source_operation.correspondence", 1, frozenset({"scan", "join", "union"})
    )
    assert session.qualify(bound, requirement).requirement == requirement


def test_missing_selected_drivers_have_structured_install_repairs() -> None:
    code = """
import builtins
from marivo.datasource.adapters import provider_for
from marivo.datasource.errors import DatasourceConnectionError
from marivo.datasource.ir import AiContextIR, DatasourceIR, DatasourceSourceLocation
original = builtins.__import__
blocked_driver = None
def reject_driver(name, *args, **kwargs):
    if blocked_driver is not None and (name == blocked_driver or name.startswith(blocked_driver + '.')):
        raise ModuleNotFoundError('blocked optional driver', name=blocked_driver)
    return original(name, *args, **kwargs)
builtins.__import__ = reject_driver
for backend, blocked_driver in (
    ('postgres', 'psycopg'),
    ('mysql', 'MySQLdb'),
    ('trino', 'trino'),
    ('clickhouse', 'clickhouse_connect'),
):
    datasource = DatasourceIR(semantic_id='missing', name='missing', backend_type=backend,
        fields={'host':'127.0.0.1','database':'analysis','catalog':'iceberg','user':'reader'},
        env_refs={}, ai_context=AiContextIR(), python_symbol='missing',
        location=DatasourceSourceLocation('missing.py', 1))
    try:
        provider_for(backend).open(datasource)
    except DatasourceConnectionError as error:
        assert error.expected and error.received and error.repair
        assert 'marivo[' + backend + ']' in str(error)
    else:
        raise AssertionError('Missing selected dependency must fail before connection')
"""
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)
