"""Tests for the public datasource raw SQL escape hatch."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import ibis
import pytest
from ibis.backends import BaseBackend

import marivo.datasource as md
import marivo.semantic as ms
from marivo.analysis._capabilities.validation import validate_capability_inputs
from marivo.analysis.errors import AnalysisError
from marivo.datasource import store
from marivo.datasource.authoring import DuckDBSpec, TrinoSpec
from marivo.datasource.backends import build_backend
from marivo.datasource.engines import ENGINE_PROFILES
from marivo.datasource.errors import DatasourceError, DatasourceRawSqlError


def _register_raw_sql_fixture(project_root: Path) -> None:
    db_path = project_root / "warehouse.duckdb"
    con = ibis.duckdb.connect(db_path)
    con.create_table("orders", {"id": [1, 2], "amount": [10.0, 20.0]})
    con.disconnect()
    md.register(DuckDBSpec(name="warehouse", path=str(db_path)), project_root=project_root)


def _register_raw_sql_ranking_fixture(project_root: Path) -> None:
    db_path = project_root / "ranking.duckdb"
    con = ibis.duckdb.connect(db_path)
    con.create_table(
        "events",
        {"id": [1, 2, 3, 4, 5], "delta": [10.0, -5.0, 30.0, 5.0, 20.0]},
    )
    con.disconnect()
    md.register(DuckDBSpec(name="warehouse", path=str(db_path)), project_root=project_root)


def test_raw_sql_requires_reason_before_connecting(tmp_path: Path) -> None:
    _register_raw_sql_fixture(tmp_path)

    with pytest.raises(ValueError, match="reason must be non-empty"):
        md.raw_sql(ms.ref.datasource("warehouse"), "SELECT 1", reason="", project_root=tmp_path)


def test_raw_sql_rejects_multi_statement_input(tmp_path: Path) -> None:
    _register_raw_sql_fixture(tmp_path)

    with pytest.raises(ValueError, match="single read-only statement"):
        md.raw_sql(
            ms.ref.datasource("warehouse"),
            "SELECT 1; SELECT 2",
            reason="diagnose duplicate keys",
            project_root=tmp_path,
        )


@pytest.mark.parametrize(
    "sql",
    [
        "",
        " \n\t ",
        ";",
        ";;",
        " ; ; ",
        "-- comment only",
        "# comment only",
        "/* comment only */",
        "/* outer /* inner */ outer */",
        "/* comment containing ; SELECT 1 */",
        "-- SELECT 1;\r\n/* another comment */ ;",
        "; /* comment */ ; -- final comment",
        "/* unterminated comment",
    ],
)
def test_raw_sql_rejects_missing_statement_before_connecting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sql: str
) -> None:
    from marivo.datasource import manage as manage_mod

    def fail_connection(_root: Path | None) -> None:
        pytest.fail("SQL without a statement must fail before acquiring a connection")

    monkeypatch.setattr(manage_mod, "DatasourceConnectionService", fail_connection)
    with pytest.raises(ValueError, match="sql must contain a statement"):
        md.raw_sql(
            ms.ref.datasource("default"),
            sql,
            reason="reject missing statement",
            project_root=tmp_path,
        )


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1 AS ok",
        "-- leading comment\nSELECT 1 AS ok",
        "-- leading comment\rSELECT 1 AS ok",
        "/* leading comment */ SELECT 1 AS ok",
        "/* outer /* inner */ outer */ SELECT 1 AS ok",
        "/* first */ -- second\nSELECT 1 AS ok /* trailing comment */",
        "SELECT 1 AS ok WHERE '/* literal */' <> ''",
        "SELECT 1 AS ok WHERE '-- literal' <> ''",
        "SELECT 1 AS ok WHERE '# literal' <> ''",
    ],
)
def test_raw_sql_preserves_queries_with_comments(tmp_path: Path, sql: str) -> None:
    result = md.raw_sql(
        ms.ref.datasource("default"),
        sql,
        reason="preserve valid statement",
        project_root=tmp_path,
    )
    assert result.sql == sql
    assert result.rows == ({"ok": 1},)


def test_raw_sql_returns_complete_terminal_only_result(tmp_path: Path) -> None:
    from marivo.datasource.manage import RawSqlResult

    _register_raw_sql_fixture(tmp_path)

    result = md.raw_sql(
        ms.ref.datasource("warehouse"),
        "SELECT id, amount FROM orders ORDER BY id",
        reason="diagnose order amount sample",
        project_root=tmp_path,
    )

    assert isinstance(result, RawSqlResult)
    assert result.datasource == ms.ref.datasource("warehouse")
    assert result.reason == "diagnose order amount sample"
    assert result.returned_row_count == 2
    assert result.row_count == 2
    assert result.shape == (2, 2)
    assert result.row_count == result.shape[0]
    assert not hasattr(result, "contract")
    rendered = result.render()
    assert "terminal_only" in rendered
    assert "typed_reentry: false" in rendered
    assert "row_count_semantics: returned_query_rows" in rendered
    assert "returned_row_count: 2" in rendered
    assert "returned rows are not full-source cardinality" in rendered
    assert "semantic identity, canonical lineage, typed affordances" in rendered
    assert "escape_hatch" not in rendered
    assert "diagnose order amount sample" in rendered
    assert "expensive" in rendered
    assert 'marivo.help("datasource.raw_sql")' in rendered


def test_raw_sql_result_cannot_reenter_typed_analysis(tmp_path: Path) -> None:
    _register_raw_sql_fixture(tmp_path)
    result = md.raw_sql(
        ms.ref.datasource("warehouse"),
        "SELECT id, amount FROM orders ORDER BY id",
        reason="verify terminal result cannot reenter typed analysis",
        project_root=tmp_path,
    )

    with pytest.raises(AnalysisError, match="received RawSqlResult"):
        validate_capability_inputs("compare", current=result, baseline=result)


def test_raw_sql_works_after_inspect_table_on_same_duckdb_file(tmp_path: Path) -> None:
    """raw_sql's read-only open must not be blocked by a prior discover/inspect call.

    Regression guard: ``inspect_table`` opens a read-write backend and must release
    it. DuckDB refuses a read-only connection to a file that already has a live
    read-write connection, so a leaked handle would surface as a connection error
    here. The discover-first workflow (gather evidence, then run a raw diagnostic)
    must keep working.
    """
    _register_raw_sql_fixture(tmp_path)

    from marivo.datasource.metadata import inspect_table as _inspect_table

    _inspect_table("warehouse", table="orders", project_root=tmp_path)

    result = md.raw_sql(
        ms.ref.datasource("warehouse"),
        "SELECT count(*) AS n FROM orders",
        reason="diagnose after inspect",
        project_root=tmp_path,
    )
    assert int(result.rows[0]["n"]) == 2


def test_raw_sql_write_attempt_surfaces_typed_error(tmp_path: Path) -> None:
    """A write attempt must surface as a typed DatasourceError, never a silent side effect."""
    _register_raw_sql_fixture(tmp_path)

    with pytest.raises(DatasourceError) as exc_info:
        md.raw_sql(
            ms.ref.datasource("warehouse"),
            "INSERT INTO orders VALUES (3, 30.0)",
            reason="attempt to mutate via escape hatch",
            project_root=tmp_path,
        )
    assert isinstance(exc_info.value, DatasourceRawSqlError)
    # The write did not execute: orders still holds the fixture's two rows.
    result = md.raw_sql(
        ms.ref.datasource("warehouse"),
        "SELECT count(*) AS n FROM orders",
        reason="verify no mutation",
        project_root=tmp_path,
    )
    assert int(result.rows[0]["n"]) == 2


def test_build_backend_read_only_rejects_writes(tmp_path: Path) -> None:
    """read_only=True opens a connection that rejects DDL/writes server-side."""
    _register_raw_sql_fixture(tmp_path)
    datasource_ir = store.load_one("warehouse", project_root=tmp_path)
    assert datasource_ir is not None
    backend = build_backend(datasource_ir, read_only=True).backend
    try:
        with pytest.raises(Exception):
            backend.raw_sql("CREATE TABLE evil (a INT)")
    finally:
        disconnect = getattr(backend, "disconnect", None)
        if callable(disconnect):
            disconnect()


def test_apply_read_only_kwargs_injects_connection_level_read_only() -> None:
    duckdb_profile = ENGINE_PROFILES["duckdb"]
    assert duckdb_profile.apply_read_only_kwargs({"path": "x"}) == {
        "path": "x",
        "read_only": True,
    }
    clickhouse_profile = ENGINE_PROFILES["clickhouse"]
    clickhouse = clickhouse_profile.apply_read_only_kwargs(
        {"host": "h", "settings": {"max_threads": 8, "readonly": 0}}
    )
    assert clickhouse["settings"]["readonly"] == 1
    assert clickhouse["settings"]["max_threads"] == 8
    assert "access_mode" not in clickhouse["settings"]
    timeout = clickhouse_profile.authoring_timeout
    assert timeout is not None
    backend = cast(
        "BaseBackend",
        SimpleNamespace(
            con=SimpleNamespace(
                params=dict(clickhouse["settings"]),
                server_settings={},
            )
        ),
    )
    with timeout(backend, 9):
        assert backend.con.params["readonly"] == 1
        assert backend.con.params["max_execution_time"] == "9"
    assert backend.con.params["readonly"] == 1
    assert "max_execution_time" not in backend.con.params
    # Transaction-based backends enforce read-only via transaction, not kwargs.
    postgres_profile = ENGINE_PROFILES["postgres"]
    assert postgres_profile.apply_read_only_kwargs({"host": "h"}) == {"host": "h"}
    trino_profile = ENGINE_PROFILES["trino"]
    assert trino_profile.apply_read_only_kwargs({"host": "h"}) == {"host": "h"}
    mysql_profile = ENGINE_PROFILES["mysql"]
    assert mysql_profile.apply_read_only_kwargs({"host": "h"}) == {"host": "h"}


class _FakeCursor:
    def __init__(self, columns: list[str], rows: list[tuple[object, ...]]) -> None:
        self.description = [(column, None) for column in columns]
        self._rows = rows
        self.fetchmany_calls: list[int] = []
        self.fetchall_calls = 0

    def fetchmany(self, size: int) -> list[tuple[object, ...]]:
        self.fetchmany_calls.append(size)
        return self._rows[:size]

    def fetchall(self) -> list[tuple[object, ...]]:
        self.fetchall_calls += 1
        return self._rows

    def fetchone(self) -> tuple[object, ...] | None:
        return self._rows[0] if self._rows else None


class _RawSqlBackend:
    def __init__(self, results: dict[str, object]) -> None:
        self.calls: list[str] = []
        self.results = results
        self.closed = False

    def raw_sql(self, sql: str) -> object:
        self.calls.append(sql)
        for token, cursor in self.results.items():
            if token in sql:
                return cursor
        return _FakeCursor([], [])


class _RawSqlBackendContext:
    def __init__(self, backend: _RawSqlBackend) -> None:
        self.backend = backend

    def __enter__(self) -> _RawSqlBackend:
        return self.backend

    def __exit__(self, *exc_info: object) -> None:
        self.backend.closed = True


class _RawSqlService:
    def __init__(self, backend: _RawSqlBackend) -> None:
        self.backend = backend
        self.calls: list[tuple[str, bool]] = []

    def use_backend(self, datasource: str, *, read_only: bool) -> _RawSqlBackendContext:
        self.calls.append((datasource, read_only))
        return _RawSqlBackendContext(self.backend)


def _patch_trino_timeout_to_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    import dataclasses
    from contextlib import nullcontext

    from marivo.datasource import manage as manage_mod
    from marivo.datasource.engines import require_profile_for_backend_type

    original = require_profile_for_backend_type
    trino_profile = original("trino")
    noop_profile = dataclasses.replace(
        trino_profile,
        authoring_timeout=lambda backend, ts: nullcontext(),
    )

    def _patched(backend_type: str):
        if backend_type == "trino":
            return noop_profile
        return original(backend_type)

    monkeypatch.setattr(manage_mod, "require_profile_for_backend_type", _patched)


@pytest.fixture
def trino_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _RawSqlBackend:
    from marivo.datasource import manage as manage_mod

    md.register(
        TrinoSpec(name="trino_wh", host="trino.example", catalog="hive", user_env="TRINO_USER"),
        project_root=tmp_path,
    )
    backend = _RawSqlBackend({})
    service = _RawSqlService(backend)
    monkeypatch.setattr(manage_mod, "DatasourceConnectionService", lambda _root: service)
    _patch_trino_timeout_to_noop(monkeypatch)
    return backend


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT count(*) AS n FROM orders",
        "SELECT category, sum(amount) AS delta FROM orders GROUP BY category ORDER BY delta DESC",
        "SELECT id FROM orders ORDER BY id LIMIT 5",
        "SELECT id FROM orders ORDER BY id OFFSET 10",
        "SELECT id FROM orders ORDER BY id LIMIT 5 OFFSET 10",
        "SELECT id FROM orders ORDER BY id FETCH FIRST 5 ROWS ONLY",
        "WITH recent AS (SELECT id FROM orders WHERE amount > 0) SELECT id FROM recent ORDER BY id",
        "SELECT id FROM orders UNION ALL SELECT id FROM archived_orders ORDER BY id",
        "SELECT id FROM orders ORDER BY id DESC NULLS LAST",
        "SELECT id FROM orders ORDER BY id ASC NULLS FIRST",
        "SELECT id FROM orders TABLESAMPLE BERNOULLI(10)",
        "-- Preserve native SQL comments and spelling\nSELECT id FROM orders",
        "# Preserve backend line comments\nSELECT id FROM orders",
        "/*! SELECT id FROM orders */",
        "/*M! SELECT id FROM orders */",
        "SELECT * INTO new_t FROM orders",
        "DESCRIBE orders",
        "DESC orders",
        "SHOW COLUMNS FROM orders",
        "EXPLAIN SELECT id FROM orders",
    ],
)
def test_raw_sql_trino_executes_sql_unchanged(
    tmp_path: Path, trino_backend: _RawSqlBackend, sql: str
) -> None:
    # The backend is a substitute: no write statement is sent to a real database.
    trino_backend.results[sql] = _FakeCursor(["id"], [(1,), (2,)])
    result = md.raw_sql(
        ms.ref.datasource("trino_wh"),
        "  " + sql + ";  ",
        reason="verify native SQL passthrough",
        project_root=tmp_path,
    )
    assert trino_backend.calls == [sql]
    assert trino_backend.closed
    assert result.sql == sql
    assert result.rows == ({"id": 1}, {"id": 2})


@pytest.mark.parametrize("cursor_kind", ["dbapi", "clickhouse"])
@pytest.mark.parametrize("row_count", [0, 250])
@pytest.mark.parametrize("include_types", [False, True])
def test_raw_sql_fetches_complete_cursor_result(
    tmp_path: Path,
    trino_backend: _RawSqlBackend,
    cursor_kind: str,
    row_count: int,
    include_types: bool,
) -> None:
    sql = "SELECT id FROM orders"
    rows = [(i,) for i in range(row_count)]
    cursor = (
        _FakeCursor(["id"], rows)
        if cursor_kind == "dbapi"
        else SimpleNamespace(column_names=["id"], result_rows=rows)
    )
    trino_backend.results[sql] = cursor
    result = md.raw_sql(
        ms.ref.datasource("trino_wh"),
        sql,
        reason="read all query rows",
        include_types=include_types,
        project_root=tmp_path,
    )
    assert result.rows == tuple({"id": i} for i in range(row_count))
    assert result.columns == ("id",)
    assert result.shape == (row_count, 1)
    assert result.types == ({"id": "None"} if include_types and cursor_kind == "dbapi" else {})
    assert trino_backend.closed
    if isinstance(cursor, _FakeCursor):
        assert cursor.fetchall_calls == 1
        assert cursor.fetchmany_calls == []


@pytest.mark.parametrize("failure_stage", ["execute", "fetch"])
def test_raw_sql_failure_releases_backend_without_partial_result(
    tmp_path: Path,
    trino_backend: _RawSqlBackend,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    sql = "SELECT id FROM orders"
    cursor = _FakeCursor(["id"], [(1,)])
    trino_backend.results[sql] = cursor

    def fail(*args: object) -> object:
        raise RuntimeError(
            "permission denied" if failure_stage == "execute" else "fetch interrupted"
        )

    if failure_stage == "execute":
        monkeypatch.setattr(trino_backend, "raw_sql", fail)
    else:
        monkeypatch.setattr(cursor, "fetchall", fail)
    with pytest.raises(DatasourceRawSqlError) as captured:
        md.raw_sql(
            ms.ref.datasource("trino_wh"),
            sql,
            reason="verify failure lifecycle",
            project_root=tmp_path,
        )
    assert trino_backend.closed
    error = captured.value
    assert error.effect_observed is not None
    assert error.effect_observed.query_executed
    assert "no side effects" not in str(error)
    assert ("permission denied" if failure_stage == "execute" else "fetch interrupted") in str(
        error
    )
    assert error.repair is not None
    assert error.repair.help_target.canonical_id == "raw_sql"


def test_mysql_authoring_timeout_opens_readonly_transaction() -> None:
    from marivo.datasource.engines.mysql import authoring_timeout

    class _MysqlBackend:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def raw_sql(self, sql: str) -> _FakeCursor:
            self.calls.append(sql)
            if "MAX_EXECUTION_TIME" in sql and sql.startswith("SELECT"):
                return _FakeCursor(["val"], [(1000,)])
            return _FakeCursor([], [])

    backend = _MysqlBackend()
    with authoring_timeout(backend, 5):
        backend.raw_sql("SELECT 1")
    assert backend.calls[0] == "SELECT @@SESSION.MAX_EXECUTION_TIME"
    assert backend.calls[1] == "START TRANSACTION READ ONLY"
    assert "SET SESSION MAX_EXECUTION_TIME = 5000" in backend.calls[2]
    assert backend.calls[3] == "SELECT 1"
    assert backend.calls[-2] == "ROLLBACK"
    assert "SET SESSION MAX_EXECUTION_TIME = 1000" in backend.calls[-1]


def test_raw_sql_rejects_non_positive_timeout(tmp_path: Path) -> None:
    _register_raw_sql_fixture(tmp_path)
    with pytest.raises(ValueError, match="timeout_seconds must be positive"):
        md.raw_sql(
            ms.ref.datasource("warehouse"),
            "SELECT 1",
            reason="check",
            timeout_seconds=0,
            project_root=tmp_path,
        )


def test_raw_sql_result_carries_timeout_seconds(tmp_path: Path) -> None:
    _register_raw_sql_fixture(tmp_path)
    result = md.raw_sql(
        ms.ref.datasource("warehouse"),
        "SELECT 1 AS ok",
        reason="check timeout",
        timeout_seconds=15,
        project_root=tmp_path,
    )
    assert result.timeout_seconds == 15


def test_raw_sql_fails_closed_when_timeout_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dataclasses

    from marivo.datasource import manage as manage_mod
    from marivo.datasource.engines import require_profile_for_backend_type

    _register_raw_sql_fixture(tmp_path)

    real_profile = require_profile_for_backend_type("duckdb")
    no_timeout_caps = dataclasses.replace(
        real_profile.authoring_capabilities, timeout_enforced=False
    )
    no_timeout_profile = dataclasses.replace(
        real_profile,
        authoring_timeout=None,
        authoring_capabilities=no_timeout_caps,
    )
    monkeypatch.setattr(
        manage_mod, "require_profile_for_backend_type", lambda bt: no_timeout_profile
    )

    with pytest.raises(DatasourceRawSqlError) as exc_info:
        md.raw_sql(
            ms.ref.datasource("warehouse"),
            "SELECT 1",
            reason="check fail-closed",
            project_root=tmp_path,
        )
    err = exc_info.value
    assert err.effect_observed is not None
    assert err.effect_observed.query_executed is False
    assert "no enforceable timeout" in err.message


@pytest.mark.parametrize("sql_limit", [None, 20, 150])
def test_raw_sql_returns_all_query_rows_without_client_limit(
    tmp_path: Path, sql_limit: int | None
) -> None:
    from marivo.render import _DEFAULT_MAX_OUTPUT_BYTES

    sql = "SELECT range AS id FROM range(250) ORDER BY id"
    if sql_limit is not None:
        sql += f" LIMIT {sql_limit}"
    result = md.raw_sql(
        ms.ref.datasource("default"),
        sql,
        reason="verify caller-controlled query size",
        project_root=tmp_path,
    )
    expected_count = 250 if sql_limit is None else sql_limit
    assert result.rows == tuple({"id": i} for i in range(expected_count))
    assert result.returned_row_count == result.row_count == expected_count
    assert result.shape == (expected_count, 1)
    assert result.to_pandas()["id"].tolist() == list(range(expected_count))
    assert not hasattr(result, "requested_limit")
    assert not hasattr(result, "is_truncated")
    assert "TRUNCATED" not in result.render()
    assert len(result.render().encode("utf-8")) <= _DEFAULT_MAX_OUTPUT_BYTES
    assert len(result.rows) == expected_count


def test_raw_sql_order_by_limit_returns_true_top_n(tmp_path: Path) -> None:
    """SQL itself owns Top-N selection and ordering."""
    _register_raw_sql_ranking_fixture(tmp_path)
    result = md.raw_sql(
        ms.ref.datasource("warehouse"),
        "SELECT id, delta FROM events ORDER BY delta DESC LIMIT 3",
        reason="top deltas",
        project_root=tmp_path,
    )
    assert [row["id"] for row in result.rows] == [3, 5, 1]


def test_raw_sql_result_display_shows_terminal_only_and_duration(tmp_path: Path) -> None:
    _register_raw_sql_fixture(tmp_path)
    result = md.raw_sql(
        ms.ref.datasource("warehouse"),
        "SELECT 1 AS ok",
        reason="display check",
        timeout_seconds=10,
        project_root=tmp_path,
    )
    rendered = result.render()
    assert "terminal_only" in rendered
    assert "escape_hatch" not in rendered
    assert "10" in rendered
    assert "duration" in rendered.lower() or "ms" in rendered.lower()
    assert "no metric" in rendered.lower() or "no semantic" in rendered.lower()
    assert ".to_pandas()" in rendered


def test_raw_sql_result_carries_duration_ms(tmp_path: Path) -> None:
    _register_raw_sql_fixture(tmp_path)
    result = md.raw_sql(
        ms.ref.datasource("warehouse"),
        "SELECT 1 AS ok",
        reason="duration check",
        project_root=tmp_path,
    )
    assert isinstance(result.duration_ms, int)
    assert result.duration_ms >= 0


def test_raw_sql_to_pandas_preserves_column_order_and_values(tmp_path: Path) -> None:
    _register_raw_sql_fixture(tmp_path)
    result = md.raw_sql(
        ms.ref.datasource("warehouse"),
        "SELECT id, amount FROM orders ORDER BY id",
        reason="to_pandas check",
        project_root=tmp_path,
    )
    df = result.to_pandas()
    assert list(df.columns) == ["id", "amount"]
    assert len(df) == 2
    assert df.iloc[0]["id"] == 1
    assert df.iloc[0]["amount"] == 10.0


def test_raw_sql_to_pandas_is_defensively_isolated(tmp_path: Path) -> None:
    _register_raw_sql_fixture(tmp_path)
    result = md.raw_sql(
        ms.ref.datasource("warehouse"),
        "SELECT id FROM orders ORDER BY id",
        reason="isolation check",
        project_root=tmp_path,
    )
    df = result.to_pandas()
    df.iloc[0, 0] = 999
    assert result.rows[0]["id"] == 1


def test_raw_sql_to_pandas_recursive_isolation_for_object_columns() -> None:
    from marivo.datasource.manage import RawSqlResult

    result = RawSqlResult(
        datasource=ms.ref.datasource("wh"),
        backend_type="duckdb",
        sql="SELECT data FROM tbl",
        reason="recursive isolation",
        columns=("data",),
        types={},
        rows=({"data": [1, 2, 3]},),
        returned_row_count=1,
        timeout_seconds=30,
        duration_ms=5,
        warnings=(),
    )
    df = result.to_pandas()
    assert df.iloc[0, 0] == [1, 2, 3]
    df.iloc[0, 0].append(999)
    assert result.rows[0]["data"] == [1, 2, 3]


def test_raw_sql_result_rejects_returned_row_count_drift() -> None:
    from marivo.datasource.manage import RawSqlResult

    with pytest.raises(ValueError, match="returned_row_count must equal"):
        RawSqlResult(
            datasource=ms.ref.datasource("wh"),
            backend_type="duckdb",
            sql="SELECT ok",
            reason="validate query result count",
            columns=("ok",),
            types={"ok": "int64"},
            rows=({"ok": 1},),
            returned_row_count=2,
            timeout_seconds=30,
            duration_ms=5,
            warnings=(),
        )


def test_raw_sql_terminal_facts_render_in_contract_order(tmp_path: Path) -> None:
    _register_raw_sql_fixture(tmp_path)
    result = md.raw_sql(
        ms.ref.datasource("warehouse"),
        "SELECT id, amount FROM orders ORDER BY id",
        reason="inspect terminal result facts",
        project_root=tmp_path,
    )

    rendered = result.render()
    labels = (
        "terminal_only:",
        "typed_reentry:",
        "row_count_semantics:",
        "returned_row_count:",
        "preserves:",
        "does_not_preserve:",
    )
    positions = tuple(rendered.index(label) for label in labels)
    assert positions == tuple(sorted(positions))
    assert not hasattr(result, "contract")
    for pandas_convenience in ("head", "dtypes", "groupby", "plot"):
        assert not hasattr(result, pandas_convenience)


def test_raw_sql_error_includes_execution_context(tmp_path: Path) -> None:
    _register_raw_sql_fixture(tmp_path)
    with pytest.raises(DatasourceRawSqlError) as exc_info:
        md.raw_sql(
            ms.ref.datasource("warehouse"),
            "INSERT INTO orders VALUES (3, 30.0)",
            reason="write attempt",
            timeout_seconds=10,
            project_root=tmp_path,
        )
    err = exc_info.value
    assert err.effect_observed is not None
    assert err.effect_observed.query_executed is True
    assert "warehouse" in err.location
    rendered = str(err)
    assert "raw_sql execution or result fetching failed" in rendered
    assert "Repair:" in rendered
    assert "md.help" in rendered.lower() or "raw_sql" in rendered.lower()


def test_raw_sql_error_timeout_setup_reports_no_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dataclasses

    from marivo.datasource import manage as manage_mod
    from marivo.datasource.engines import require_profile_for_backend_type

    _register_raw_sql_fixture(tmp_path)
    real_profile = require_profile_for_backend_type("duckdb")
    no_timeout_caps = dataclasses.replace(
        real_profile.authoring_capabilities, timeout_enforced=False
    )
    no_timeout_profile = dataclasses.replace(
        real_profile,
        authoring_timeout=None,
        authoring_capabilities=no_timeout_caps,
    )
    monkeypatch.setattr(
        manage_mod, "require_profile_for_backend_type", lambda bt: no_timeout_profile
    )

    with pytest.raises(DatasourceRawSqlError) as exc_info:
        md.raw_sql(
            ms.ref.datasource("warehouse"),
            "SELECT 1",
            reason="no timeout",
            project_root=tmp_path,
        )
    err = exc_info.value
    assert err.effect_observed is not None
    assert err.effect_observed.query_executed is False
    assert "no enforceable timeout" in err.message
