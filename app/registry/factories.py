from __future__ import annotations

from typing import Any

from app.adapters.base import CatalogAdapter
from app.storage.analytics import AnalyticsEngine


def _trino_connect_kwargs(connection: dict[str, Any]) -> dict[str, Any]:
    """Extract Trino connection kwargs shared by catalog adapter and analytics engine."""
    raw_tags = connection.get("client_tags") or connection.get("client-tags")
    if isinstance(raw_tags, str):
        raw_tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
    raw_headers = connection.get("http_headers") or connection.get("http-headers")
    if isinstance(raw_headers, str):
        import json
        raw_headers = json.loads(raw_headers)
    kwargs: dict[str, Any] = dict(
        host=connection["host"],
        port=connection.get("port", 8080),
        user=connection.get("user", "omnidb"),
        password=connection.get("password"),
        http_scheme=connection.get("http_scheme") or connection.get("http-scheme", "http"),
        catalog=connection.get("catalog", "hive"),
        schema=connection.get("schema", "default"),
        client_tags=raw_tags,
        source=connection.get("source"),
        http_headers=raw_headers,
    )
    if "request_timeout" in connection:
        kwargs["request_timeout"] = float(connection["request_timeout"])
    legacy_ps = connection.get("legacy_prepared_statements")
    if legacy_ps is not None:
        kwargs["legacy_prepared_statements"] = bool(legacy_ps)
    return kwargs


def build_catalog_adapter(source_type: str, connection: dict[str, Any]) -> CatalogAdapter:
    if source_type in ("local", "duckdb"):
        from app.adapters.duckdb_adapter import DuckDBCatalogAdapter

        return DuckDBCatalogAdapter(connection["path"])
    if source_type == "hive_metastore":
        from app.adapters.hive_adapter import HiveMetastoreAdapter

        return HiveMetastoreAdapter(
            host=connection["host"],
            port=connection.get("port", 9083),
        )
    if source_type == "unity_catalog":
        from app.adapters.unity_adapter import UnityCatalogAdapter

        return UnityCatalogAdapter(
            host=connection["host"],
            token=connection.get("token", ""),
            catalog_name=connection.get("catalog", "main"),
        )
    if source_type == "aws_glue":
        from app.adapters.glue_adapter import GlueCatalogAdapter

        return GlueCatalogAdapter(
            region=connection.get("region", "us-east-1"),
            catalog_id=connection.get("catalog_id"),
        )
    if source_type == "polaris":
        from app.adapters.polaris_adapter import PolarisAdapter

        return PolarisAdapter(
            host=connection["host"],
            token=connection.get("token", ""),
            warehouse=connection.get("warehouse", "default"),
        )
    if source_type == "trino":
        from app.adapters.trino_adapter import TrinoCatalogAdapter

        return TrinoCatalogAdapter(**_trino_connect_kwargs(connection))
    raise ValueError(f"Unsupported source type: {source_type}")


def build_analytics_engine(engine_type: str, connection: dict[str, Any]) -> AnalyticsEngine:
    if engine_type == "duckdb":
        from app.storage.duckdb_analytics import DuckDBAnalyticsEngine

        return DuckDBAnalyticsEngine(connection["path"])
    if engine_type == "trino":
        from app.storage.trino_analytics import TrinoAnalyticsEngine

        return TrinoAnalyticsEngine(**_trino_connect_kwargs(connection))
    if engine_type == "spark_connect":
        from app.storage.spark_connect_analytics import SparkConnectAnalyticsEngine

        return SparkConnectAnalyticsEngine(remote=connection["remote"])
    if engine_type == "spark_thrift":
        from app.storage.spark_thrift_analytics import SparkThriftAnalyticsEngine

        return SparkThriftAnalyticsEngine(
            host=connection["host"],
            port=connection.get("port", 10009),
            username=connection.get("username", "omnidb"),
            database=connection.get("database", "default"),
            auth=connection.get("auth", "NOSASL"),
        )
    raise ValueError(f"Unsupported engine type: {engine_type}")
