"""SQLite datasource integration contracts."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

import marivo.datasource as md
from marivo.datasource import backends, manage, store
from marivo.datasource.errors import DatasourceRawSqlError
from marivo.datasource.metadata import inspect_table


def _seed_sqlite(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE orders (
                order_id INTEGER PRIMARY KEY,
                code TEXT UNIQUE,
                amount REAL NOT NULL,
                created_at TIMESTAMP NOT NULL
            );
            INSERT INTO orders VALUES
                (1, 'a', 10.0, '2026-07-01 10:00:00'),
                (2, 'b', 20.0, '2026-07-02 11:00:00');
            CREATE TABLE composite_keys (
                account_id TEXT,
                region TEXT,
                PRIMARY KEY (account_id, region),
                UNIQUE (region)
            );
            INSERT INTO composite_keys VALUES (NULL, 'east');
            CREATE TABLE descending_integer_key (
                id INTEGER PRIMARY KEY DESC
            );
            INSERT INTO descending_integer_key VALUES (NULL);
            CREATE TABLE strict_keys (
                account_id TEXT,
                region TEXT,
                PRIMARY KEY (account_id, region)
            ) STRICT;
            CREATE TABLE without_rowid_keys (
                account_id TEXT,
                region TEXT,
                PRIMARY KEY (account_id, region)
            ) WITHOUT ROWID;
            CREATE VIEW order_totals AS SELECT order_id, amount FROM orders;
            """
        )
        connection.commit()
    finally:
        connection.close()


