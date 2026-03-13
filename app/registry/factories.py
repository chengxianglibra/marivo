from __future__ import annotations

from typing import Any

from app.adapters.base import CatalogAdapter
from app.storage.analytics import AnalyticsEngine


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

        return TrinoCatalogAdapter(
            host=connection["host"],
            port=connection.get("port", 8080),
            user=connection.get("user", "omnidb"),
            password=connection.get("password"),
            http_scheme=connection.get("http_scheme", "http"),
            catalog=connection.get("catalog", "hive"),
            schema=connection.get("schema", "default"),
        )
    raise ValueError(f"Unsupported source type: {source_type}")


def build_analytics_engine(engine_type: str, connection: dict[str, Any]) -> AnalyticsEngine:
    if engine_type == "duckdb":
        from app.storage.duckdb_analytics import DuckDBAnalyticsEngine

        return DuckDBAnalyticsEngine(connection["path"])
    if engine_type == "trino":
        from app.storage.trino_analytics import TrinoAnalyticsEngine

        return TrinoAnalyticsEngine(
            host=connection["host"],
            port=connection.get("port", 8080),
            user=connection.get("user", "omnidb"),
            catalog=connection.get("catalog", "hive"),
            schema=connection.get("schema", "default"),
        )
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
