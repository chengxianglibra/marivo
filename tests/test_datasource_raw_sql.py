"""Tests for the public datasource raw SQL escape hatch."""

from __future__ import annotations

from pathlib import Path

import ibis
import pytest

import marivo.datasource as md
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


def test_raw_sql_requires_reason_before_connecting(tmp_path: Path) -> None:
    _register_raw_sql_fixture(tmp_path)

    with pytest.raises(ValueError, match="reason must be non-empty"):
        md.raw_sql(md.ref("datasource.warehouse"), "SELECT 1", reason="", project_root=tmp_path)


def test_raw_sql_rejects_multi_statement_input(tmp_path: Path) -> None:
    _register_raw_sql_fixture(tmp_path)

    with pytest.raises(ValueError, match="single read-only statement"):
        md.raw_sql(
            md.ref("datasource.warehouse"),
            "SELECT 1; SELECT 2",
            reason="diagnose duplicate keys",
            project_root=tmp_path,
        )


def test_raw_sql_returns_bounded_terminal_only_result(tmp_path: Path) -> None:
    from marivo.datasource.manage import RawSqlResult

    _register_raw_sql_fixture(tmp_path)

    result = md.raw_sql(
        md.ref("datasource.warehouse"),
        "SELECT id, amount FROM orders ORDER BY id",
        limit=1,
        reason="diagnose order amount sample",
        project_root=tmp_path,
    )

    assert isinstance(result, RawSqlResult)
    assert result.datasource == md.ref("datasource.warehouse")
    assert result.reason == "diagnose order amount sample"
    assert result.returned_row_count == 1
    assert result.is_truncated is True
    rendered = result.render()
    assert "terminal_only" in rendered
    assert "escape_hatch" not in rendered
    assert "diagnose order amount sample" in rendered
    assert "expensive" in rendered
    assert 'md.help("raw_sql")' in rendered


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
        md.ref("datasource.warehouse"),
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
            md.ref("datasource.warehouse"),
            "INSERT INTO orders VALUES (3, 30.0)",
            reason="attempt to mutate via escape hatch",
            project_root=tmp_path,
        )
    assert isinstance(exc_info.value, DatasourceRawSqlError)
    # The write did not execute: orders still holds the fixture's two rows.
    result = md.raw_sql(
        md.ref("datasource.warehouse"),
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
    backend = build_backend(datasource_ir, read_only=True)
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
        {"host": "h", "settings": {"max_threads": 8}}
    )
    assert clickhouse["settings"]["access_mode"] == "read_only"
    assert clickhouse["settings"]["max_threads"] == 8
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

    def fetchmany(self, size: int) -> list[tuple[object, ...]]:
        self.fetchmany_calls.append(size)
        return self._rows[:size]

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._rows

    def fetchone(self) -> tuple[object, ...] | None:
        return self._rows[0] if self._rows else None


class _RawSqlBackend:
    def __init__(self, results: dict[str, _FakeCursor]) -> None:
        self.calls: list[str] = []
        self.results = results

    def raw_sql(self, sql: str) -> _FakeCursor:
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
        return None


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


def test_raw_sql_trino_describe_executes_directly_without_readonly_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    md.register(
        TrinoSpec(name="trino_wh", host="trino.example", catalog="hive"),
        project_root=tmp_path,
    )
    cursor = _FakeCursor(
        ["Column", "Type"],
        [("order_id", "bigint"), ("amount", "double")],
    )
    backend = _RawSqlBackend({"DESCRIBE orders": cursor})
    service = _RawSqlService(backend)

    import marivo.datasource.manage as manage_mod

    monkeypatch.setattr(manage_mod, "DatasourceConnectionService", lambda _root: service)
    _patch_trino_timeout_to_noop(monkeypatch)

    result = md.raw_sql(
        md.ref("datasource.trino_wh"),
        "DESCRIBE orders",
        limit=1,
        reason="diagnose trino table schema",
        project_root=tmp_path,
    )

    assert backend.calls == ["DESCRIBE orders"]
    assert service.calls == [("trino_wh", True)]
    assert result.rows == ({"Column": "order_id", "Type": "bigint"},)
    assert result.is_truncated is True
    assert cursor.fetchmany_calls == [2]


def test_raw_sql_trino_show_executes_directly_and_bounds_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    md.register(
        TrinoSpec(name="trino_wh", host="trino.example", catalog="hive"),
        project_root=tmp_path,
    )
    backend = _RawSqlBackend(
        {
            "SHOW COLUMNS FROM orders": _FakeCursor(
                ["Column", "Type"],
                [("order_id", "bigint"), ("amount", "double")],
            )
        }
    )
    service = _RawSqlService(backend)

    import marivo.datasource.manage as manage_mod

    monkeypatch.setattr(manage_mod, "DatasourceConnectionService", lambda _root: service)
    _patch_trino_timeout_to_noop(monkeypatch)

    result = md.raw_sql(
        md.ref("datasource.trino_wh"),
        "SHOW COLUMNS FROM orders",
        limit=2,
        reason="diagnose trino column metadata",
        project_root=tmp_path,
    )

    assert backend.calls == ["SHOW COLUMNS FROM orders"]
    assert result.returned_row_count == 2
    assert result.is_truncated is False


