"""Spark Thrift / Kyuubi analytics engine adapter.

Uses ``pyhive.hive`` (HiveServer2 / Thrift protocol) to connect to
Kyuubi, Spark Thrift Server, or any HiveServer2-compatible endpoint.
"""

from __future__ import annotations

from typing import Any

from app.storage.analytics import AnalyticsEngine


class SparkThriftAnalyticsEngine(AnalyticsEngine):
    """HiveServer2/Thrift client for Kyuubi or Spark Thrift Server."""

    def __init__(
        self,
        host: str,
        port: int = 10009,
        username: str = "factum",
        database: str = "default",
        auth: str = "NOSASL",
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.database = database
        self.auth = auth

    def _connect(self):  # noqa: ANN202
        from pyhive import hive

        return hive.connect(
            host=self.host,
            port=self.port,
            username=self.username,
            database=self.database,
            auth=self.auth,
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
            cur.execute(f"SHOW TABLES LIKE '{table_name}'")
            rows = cur.fetchall()
            return len(rows) > 0
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
