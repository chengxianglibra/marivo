from __future__ import annotations

import queue
import re
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from importlib import import_module
from typing import Any

from marivo.adapters.dialect import MYSQL_METADATA_DIALECT, MetadataDialect
from marivo.adapters.metadata import MetadataStore
from marivo.adapters.schema import (
    evaluate_metadata_schema_state,
    metadata_ddl_for_backend,
    metadata_schema_marker_row,
)
from marivo.redaction import redact_sensitive_text

ConnectionFactory = Callable[..., Any]


class MySQLMetadataStore(MetadataStore):
    """MySQL-backed metadata store for shared production metadata."""

    dialect: MetadataDialect = MYSQL_METADATA_DIALECT

    def __init__(
        self,
        *,
        host: str,
        database: str,
        user: str,
        password: str | None = None,
        port: int = 3306,
        connect_timeout: int = 10,
        pool_size: int = 5,
        ssl: dict[str, Any] | bool | None = None,
        dsn: str | None = None,
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
        if connect_timeout <= 0:
            raise ValueError("connect_timeout must be positive")
        if pool_size <= 0:
            raise ValueError("pool_size must be positive")
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.connect_timeout = connect_timeout
        self.pool_size = pool_size
        self.ssl = ssl
        self.dsn = dsn
        self._connection_factory = connection_factory
        self._pool: queue.LifoQueue[Any] = queue.LifoQueue(maxsize=pool_size)
        self._pool_lock = threading.Lock()
        self._created_connections = 0

    def initialize(self) -> None:
        with self.connect() as con:
            self._assert_mysql_environment(con)
            table_names = self._table_names(con)
            marker_row = self._marker_row(con) if "metadata_schema_marker" in table_names else None
            state = evaluate_metadata_schema_state("mysql", table_names, marker_row)
            if state.state == "current":
                self._assert_current_schema_shape(con)
                con.commit()
                return
            if state.state != "empty":
                raise RuntimeError(f"MySQL metadata schema preflight failed: {state.reason}")

            for ddl in metadata_ddl_for_backend("mysql"):
                self.execute_sql(con, ddl)
            marker = metadata_schema_marker_row("mysql")
            self.execute_sql(
                con,
                self.dialect.insert_ignore_sql(
                    "metadata_schema_marker",
                    ["backend", "schema_version", "ddl_fingerprint"],
                ),
                [marker["backend"], marker["schema_version"], marker["ddl_fingerprint"]],
            )
            con.commit()

    @contextmanager
    def connect(self) -> Iterator[Any]:
        con = self._acquire_connection()
        reusable = True
        try:
            yield con
        except Exception:
            reusable = self._rollback_quietly(con)
            raise
        finally:
            if reusable:
                self._release_connection(con)
            else:
                with self._pool_lock:
                    self._created_connections -= 1

    def execute(self, sql: str, params: list[Any] | None = None) -> None:
        with self.connect() as con:
            self.execute_sql(con, sql, params)
            con.commit()

    def execute_many(self, sql: str, rows: list[tuple[Any, ...]]) -> None:
        with self.connect() as con:
            cursor = con.cursor()
            try:
                cursor.executemany(self.dialect.compile_sql(sql), rows)
            finally:
                cursor.close()
            con.commit()

    def query_rows(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        with self.connect() as con:
            cursor = self.execute_sql(con, sql, params)
            rows = cursor.fetchall()
            cursor.close()
            return [dict(row) for row in rows]

    def query_one(self, sql: str, params: list[Any] | None = None) -> dict[str, Any] | None:
        rows = self.query_rows(sql, params)
        return rows[0] if rows else None

    def execute_sql(self, con: Any, sql: str, params: list[Any] | None = None) -> Any:
        cursor = con.cursor()
        try:
            cursor.execute(self.dialect.compile_sql(sql), params or [])
        except Exception:
            cursor.close()
            raise
        return cursor

    def _connection_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "user": self.user,
            "password": self.password,
            "connect_timeout": self.connect_timeout,
            "charset": "utf8mb4",
            "autocommit": False,
        }
        if self.ssl:
            kwargs["ssl"] = {} if self.ssl is True else self.ssl
        return kwargs

    def _new_connection(self) -> Any:
        factory = self._connection_factory or _default_connection_factory()
        try:
            return factory(**self._connection_kwargs())
        except Exception as exc:
            detail = redact_sensitive_text(exc)
            raise RuntimeError(f"Failed to connect MySQL metadata store: {detail}") from exc

    def _acquire_connection(self) -> Any:
        try:
            return self._pool.get_nowait()
        except queue.Empty:
            pass

        with self._pool_lock:
            if self._created_connections < self.pool_size:
                self._created_connections += 1
                try:
                    return self._new_connection()
                except Exception:
                    self._created_connections -= 1
                    raise

        return self._pool.get()

    def _release_connection(self, con: Any) -> None:
        try:
            self._pool.put_nowait(con)
        except queue.Full:
            self._close_quietly(con)
            with self._pool_lock:
                self._created_connections -= 1

    def _assert_mysql_environment(self, con: Any) -> None:
        row = self._select_one(
            con,
            "SELECT VERSION() AS version, @@character_set_database AS charset",
        )
        if row is None:
            raise RuntimeError("MySQL metadata environment preflight returned no result")
        version = str(row.get("version") or "")
        charset = str(row.get("charset") or "")
        if not _mysql_version_at_least(version, 8, 0, 16):
            raise RuntimeError(f"MySQL metadata requires MySQL 8.0.16+, got {version}")
        if charset and not charset.lower().startswith("utf8mb4"):
            raise RuntimeError(f"MySQL metadata database charset must be utf8mb4, got {charset}")

    def _table_names(self, con: Any) -> set[str]:
        cursor = self.execute_sql(
            con,
            """
            SELECT TABLE_NAME AS name
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE()
            """,
        )
        try:
            return {str(row["name"]) for row in cursor.fetchall()}
        finally:
            cursor.close()

    def _marker_row(self, con: Any) -> dict[str, Any] | None:
        return self._select_one(
            con,
            """
            SELECT backend, schema_version, ddl_fingerprint
            FROM metadata_schema_marker
            WHERE backend = ?
            """,
            ["mysql"],
        )

    def _assert_current_schema_shape(self, con: Any) -> None:
        actual_indexes = self._index_names(con)
        missing_indexes = _expected_mysql_index_names() - actual_indexes
        if missing_indexes:
            raise RuntimeError(
                "MySQL metadata schema preflight failed: missing indexes: "
                + ", ".join(sorted(missing_indexes))
            )

        actual_foreign_keys = self._foreign_key_names(con)
        missing_foreign_keys = _expected_mysql_foreign_key_names() - actual_foreign_keys
        if missing_foreign_keys:
            raise RuntimeError(
                "MySQL metadata schema preflight failed: missing foreign keys: "
                + ", ".join(sorted(missing_foreign_keys))
            )

    def _index_names(self, con: Any) -> set[str]:
        cursor = self.execute_sql(
            con,
            """
            SELECT DISTINCT INDEX_NAME AS name
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
            """,
        )
        try:
            return {str(row["name"]) for row in cursor.fetchall()}
        finally:
            cursor.close()

    def _foreign_key_names(self, con: Any) -> set[str]:
        cursor = self.execute_sql(
            con,
            """
            SELECT CONSTRAINT_NAME AS name
            FROM information_schema.REFERENTIAL_CONSTRAINTS
            WHERE CONSTRAINT_SCHEMA = DATABASE()
            """,
        )
        try:
            return {str(row["name"]) for row in cursor.fetchall()}
        finally:
            cursor.close()

    def _select_one(
        self, con: Any, sql: str, params: list[Any] | None = None
    ) -> dict[str, Any] | None:
        cursor = self.execute_sql(con, sql, params)
        try:
            row = cursor.fetchone()
            return dict(row) if row is not None else None
        finally:
            cursor.close()

    def _rollback_quietly(self, con: Any) -> bool:
        try:
            con.rollback()
            return True
        except Exception:
            self._close_quietly(con)
            return False

    def _close_quietly(self, con: Any) -> None:
        with suppress(Exception):
            con.close()


def _default_connection_factory() -> ConnectionFactory:
    try:
        pymysql = import_module("pymysql")
        cursors = import_module("pymysql.cursors")
    except ImportError as exc:
        raise RuntimeError(
            "MySQL metadata requires the optional PyMySQL dependency. "
            "Install Marivo with the mysql extra."
        ) from exc
    return lambda **kwargs: pymysql.connect(cursorclass=cursors.DictCursor, **kwargs)


def _expected_mysql_index_names() -> set[str]:
    return {
        match.group(1)
        for statement in metadata_ddl_for_backend("mysql")
        if (
            match := re.match(
                r"\s*CREATE (?:UNIQUE )?INDEX\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+ON\b",
                statement,
            )
        )
        is not None
    }


def _expected_mysql_foreign_key_names() -> set[str]:
    return {
        match.group(1)
        for statement in metadata_ddl_for_backend("mysql")
        if (
            match := re.match(
                r"\s*ALTER TABLE\s+\w+\s+ADD CONSTRAINT\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+FOREIGN KEY\b",
                statement,
            )
        )
        is not None
    }


def _mysql_version_at_least(version: str, major: int, minor: int, patch: int) -> bool:
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", version)
    if match is None:
        return False
    actual = tuple(int(part) for part in match.groups())
    return actual >= (major, minor, patch)