def test_raw_sql_trino_select_uses_subquery_wrap_without_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    md.register(
        TrinoSpec(name="trino_wh", host="trino.example", catalog="hive"),
        project_root=tmp_path,
    )
    backend = _RawSqlBackend({"marivo_raw_sql": _FakeCursor(["n"], [(2,)])})
    service = _RawSqlService(backend)

    import marivo.datasource.manage as manage_mod

    monkeypatch.setattr(manage_mod, "DatasourceConnectionService", lambda _root: service)
    _patch_trino_timeout_to_noop(monkeypatch)

    result = md.raw_sql(
        md.ref("datasource.trino_wh"),
        "SELECT count(*) AS n FROM orders",
        reason="diagnose row count",
        project_root=tmp_path,
    )

    assert backend.calls == [
        "SELECT * FROM (SELECT count(*) AS n FROM orders) AS marivo_raw_sql LIMIT 101"
    ]
    assert result.rows == ({"n": 2},)


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
            md.ref("datasource.warehouse"),
            "SELECT 1",
            reason="check",
            timeout_seconds=0,
            project_root=tmp_path,
        )


def test_raw_sql_result_carries_timeout_seconds(tmp_path: Path) -> None:
    _register_raw_sql_fixture(tmp_path)
    result = md.raw_sql(
        md.ref("datasource.warehouse"),
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
            md.ref("datasource.warehouse"),
            "SELECT 1",
            reason="check fail-closed",
            project_root=tmp_path,
        )
    assert exc_info.value.details["stage"] == "timeout_setup"


def test_raw_sql_exact_limit_reports_not_truncated(tmp_path: Path) -> None:
    _register_raw_sql_fixture(tmp_path)
    result = md.raw_sql(
        md.ref("datasource.warehouse"),
        "SELECT id FROM orders ORDER BY id",
        limit=2,
        reason="exact limit check",
        project_root=tmp_path,
    )
    assert result.returned_row_count == 2
    assert result.is_truncated is False


def test_raw_sql_extra_row_reports_truncated(tmp_path: Path) -> None:
    _register_raw_sql_fixture(tmp_path)
    result = md.raw_sql(
        md.ref("datasource.warehouse"),
        "SELECT id FROM orders ORDER BY id",
        limit=1,
        reason="truncation check",
        project_root=tmp_path,
    )
    assert result.returned_row_count == 1
    assert result.is_truncated is True


def test_raw_sql_result_display_shows_terminal_only_and_duration(tmp_path: Path) -> None:
    _register_raw_sql_fixture(tmp_path)
    result = md.raw_sql(
        md.ref("datasource.warehouse"),
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
        md.ref("datasource.warehouse"),
        "SELECT 1 AS ok",
        reason="duration check",
        project_root=tmp_path,
    )
    assert isinstance(result.duration_ms, int)
    assert result.duration_ms >= 0


def test_raw_sql_to_pandas_preserves_column_order_and_values(tmp_path: Path) -> None:
    _register_raw_sql_fixture(tmp_path)
    result = md.raw_sql(
        md.ref("datasource.warehouse"),
        "SELECT id, amount FROM orders ORDER BY id",
        limit=2,
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
        md.ref("datasource.warehouse"),
        "SELECT id FROM orders ORDER BY id",
        limit=1,
        reason="isolation check",
        project_root=tmp_path,
    )
    df = result.to_pandas()
    df.iloc[0, 0] = 999
    assert result.rows[0]["id"] == 1


def test_raw_sql_to_pandas_recursive_isolation_for_object_columns() -> None:
    from marivo.datasource.manage import RawSqlResult

    result = RawSqlResult(
        datasource=md.ref("datasource.wh"),
        backend_type="duckdb",
        sql="SELECT data FROM tbl",
        reason="recursive isolation",
        columns=("data",),
        types={},
        rows=({"data": [1, 2, 3]},),
        requested_limit=10,
        returned_row_count=1,
        is_truncated=False,
        timeout_seconds=30,
        duration_ms=5,
        warnings=(),
    )
    df = result.to_pandas()
    assert df.iloc[0, 0] == [1, 2, 3]
    df.iloc[0, 0].append(999)
    assert result.rows[0]["data"] == [1, 2, 3]


def test_raw_sql_error_includes_stage_and_timeout(tmp_path: Path) -> None:
    _register_raw_sql_fixture(tmp_path)
    with pytest.raises(DatasourceRawSqlError) as exc_info:
        md.raw_sql(
            md.ref("datasource.warehouse"),
            "INSERT INTO orders VALUES (3, 30.0)",
            reason="write attempt",
            timeout_seconds=10,
            project_root=tmp_path,
        )
    details = exc_info.value.details
    assert details["stage"] == "execution"
    assert details["timeout_seconds"] == 10
    assert details["reason"] == "write attempt"
    rendered = str(exc_info.value)
    assert "terminal" in rendered.lower()
    assert "no analysis artifact" in rendered.lower()
    assert "md.help" in rendered.lower() or "raw_sql" in rendered.lower()


def test_raw_sql_error_timeout_setup_stage_mentions_no_execution(
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
            md.ref("datasource.warehouse"),
            "SELECT 1",
            reason="no timeout",
            project_root=tmp_path,
        )
    assert exc_info.value.details["stage"] == "timeout_setup"
    rendered = str(exc_info.value)
    assert "did not begin execution" in rendered.lower()
