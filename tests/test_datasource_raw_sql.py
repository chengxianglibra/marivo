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


def test_raw_sql_returns_bounded_terminal_only_result(tmp_path: Path) -> None:
    from marivo.datasource.manage import RawSqlResult

    _register_raw_sql_fixture(tmp_path)

    result = md.raw_sql(
        ms.ref.datasource("warehouse"),
        "SELECT id, amount FROM orders ORDER BY id",
        limit=1,
        reason="diagnose order amount sample",
        project_root=tmp_path,
    )

    assert isinstance(result, RawSqlResult)
    assert result.datasource == ms.ref.datasource("warehouse")
    assert result.reason == "diagnose order amount sample"
    assert result.returned_row_count == 1
    assert result.row_count == 1
    assert result.shape == (1, 2)
    assert result.row_count == result.shape[0]
    assert result.is_truncated is True
    assert not hasattr(result, "contract")
    rendered = result.render()
    assert "terminal_only" in rendered
    assert "typed_reentry: false" in rendered
    assert "row_count_semantics: returned_bounded_rows" in rendered
    assert "returned_row_count: 1" in rendered
    assert "requested_limit: 1" in rendered
    assert "is_truncated: true" in rendered
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
        limit=1,
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
        ms.ref.datasource("trino_wh"),
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
        ms.ref.datasource("trino_wh"),
        "SHOW COLUMNS FROM orders",
        limit=2,
        reason="diagnose trino column metadata",
        project_root=tmp_path,
    )

    assert backend.calls == ["SHOW COLUMNS FROM orders"]
    assert result.returned_row_count == 2
    assert result.is_truncated is False


def test_raw_sql_trino_select_injects_probe_limit_without_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    md.register(
        TrinoSpec(name="trino_wh", host="trino.example", catalog="hive"),
        project_root=tmp_path,
    )
    backend = _RawSqlBackend({"FROM orders LIMIT 101": _FakeCursor(["n"], [(2,)])})
    service = _RawSqlService(backend)

    import marivo.datasource.manage as manage_mod

    monkeypatch.setattr(manage_mod, "DatasourceConnectionService", lambda _root: service)
    _patch_trino_timeout_to_noop(monkeypatch)

    result = md.raw_sql(
        ms.ref.datasource("trino_wh"),
        "SELECT count(*) AS n FROM orders",
        limit=100,
        reason="diagnose row count",
        project_root=tmp_path,
    )

    assert backend.calls == ["SELECT COUNT(*) AS n FROM orders LIMIT 101"]
    assert result.rows == ({"n": 2},)


