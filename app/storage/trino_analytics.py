from __future__ import annotations

from typing import Any

from app.storage.analytics import AnalyticsEngine


class TrinoAnalyticsEngine(AnalyticsEngine):
    """Trino-backed analytics engine using the trino-python-client."""

    def __init__(
        self,
        host: str,
        port: int = 8080,
        user: str = "omnidb",
        catalog: str = "hive",
        schema: str = "default",
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.catalog = catalog
        self.schema = schema

    def _connect(self):  # noqa: ANN202
        from trino.dbapi import connect

        return connect(
            host=self.host,
            port=self.port,
            user=self.user,
            catalog=self.catalog,
            schema=self.schema,
        )

    def initialize(self) -> None:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
        finally:
            conn.close()

    def query_rows(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            columns = [col[0] for col in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
        finally:
            conn.close()

    def table_exists(self, table_name: str) -> bool:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_catalog = ? AND table_schema = ? AND table_name = ?",
                [self.catalog, self.schema, table_name],
            )
            row = cur.fetchone()
            return row[0] > 0
        finally:
            conn.close()

    def table_row_count(self, table_name: str) -> int:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(*) FROM {table_name}")
            return cur.fetchone()[0]
        finally:
            conn.close()