def test_sqlite_register_inspect_sample_and_raw_sql(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    database_path = tmp_path / "app.sqlite"
    _seed_sqlite(database_path)
    spec = md.sqlite(
        name="app",
        path=str(database_path),
        type_map={"money": "float64"},
    )

    summary = md.register(spec)
    description = md.describe("app")
    connection_test = md.test(spec.ref)
    inspection = md.inspect(spec.ref, md.table("orders"))
    snapshot = inspection.sample(
        scope=md.unpruned(max_rows=10, timeout_seconds=5),
        columns=("order_id", "amount", "created_at"),
    )
    raw_result = md.raw_sql(
        spec.ref,
        "SELECT order_id, amount FROM orders ORDER BY order_id",
        reason="verify SQLite row access",
        limit=10,
        project_root=tmp_path,
    )

    assert summary.backend_type == "sqlite"
    assert description.literal_fields == {
        "path": str(database_path),
        "read_only": False,
        "type_map": {"money": "float64"},
    }
    assert connection_test.ok is True
    assert [column.name for column in inspection.schema] == [
        "order_id",
        "code",
        "amount",
        "created_at",
    ]
    assert inspection.partitioning.state == "none"
    assert inspection.physical_extent.row_count is None
    assert inspection.physical_extent.size_bytes is None
    assert inspection.execution_capabilities.timeout_enforced is True
    assert snapshot.coverage.retained_row_count == 2
    assert raw_result.rows == (
        {"order_id": 1, "amount": 10.0},
        {"order_id": 2, "amount": 20.0},
    )


def test_sqlite_metadata_exposes_keys_and_view_definition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    database_path = tmp_path / "app.sqlite"
    _seed_sqlite(database_path)
    md.register(md.sqlite(name="app", path=str(database_path)))

    table_metadata = inspect_table("app", table="orders", project_root=tmp_path)
    view_metadata = inspect_table(
        "app",
        table="order_totals",
        database="main",
        project_root=tmp_path,
    )
    composite_metadata = inspect_table(
        "app",
        table="composite_keys",
        project_root=tmp_path,
    )
    descending_metadata = inspect_table(
        "app",
        table="descending_integer_key",
        project_root=tmp_path,
    )
    strict_metadata = inspect_table(
        "app",
        table="strict_keys",
        project_root=tmp_path,
    )
    without_rowid_metadata = inspect_table(
        "app",
        table="without_rowid_keys",
        project_root=tmp_path,
    )

    assert tuple(column.name for column in table_metadata.columns) == (
        "order_id",
        "code",
        "amount",
        "created_at",
    )
    assert table_metadata.primary_keys == ("order_id",)
    assert [constraint.columns for constraint in table_metadata.unique_constraints] == [("code",)]
    assert table_metadata.partition_state == "none"
    assert table_metadata.physical_profile is None
    assert view_metadata.is_view is True
    assert view_metadata.view_definition is not None
    assert "CREATE VIEW order_totals" in view_metadata.view_definition
    assert composite_metadata.primary_keys == ("account_id", "region")
    assert {column.name: column.nullable for column in composite_metadata.columns} == {
        "account_id": True,
        "region": True,
    }
    assert [constraint.columns for constraint in composite_metadata.unique_constraints] == [
        ("region",)
    ]
    assert descending_metadata.columns[0].nullable is True
    assert strict_metadata.primary_keys == ("account_id", "region")
    assert all(column.nullable is False for column in strict_metadata.columns)
    assert without_rowid_metadata.primary_keys == ("account_id", "region")
    assert all(column.nullable is False for column in without_rowid_metadata.columns)


def test_sqlite_raw_sql_timeout_remains_armed_during_cursor_fetch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    database_path = tmp_path / "app.sqlite"
    _seed_sqlite(database_path)
    spec = md.sqlite(name="app", path=str(database_path))
    md.register(spec)
    datasource_ir = store.load_one("app", project_root=tmp_path)
    assert datasource_ir is not None
    backend = backends.build_backend(datasource_ir)

    class FetchTimer:
        def __init__(self, seconds: int, interrupt: Callable[[], None]) -> None:
            assert seconds == 1
            self.interrupt = interrupt
            self.armed = False

        def start(self) -> None:
            self.armed = True

        def cancel(self) -> None:
            self.armed = False

    timers: list[FetchTimer] = []
    fetch_armed: list[bool] = []

    def timer(seconds: int, interrupt: Callable[[], None]) -> FetchTimer:
        result = FetchTimer(seconds, interrupt)
        timers.append(result)
        return result

    def pause(seconds: float) -> float:
        # SQLite evaluates the second row during fetch, after execute returns.
        if seconds:
            armed = len(timers) == 1 and timers[0].armed
            fetch_armed.append(armed)
            if armed:
                timers[0].interrupt()
        return seconds

    backend.con.create_function("pause", 1, pause)
    backend.raw_sql("CREATE TABLE delays (seconds REAL)")
    backend.raw_sql("INSERT INTO delays VALUES (0), (0.05)")

    class _Service:
        @contextmanager
        def use_backend(
            self, _name: str, *, read_only: bool, terminal_timeout_seconds: int | None = None
        ) -> Iterator[Any]:
            assert read_only is True
            yield backend

    monkeypatch.setattr("marivo.datasource.engines.sqlite.Timer", timer)
    monkeypatch.setattr(
        "marivo.datasource.manage.DatasourceConnectionService",
        lambda _root: _Service(),
    )

    try:
        with pytest.raises(DatasourceRawSqlError, match="result fetching failed"):
            md.raw_sql(
                spec.ref,
                "SELECT pause(seconds) AS waited FROM delays",
                reason="verify SQLite fetch timeout",
                timeout_seconds=1,
                project_root=tmp_path,
            )
        assert fetch_armed == [True]
        assert len(timers) == 1 and not timers[0].armed
    finally:
        backend.disconnect()


def test_sqlite_explicit_and_internal_read_only_connections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    database_path = tmp_path / "app.sqlite"
    _seed_sqlite(database_path)
    md.register(md.sqlite(name="app", path=str(database_path), read_only=True))

    with (
        manage._connect("app") as connection,
        pytest.raises(sqlite3.DatabaseError, match="not authorized"),
    ):
        connection.raw_sql("INSERT INTO orders VALUES (3, 'c', 30.0, CURRENT_TIMESTAMP)")

    md.remove("app")
    md.register(md.sqlite(name="app", path=str(database_path)))
    datasource_ir = store.load_one("app", project_root=tmp_path)
    assert datasource_ir is not None
    backend = backends.build_backend(datasource_ir, read_only=True)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            backend.raw_sql("DELETE FROM orders")
    finally:
        backend.disconnect()


def test_sqlite_profile_passes_strptime_through_and_lacks_quantile() -> None:
    """SQLite parses text with its own connection-local scalar, which consumes
    the Python format directly, so the profile contributes no translation."""
    from marivo.datasource.engines import require_profile_for_backend_type

    profile = require_profile_for_backend_type("sqlite")

    assert profile.quantile is None
    assert profile.translate_strptime_format("%Y-%m-%d %H:%M:%S") == "%Y-%m-%d %H:%M:%S"
    assert profile.translate_strptime_format("%M") == "%M"


def test_owned_staging_never_allows_persistent_or_foreign_temp_writes(tmp_path: Path) -> None:
    from marivo.datasource.engines.sqlite import connect, owned_temporary_writes

    database = tmp_path / "stage.sqlite"
    _seed_sqlite(database)
    backend = connect("sqlite", {"path": str(database), "read_only": True})
    name = "mv_graph_" + "a" * 32
    try:
        with owned_temporary_writes(backend, frozenset({name})):
            backend.create_table(name, schema={"value": "int64"}, temp=True)
            with pytest.raises(sqlite3.DatabaseError):
                backend.create_table("persistent", schema={"value": "int64"})
            with pytest.raises(sqlite3.DatabaseError):
                backend.create_table("foreign", schema={"value": "int64"}, temp=True)
            backend.drop_table(name, database="temp")
        with pytest.raises(sqlite3.DatabaseError):
            backend.create_table(name, schema={"value": "int64"}, temp=True)
        with (
            pytest.raises(RuntimeError, match="abort"),
            owned_temporary_writes(backend, frozenset({name})),
        ):
            raise RuntimeError("abort")
        with pytest.raises(sqlite3.DatabaseError):
            backend.create_table(name, schema={"value": "int64"}, temp=True)
    finally:
        backend.disconnect()
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM orders").fetchone() == (2,)
