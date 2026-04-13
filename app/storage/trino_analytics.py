from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.storage.analytics import AnalyticsEngine


class TrinoAnalyticsEngine(AnalyticsEngine):
    """Trino-backed analytics engine using the trino-python-client."""

    def __init__(
        self,
        host: str,
        port: int = 8080,
        user: str = "factum",
        password: str | None = None,
        http_scheme: str = "http",
        catalog: str = "hive",
        schema: str = "default",
        client_tags: list[str] | None = None,
        source: str | None = None,
        http_headers: dict[str, str] | None = None,
        request_timeout: float = 600.0,
        legacy_prepared_statements: bool | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.http_scheme = http_scheme
        self.catalog = catalog
        self.schema = schema
        self.client_tags = client_tags
        self.source = source
        self.http_headers = http_headers
        self.request_timeout = request_timeout
        self.legacy_prepared_statements = legacy_prepared_statements

    def _connect(self) -> Any:
        from trino.dbapi import connect

        connect_fn: Callable[..., Any] = connect
        _reserved_prefixes = ("x-trino-",)
        safe_headers: dict[str, str] | None = None
        if self.http_headers:
            safe_headers = {
                k: v
                for k, v in self.http_headers.items()
                if not k.lower().startswith(_reserved_prefixes)
            } or None

        kwargs: dict[str, Any] = {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "http_scheme": self.http_scheme,
            "catalog": self.catalog,
            "schema": self.schema,
            "request_timeout": self.request_timeout,
        }
        if self.password is not None:
            from trino.auth import BasicAuthentication

            kwargs["auth"] = BasicAuthentication(self.user, self.password)
        if self.client_tags is not None:
            kwargs["client_tags"] = self.client_tags
        if self.source is not None:
            kwargs["source"] = self.source
        if safe_headers is not None:
            kwargs["http_headers"] = safe_headers
        if self.legacy_prepared_statements is not None:
            kwargs["legacy_prepared_statements"] = self.legacy_prepared_statements
        return connect_fn(**kwargs)

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
            return [dict(zip(columns, row, strict=False)) for row in cur.fetchall()]
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
            return bool(row) and row[0] > 0
        finally:
            conn.close()

    def table_row_count(self, table_name: str) -> int:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(*) FROM {table_name}")
            _row = cur.fetchone()
            return int(_row[0]) if _row else 0
        finally:
            conn.close()
