from __future__ import annotations

import unittest
from typing import Any

from marivo.adapters.schema import expected_metadata_tables, metadata_schema_marker_row
from marivo.adapters.server.mysql_metadata import (
    MySQLMetadataStore,
    _expected_mysql_foreign_key_names,
    _expected_mysql_index_names,
)


class FakeCursor:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.rows: list[dict[str, Any]] = []
        self.description: list[tuple[str]] | None = None
        self.rowcount = 0
        self.closed = False

    def execute(self, sql: str, params: list[Any]) -> None:
        self.connection.executed.append((sql, params))
        if self.connection.fail_on and self.connection.fail_on in sql:
            raise RuntimeError(self.connection.failure_message)
        if "VERSION()" in sql:
            self.rows = [{"version": self.connection.version, "charset": self.connection.charset}]
        elif "information_schema.TABLES" in sql:
            self.rows = [{"name": name} for name in sorted(self.connection.table_names)]
        elif "information_schema.STATISTICS" in sql:
            self.rows = [{"name": name} for name in sorted(self.connection.index_names)]
        elif "information_schema.REFERENTIAL_CONSTRAINTS" in sql:
            self.rows = [{"name": name} for name in sorted(self.connection.foreign_key_names)]
        elif "FROM metadata_schema_marker" in sql:
            self.rows = [self.connection.marker] if self.connection.marker is not None else []
        elif "FROM sessions" in sql:
            self.rows = [] if params == ["missing"] else list(self.connection.session_rows)
        else:
            self.rows = []
        self.description = [(column,) for row in self.rows[:1] for column in row]
        self.rowcount = len(self.rows)

    def executemany(self, sql: str, rows: list[tuple[Any, ...]]) -> None:
        self.connection.executed.append((sql, list(rows)))
        self.rowcount = len(rows)

    def fetchall(self) -> list[dict[str, Any]]:
        return self.rows

    def fetchone(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(self) -> None:
        self.executed: list[tuple[str, Any]] = []
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0
        self.version = "8.0.36"
        self.charset = "utf8mb4"
        self.table_names = expected_metadata_tables("mysql")
        self.index_names = _expected_mysql_index_names()
        self.foreign_key_names = _expected_mysql_foreign_key_names()
        self.marker: dict[str, Any] | None = metadata_schema_marker_row("mysql")
        self.session_rows: list[dict[str, Any]] = []
        self.fail_on: str | None = None
        self.fail_rollback = False
        self.failure_message = "insert failed"

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1
        if self.fail_rollback:
            raise RuntimeError("rollback failed")

    def close(self) -> None:
        self.closes += 1


def _store(connection: FakeConnection) -> MySQLMetadataStore:
    return MySQLMetadataStore(
        host="db.example",
        database="marivo",
        user="marivo",
        password="secret",
        pool_size=1,
        connection_factory=lambda **_: connection,
    )


class MySQLMetadataStoreTests(unittest.TestCase):
    def test_query_rows_and_query_one_return_dict_shape(self) -> None:
        connection = FakeConnection()
        connection.session_rows = [{"session_id": "s1", "goal": "g1"}]
        store = _store(connection)

        rows = store.query_rows("SELECT * FROM sessions WHERE session_id = ?", ["s1"])

        self.assertEqual(rows, [{"session_id": "s1", "goal": "g1"}])
        self.assertEqual(
            store.query_one("SELECT * FROM sessions WHERE session_id = ?", ["s1"]),
            rows[0],
        )
        self.assertIsNone(
            store.query_one("SELECT * FROM sessions WHERE session_id = ?", ["missing"])
        )
        self.assertIn("%s", connection.executed[0][0])
        self.assertNotIn("?", connection.executed[0][0])

    def test_transaction_query_rows_returns_dict_cursor_values(self) -> None:
        connection = FakeConnection()
        connection.session_rows = [{"session_id": "s1", "goal": "g1"}]
        store = _store(connection)

        with store.transaction() as txn:
            rows = txn.query_rows("SELECT * FROM sessions WHERE session_id = ?", ["s1"])

        self.assertEqual(rows, [{"session_id": "s1", "goal": "g1"}])
        self.assertNotEqual(rows, [{"session_id": "session_id", "goal": "goal"}])

    def test_transaction_rolls_back_connection_on_checkout_before_reuse(self) -> None:
        connection = FakeConnection()
        store = _store(connection)

        with store.connect() as con:
            store.execute_sql(con, "SELECT * FROM sessions WHERE session_id = ?", ["missing"])

        self.assertEqual(connection.rollbacks, 0)

        with store.transaction() as txn:
            txn.execute("INSERT INTO sessions (session_id) VALUES (?)", ["s1"])

        self.assertEqual(connection.rollbacks, 1)
        self.assertEqual(connection.commits, 1)

    def test_transaction_discards_connection_when_checkout_rollback_fails(self) -> None:
        connection = FakeConnection()
        connection.fail_rollback = True
        store = _store(connection)

        with self.assertRaisesRegex(RuntimeError, "Failed to reset"), store.transaction():
            self.fail("transaction should not yield after rollback reset failure")

        self.assertEqual(connection.rollbacks, 1)
        self.assertEqual(connection.closes, 1)
        self.assertEqual(store._created_connections, 0)

    def test_initialize_accepts_current_schema(self) -> None:
        connection = FakeConnection()
        store = _store(connection)

        store.initialize()

        self.assertEqual(connection.commits, 1)
        executed_sql = "\n".join(sql for sql, _params in connection.executed)
        self.assertIn("information_schema.TABLES", executed_sql)
        self.assertNotIn("CREATE TABLE", executed_sql)

    def test_initialize_fails_closed_for_unknown_schema(self) -> None:
        connection = FakeConnection()
        connection.table_names = {"customer_table"}
        connection.marker = None
        store = _store(connection)

        with self.assertRaisesRegex(RuntimeError, "unknown tables"):
            store.initialize()

        self.assertEqual(connection.rollbacks, 1)

    def test_initialize_fails_closed_when_current_schema_lacks_index(self) -> None:
        connection = FakeConnection()
        connection.index_names = set()
        store = _store(connection)

        with self.assertRaisesRegex(RuntimeError, "missing indexes"):
            store.initialize()

        self.assertEqual(connection.rollbacks, 1)

    def test_initialize_fails_closed_when_current_schema_lacks_foreign_key(self) -> None:
        connection = FakeConnection()
        connection.foreign_key_names = set()
        store = _store(connection)

        with self.assertRaisesRegex(RuntimeError, "missing foreign keys"):
            store.initialize()

        self.assertEqual(connection.rollbacks, 1)

    def test_connect_rolls_back_on_exception_and_reuses_connection(self) -> None:
        connection = FakeConnection()
        store = _store(connection)

        with self.assertRaisesRegex(RuntimeError, "insert failed"), store.connect() as con:
            store.execute_sql(con, "INSERT INTO artifacts (artifact_id) VALUES (?)", ["a1"])
            connection.fail_on = "findings"
            store.execute_sql(con, "INSERT INTO findings (finding_id) VALUES (?)", ["f1"])

        self.assertEqual(connection.rollbacks, 1)
        store.execute("INSERT INTO sessions (session_id) VALUES (?)", ["s1"])
        self.assertEqual(connection.commits, 1)

    def test_connection_failure_error_is_redacted(self) -> None:
        def fail_connect(**_: Any) -> Any:
            raise RuntimeError("mysql://marivo:secret@db.example/marivo password=secret")

        store = MySQLMetadataStore(
            host="db.example",
            database="marivo",
            user="marivo",
            password="secret",
            connection_factory=fail_connect,
        )

        with self.assertRaisesRegex(RuntimeError, r"password=\*\*\*") as ctx:
            store.query_one("SELECT 1")

        self.assertNotIn("secret", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