def test_raw_sql_trino_group_by_order_by_keeps_order_before_probe_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The truncation-probe LIMIT must land in the same top-level query as ORDER BY.

    Regression guard for Trino's Top-N contract: ORDER BY only affects the query
    that directly contains it, so an unordered outer wrapper discards the user's
    ``ORDER BY delta DESC`` before truncation and can admit negative deltas into
    an intended "top growth" result.
    """
    md.register(
        TrinoSpec(name="trino_wh", host="trino.example", catalog="hive"),
        project_root=tmp_path,
    )
    backend = _RawSqlBackend(
        {"ORDER BY delta DESC LIMIT 101": _FakeCursor(["category", "delta"], [("a", 5.0)])}
    )
    service = _RawSqlService(backend)

    import marivo.datasource.manage as manage_mod

    monkeypatch.setattr(manage_mod, "DatasourceConnectionService", lambda _root: service)
    _patch_trino_timeout_to_noop(monkeypatch)

    result = md.raw_sql(
        ms.ref.datasource("trino_wh"),
        "SELECT category, sum(amount) AS delta FROM orders GROUP BY category ORDER BY delta DESC",
        limit=100,
        reason="top categories by delta",
        project_root=tmp_path,
    )

    assert backend.calls == [
        "SELECT category, SUM(amount) AS delta FROM orders GROUP BY category "
        "ORDER BY delta DESC LIMIT 101"
    ]
    assert result.rows == ({"category": "a", "delta": 5.0},)


def test_raw_sql_trino_user_limit_is_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    md.register(
        TrinoSpec(name="trino_wh", host="trino.example", catalog="hive"),
        project_root=tmp_path,
    )
    backend = _RawSqlBackend({"LIMIT 5": _FakeCursor(["id"], [(1,)])})
    service = _RawSqlService(backend)

    import marivo.datasource.manage as manage_mod

    monkeypatch.setattr(manage_mod, "DatasourceConnectionService", lambda _root: service)
    _patch_trino_timeout_to_noop(monkeypatch)

    md.raw_sql(
        ms.ref.datasource("trino_wh"),
        "SELECT id FROM orders ORDER BY id LIMIT 5",
        limit=100,
        reason="user limit",
        project_root=tmp_path,
    )

    assert backend.calls == ["SELECT id FROM orders ORDER BY id LIMIT 5"]


def test_raw_sql_trino_user_offset_is_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    md.register(
        TrinoSpec(name="trino_wh", host="trino.example", catalog="hive"),
        project_root=tmp_path,
    )
    backend = _RawSqlBackend({"OFFSET 10": _FakeCursor(["id"], [(11,)])})
    service = _RawSqlService(backend)

    import marivo.datasource.manage as manage_mod

    monkeypatch.setattr(manage_mod, "DatasourceConnectionService", lambda _root: service)
    _patch_trino_timeout_to_noop(monkeypatch)

    md.raw_sql(
        ms.ref.datasource("trino_wh"),
        "SELECT id FROM orders ORDER BY id LIMIT 5 OFFSET 10",
        limit=100,
        reason="user offset",
        project_root=tmp_path,
    )

    assert backend.calls == ["SELECT id FROM orders ORDER BY id LIMIT 5 OFFSET 10"]


def test_raw_sql_trino_user_fetch_first_is_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    md.register(
        TrinoSpec(name="trino_wh", host="trino.example", catalog="hive"),
        project_root=tmp_path,
    )
    backend = _RawSqlBackend({"FETCH FIRST 5 ROWS ONLY": _FakeCursor(["id"], [(1,)])})
    service = _RawSqlService(backend)

    import marivo.datasource.manage as manage_mod

    monkeypatch.setattr(manage_mod, "DatasourceConnectionService", lambda _root: service)
    _patch_trino_timeout_to_noop(monkeypatch)

    md.raw_sql(
        ms.ref.datasource("trino_wh"),
        "SELECT id FROM orders ORDER BY id FETCH FIRST 5 ROWS ONLY",
        limit=100,
        reason="user fetch first",
        project_root=tmp_path,
    )

    assert backend.calls == ["SELECT id FROM orders ORDER BY id FETCH FIRST 5 ROWS ONLY"]


def test_raw_sql_trino_cte_keeps_order_before_probe_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    md.register(
        TrinoSpec(name="trino_wh", host="trino.example", catalog="hive"),
        project_root=tmp_path,
    )
    backend = _RawSqlBackend(
        {"ORDER BY amount DESC LIMIT 101": _FakeCursor(["id", "amount"], [(2, 20.0)])}
    )
    service = _RawSqlService(backend)

    import marivo.datasource.manage as manage_mod

    monkeypatch.setattr(manage_mod, "DatasourceConnectionService", lambda _root: service)
    _patch_trino_timeout_to_noop(monkeypatch)

    md.raw_sql(
        ms.ref.datasource("trino_wh"),
        "WITH recent AS (SELECT id, amount FROM orders WHERE amount > 0) "
        "SELECT id, amount FROM recent ORDER BY amount DESC",
        limit=100,
        reason="top recent amounts",
        project_root=tmp_path,
    )

    assert backend.calls == [
        "WITH recent AS (SELECT id, amount FROM orders WHERE amount > 0) "
        "SELECT id, amount FROM recent ORDER BY amount DESC LIMIT 101"
    ]


def _bounded(statement: str, limit: int = 100) -> str:
    from marivo.datasource.manage import _bounded_execution_sql

    return _bounded_execution_sql(statement, limit)


def test_bounded_execution_sql_select_into_falls_back_to_invalid_wrapper() -> None:
    """``SELECT ... INTO`` is a write; the round-trip must not turn it into a CTAS."""
    assert _bounded("SELECT * INTO new_t FROM orders") == (
        "SELECT * FROM (SELECT * INTO new_t FROM orders) AS marivo_raw_sql LIMIT 101"
    )
    assert _bounded("SELECT id INTO @x FROM t") == (
        "SELECT * FROM (SELECT id INTO @x FROM t) AS marivo_raw_sql LIMIT 101"
    )


def test_bounded_execution_sql_explicit_nulls_ordering_falls_back_to_wrapper() -> None:
    """Explicit ``NULLS FIRST/LAST`` is preserved verbatim, not stripped."""
    assert _bounded("SELECT id FROM t ORDER BY id DESC NULLS LAST") == (
        "SELECT * FROM (SELECT id FROM t ORDER BY id DESC NULLS LAST) AS marivo_raw_sql LIMIT 101"
    )
    assert _bounded("SELECT id FROM t ORDER BY id ASC NULLS FIRST") == (
        "SELECT * FROM (SELECT id FROM t ORDER BY id ASC NULLS FIRST) AS marivo_raw_sql LIMIT 101"
    )


def test_bounded_execution_sql_tablesample_falls_back_to_wrapper() -> None:
    """``TABLESAMPLE BERNOULLI(n)`` must keep its percentage unit, not become rows."""
    assert _bounded("SELECT id FROM orders TABLESAMPLE BERNOULLI(10)") == (
        "SELECT * FROM (SELECT id FROM orders TABLESAMPLE BERNOULLI(10)) AS marivo_raw_sql LIMIT 101"
    )


def test_bounded_execution_sql_plain_select_still_injects_probe_limit() -> None:
    """A hazard-free statement still gets the same-top-level probe LIMIT."""
    assert _bounded("SELECT id FROM t ORDER BY id") == "SELECT id FROM t ORDER BY id LIMIT 101"


def test_raw_sql_trino_select_into_is_not_normalized_to_ctas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    md.register(
        TrinoSpec(name="trino_wh", host="trino.example", catalog="hive"),
        project_root=tmp_path,
    )
    backend = _RawSqlBackend({"marivo_raw_sql": _FakeCursor(["n"], [(0,)])})
    service = _RawSqlService(backend)

    import marivo.datasource.manage as manage_mod

    monkeypatch.setattr(manage_mod, "DatasourceConnectionService", lambda _root: service)
    _patch_trino_timeout_to_noop(monkeypatch)

    md.raw_sql(
        ms.ref.datasource("trino_wh"),
        "SELECT * INTO new_t FROM orders",
        limit=100,
        reason="attempt write via select into",
        project_root=tmp_path,
    )

    # The subquery wrapper (invalid SQL on every backend) is what was executed,
    # never a normalized CREATE TABLE ... AS SELECT.
    assert backend.calls == [
        "SELECT * FROM (SELECT * INTO new_t FROM orders) AS marivo_raw_sql LIMIT 101"
    ]


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


def test_raw_sql_exact_limit_reports_not_truncated(tmp_path: Path) -> None:
    _register_raw_sql_fixture(tmp_path)
    result = md.raw_sql(
        ms.ref.datasource("warehouse"),
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
        ms.ref.datasource("warehouse"),
        "SELECT id FROM orders ORDER BY id",
        limit=1,
        reason="truncation check",
        project_root=tmp_path,
    )
    assert result.returned_row_count == 1
    assert result.is_truncated is True


def test_raw_sql_order_by_limit_returns_true_top_n(tmp_path: Path) -> None:
    """ORDER BY + truncation must return the true Top-N rows, not an arbitrary subset.

    Documents the end-to-end Top-N contract on DuckDB: ``ORDER BY delta DESC`` with
    ``limit=3`` over five rows must yield ids [3, 5, 1] (deltas 30, 20, 10) and
    still report truncation because five rows exceed the requested three.
    """
    _register_raw_sql_ranking_fixture(tmp_path)
    result = md.raw_sql(
        ms.ref.datasource("warehouse"),
        "SELECT id, delta FROM events ORDER BY delta DESC",
        limit=3,
        reason="top deltas",
        project_root=tmp_path,
    )
    assert [row["id"] for row in result.rows] == [3, 5, 1]
    assert result.is_truncated is True


def test_raw_sql_truncated_result_injects_truncation_warning(tmp_path: Path) -> None:
    _register_raw_sql_fixture(tmp_path)
    result = md.raw_sql(
        ms.ref.datasource("warehouse"),
        "SELECT id FROM orders ORDER BY id",
        limit=1,
        reason="truncation warning check",
        project_root=tmp_path,
    )
    assert result.is_truncated is True
    truncation_warnings = [w for w in result.warnings if "truncated" in w.lower()]
    assert truncation_warnings
    assert "is_truncated" in truncation_warnings[0]


def test_raw_sql_untruncated_result_has_no_truncation_warning(tmp_path: Path) -> None:
    _register_raw_sql_fixture(tmp_path)
    result = md.raw_sql(
        ms.ref.datasource("warehouse"),
        "SELECT id FROM orders ORDER BY id",
        limit=2,
        reason="no truncation warning check",
        project_root=tmp_path,
    )
    assert result.is_truncated is False
    assert not any("truncated" in w.lower() for w in result.warnings)


def test_raw_sql_truncated_result_render_flags_truncation_prominently(tmp_path: Path) -> None:
    _register_raw_sql_fixture(tmp_path)
    result = md.raw_sql(
        ms.ref.datasource("warehouse"),
        "SELECT id FROM orders ORDER BY id",
        limit=1,
        reason="prominent truncation check",
        project_root=tmp_path,
    )
    rendered = result.render()
    assert "TRUNCATED" in rendered
    assert "is_truncated" in rendered


def test_raw_sql_default_limit_is_100(tmp_path: Path) -> None:
    from marivo.datasource.manage import RAW_SQL_DEFAULT_LIMIT

    assert RAW_SQL_DEFAULT_LIMIT == 100
    _register_raw_sql_fixture(tmp_path)
    result = md.raw_sql(
        ms.ref.datasource("warehouse"),
        "SELECT id FROM orders ORDER BY id",
        reason="default limit check",
        project_root=tmp_path,
    )
    assert result.requested_limit == RAW_SQL_DEFAULT_LIMIT

    # The explicit limit argument still overrides the default.
    explicit = md.raw_sql(
        ms.ref.datasource("warehouse"),
        "SELECT id FROM orders ORDER BY id",
        reason="explicit limit override",
        limit=5,
        project_root=tmp_path,
    )
    assert explicit.requested_limit == 5


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
        ms.ref.datasource("warehouse"),
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
        datasource=ms.ref.datasource("wh"),
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


def test_raw_sql_result_rejects_returned_row_count_drift() -> None:
    from marivo.datasource.manage import RawSqlResult

    with pytest.raises(ValueError, match="returned_row_count must equal"):
        RawSqlResult(
            datasource=ms.ref.datasource("wh"),
            backend_type="duckdb",
            sql="SELECT ok",
            reason="validate bounded result count",
            columns=("ok",),
            types={"ok": "int64"},
            rows=({"ok": 1},),
            requested_limit=10,
            returned_row_count=2,
            is_truncated=False,
            timeout_seconds=30,
            duration_ms=5,
            warnings=(),
        )


def test_raw_sql_terminal_facts_render_in_contract_order(tmp_path: Path) -> None:
    _register_raw_sql_fixture(tmp_path)
    result = md.raw_sql(
        ms.ref.datasource("warehouse"),
        "SELECT id, amount FROM orders ORDER BY id",
        limit=1,
        reason="inspect terminal result facts",
        project_root=tmp_path,
    )

    rendered = result.render()
    labels = (
        "terminal_only:",
        "typed_reentry:",
        "row_count_semantics:",
        "returned_row_count:",
        "requested_limit:",
        "is_truncated:",
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
