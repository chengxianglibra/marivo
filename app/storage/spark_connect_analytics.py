"""Spark Connect (gRPC) analytics engine adapter.

Uses ``pyspark[connect]`` to talk to a remote Spark 3.4+ cluster via
the Spark Connect protocol.  No embedded SparkSession is created.
"""

from __future__ import annotations

from typing import Any

from app.storage.analytics import AnalyticsEngine


class SparkConnectAnalyticsEngine(AnalyticsEngine):
    """Lightweight gRPC client for Spark Connect."""

    def __init__(self, remote: str, **kwargs: Any) -> None:
        self.remote = remote
        self.extra_config = kwargs
        self._spark = None

    def _connect(self):  # noqa: ANN202
        from pyspark.sql import SparkSession

        if self._spark is None:
            builder = SparkSession.builder.remote(self.remote)
            for k, v in self.extra_config.items():
                builder = builder.config(k, v)
            self._spark = builder.getOrCreate()
        return self._spark

    def initialize(self) -> None:
        spark = self._connect()
        spark.sql("SELECT 1").collect()

    def query_rows(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        spark = self._connect()
        if params:
            # Spark SQL doesn't support ``?`` placeholders — format directly.
            sql = self._interpolate(sql, params)
        df = spark.sql(sql)
        columns = df.columns
        return [dict(zip(columns, row)) for row in df.collect()]

    def table_exists(self, table_name: str) -> bool:
        spark = self._connect()
        return spark.catalog.tableExists(table_name)

    def table_row_count(self, table_name: str) -> int:
        rows = self.query_rows(f"SELECT COUNT(*) AS cnt FROM {table_name}")
        return rows[0]["cnt"]

    # ------------------------------------------------------------------
    @staticmethod
    def _interpolate(sql: str, params: list[Any]) -> str:
        """Positional ``?`` placeholder interpolation for Spark SQL."""
        parts = sql.split("?")
        if len(parts) - 1 != len(params):
            raise ValueError(
                f"Parameter count mismatch: SQL has {len(parts) - 1} placeholders "
                f"but {len(params)} params given"
            )
        result: list[str] = [parts[0]]
        for part, param in zip(parts[1:], params):
            if isinstance(param, str):
                escaped = param.replace("'", "''")
                result.append(f"'{escaped}'")
            elif param is None:
                result.append("NULL")
            else:
                result.append(str(param))
            result.append(part)
        return "".join(result)
