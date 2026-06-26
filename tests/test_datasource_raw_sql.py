"""Tests for the public datasource raw SQL escape hatch."""

from __future__ import annotations

from pathlib import Path

import ibis
import pytest

import marivo.datasource as md
from marivo.datasource import store
from marivo.datasource.authoring import _DuckDBSpec
from marivo.datasource.backends import _with_read_only_kwargs, build_backend
from marivo.datasource.errors import DatasourceError, DatasourceRawSqlError
from marivo.datasource.manage import _execute_readonly


def _register_raw_sql_fixture(project_root: Path) -> None:
    db_path = project_root / "warehouse.duckdb"
    con = ibis.duckdb.connect(db_path)
    con.create_table("orders", {"id": [1, 2], "amount": [10.0, 20.0]})
    con.disconnect()
    md.register(_DuckDBSpec(name="warehouse", path=str(db_path)), project_root=project_root)


def test_raw_sql_requires_reason_before_connecting(tmp_path: Path) -> None:
    _register_raw_sql_fixture(tmp_path)

    with pytest.raises(ValueError, match="reason must be non-empty"):
        md.raw_sql(md.ref("warehouse"), "SELECT 1", reason="", project_root=tmp_path)


def test_raw_sql_rejects_multi_statement_input(tmp_path: Path) -> None:
    _register_raw_sql_fixture(tmp_path)

    with pytest.raises(ValueError, match="single read-only statement"):
        md.raw_sql(
            md.ref("warehouse"),
            "SELECT 1; SELECT 2",
            reason="diagnose duplicate keys",
            project_root=tmp_path,
        )


def test_raw_sql_returns_bounded_escape_hatch_result(tmp_path: Path) -> None:
    _register_raw_sql_fixture(tmp_path)

    result = md.raw_sql(
        md.ref("warehouse"),
        "SELECT id, amount FROM orders ORDER BY id",
        limit=1,
        reason="diagnose order amount sample",
        project_root=tmp_path,
    )

    assert isinstance(result, md.RawSqlResult)
    assert result.datasource == md.ref("warehouse")
    assert result.reason == "diagnose order amount sample"
    assert result.returned_row_count == 1
    assert result.is_truncated is True
    rendered = result.render()
    assert "escape_hatch" in rendered
    assert "diagnose order amount sample" in rendered


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
        md.ref("warehouse"),
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
            md.ref("warehouse"),
            "INSERT INTO orders VALUES (3, 30.0)",
            reason="attempt to mutate via escape hatch",
            project_root=tmp_path,
        )
    assert isinstance(exc_info.value, DatasourceRawSqlError)
    # The write did not execute: orders still holds the fixture's two rows.
    result = md.raw_sql(
        md.ref("warehouse"),
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


def test_with_read_only_kwargs_injects_connection_level_read_only() -> None:
    assert _with_read_only_kwargs("duckdb", {"path": "x"}, True) == {
        "path": "x",
        "read_only": True,
    }
    clickhouse = _with_read_only_kwargs(
        "clickhouse", {"host": "h", "settings": {"max_threads": 8}}, True
    )
    assert clickhouse["settings"]["access_mode"] == "read_only"
    assert clickhouse["settings"]["max_threads"] == 8
    # Transaction-based backends enforce read-only via transaction, not kwargs.
    assert _with_read_only_kwargs("postgres", {"host": "h"}, True) == {"host": "h"}
    assert _with_read_only_kwargs("trino", {"host": "h"}, True) == {"host": "h"}
    assert _with_read_only_kwargs("mysql", {"host": "h"}, True) == {"host": "h"}
    # read_only=False leaves kwargs untouched.
    assert _with_read_only_kwargs("duckdb", {"path": "x"}, False) == {"path": "x"}


class _FakeBackend:
    """Records raw_sql calls and optionally fails on a specific statement."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[str] = []
        self._fail_on = fail_on

    def raw_sql(self, sql: str) -> str:
        self.calls.append(sql)
        if self._fail_on is not None and sql == self._fail_on:
            raise RuntimeError("boom")
        return sql


def test_execute_readonly_transaction_sequence_per_backend() -> None:
    # DuckDB/ClickHouse: connection already read-only, no transaction control.
    duck = _FakeBackend()
    assert _execute_readonly(duck, "duckdb", "SELECT 1") == "SELECT 1"
    assert duck.calls == ["SELECT 1"]
    click = _FakeBackend()
    assert _execute_readonly(click, "clickhouse", "SELECT 1") == "SELECT 1"
    assert click.calls == ["SELECT 1"]

    # Postgres: BEGIN READ ONLY ... COMMIT on success.
    pg = _FakeBackend()
    assert _execute_readonly(pg, "postgres", "SELECT 1") == "SELECT 1"
    assert pg.calls == ["BEGIN READ ONLY", "SELECT 1", "COMMIT"]

    # Trino/MySQL: START TRANSACTION READ ONLY ... COMMIT.
    trino = _FakeBackend()
    _execute_readonly(trino, "trino", "SELECT 1")
    assert trino.calls[0] == "START TRANSACTION READ ONLY"
    assert trino.calls == ["START TRANSACTION READ ONLY", "SELECT 1", "COMMIT"]
    mysql = _FakeBackend()
    _execute_readonly(mysql, "mysql", "SELECT 1")
    assert mysql.calls[0] == "START TRANSACTION READ ONLY"
    assert mysql.calls[-1] == "COMMIT"


def test_execute_readonly_rolls_back_on_failure() -> None:
    pg = _FakeBackend(fail_on="SELECT 1")
    with pytest.raises(RuntimeError, match="boom"):
        _execute_readonly(pg, "postgres", "SELECT 1")
    assert pg.calls == ["BEGIN READ ONLY", "SELECT 1", "ROLLBACK"]
