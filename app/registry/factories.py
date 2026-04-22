from __future__ import annotations

from typing import Any

from app.adapters.base import CatalogAdapter
from app.storage.analytics import AnalyticsEngine

SUPPORTED_SOURCE_TYPES: tuple[str, ...] = ("duckdb", "trino")
SUPPORTED_ENGINE_TYPES: tuple[str, ...] = ("duckdb", "trino")


def _duckdb_path(connection: dict[str, Any]) -> str:
    for key in ("path", "database", "db_path"):
        value = connection.get(key)
        if isinstance(value, str) and value:
            return value
    raise KeyError("DuckDB connection requires one of: path, database, db_path")


def _trino_connect_kwargs(connection: dict[str, Any]) -> dict[str, Any]:
    """Extract Trino connection kwargs shared by catalog adapter and analytics engine."""
    raw_tags = connection.get("client_tags") or connection.get("client-tags")
    if isinstance(raw_tags, str):
        raw_tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
    raw_headers = connection.get("http_headers") or connection.get("http-headers")
    if isinstance(raw_headers, str):
        import json

        raw_headers = json.loads(raw_headers)
    kwargs: dict[str, Any] = {
        "host": connection["host"],
        "port": connection.get("port", 8080),
        "user": connection.get("user", "marivo"),
        "password": connection.get("password"),
        "http_scheme": connection.get("http_scheme") or connection.get("http-scheme", "http"),
        "catalog": connection.get("catalog", "hive"),
        "schema": connection.get("schema", "default"),
        "client_tags": raw_tags,
        "source": connection.get("source"),
        "http_headers": raw_headers,
    }
    if "request_timeout" in connection:
        kwargs["request_timeout"] = float(connection["request_timeout"])
    legacy_ps = connection.get("legacy_prepared_statements")
    if legacy_ps is not None:
        kwargs["legacy_prepared_statements"] = bool(legacy_ps)
    return kwargs


def validate_source_type(source_type: str) -> None:
    if source_type not in SUPPORTED_SOURCE_TYPES:
        supported = ", ".join(SUPPORTED_SOURCE_TYPES)
        raise ValueError(
            f"Unsupported source type: {source_type}. Supported source types: {supported}"
        )


def validate_engine_type(engine_type: str) -> None:
    if engine_type not in SUPPORTED_ENGINE_TYPES:
        supported = ", ".join(SUPPORTED_ENGINE_TYPES)
        raise ValueError(
            f"Unsupported engine type: {engine_type}. Supported engine types: {supported}"
        )


def build_catalog_adapter(source_type: str, connection: dict[str, Any]) -> CatalogAdapter:
    validate_source_type(source_type)
    if source_type == "duckdb":
        from app.adapters.duckdb_adapter import DuckDBCatalogAdapter

        return DuckDBCatalogAdapter(_duckdb_path(connection))
    if source_type == "trino":
        from app.adapters.trino_adapter import TrinoCatalogAdapter

        return TrinoCatalogAdapter(**_trino_connect_kwargs(connection))
    raise ValueError(f"Unsupported source type: {source_type}")


def build_analytics_engine(engine_type: str, connection: dict[str, Any]) -> AnalyticsEngine:
    validate_engine_type(engine_type)
    if engine_type == "duckdb":
        from app.storage.duckdb_analytics import DuckDBAnalyticsEngine

        return DuckDBAnalyticsEngine(_duckdb_path(connection))
    if engine_type == "trino":
        from app.storage.trino_analytics import TrinoAnalyticsEngine

        return TrinoAnalyticsEngine(**_trino_connect_kwargs(connection))
    raise ValueError(f"Unsupported engine type: {engine_type}")
