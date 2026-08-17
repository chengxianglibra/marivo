"""Project-level datasource authoring and management API."""

from __future__ import annotations

from marivo.datasource.authoring import (
    ClickHouseSpec,
    DatasourceSpec,
    DuckDBSpec,
    MySQLSpec,
    PostgresSpec,
    SQLiteSpec,
    TrinoSpec,
    clickhouse,
    duckdb,
    mysql,
    postgres,
    sqlite,
    trino,
)
from marivo.datasource.catalog import DatasourceCatalog, load
from marivo.datasource.inspection import (
    ExecutionCapabilities,
    Partitioning,
    PartitionInspection,
    PhysicalExtent,
    SourceInspection,
    inspect,
)
from marivo.datasource.ir import TableColumnBindingIR
from marivo.datasource.manage import (
    DatasourceConnection,
    DatasourceDescription,
    DatasourceFailure,
    DatasourceList,
    DatasourceSummary,
    DatasourceTestResult,
    connect,
    describe,
    list,
    raw_sql,
    register,
    remove,
    test,
)
from marivo.datasource.snapshot import DiscoverySnapshot
from marivo.datasource.source import (
    PartitionScope,
    TableSource,
    UnprunedScope,
    csv,
    json,
    parquet,
    partition,
    source_column,
    source_param,
    table,
    time_range,
    unpruned,
)

__all__ = [
    "ClickHouseSpec",
    "DatasourceCatalog",
    "DatasourceConnection",
    "DatasourceDescription",
    "DatasourceFailure",
    "DatasourceList",
    "DatasourceSpec",
    "DatasourceSummary",
    "DatasourceTestResult",
    "DiscoverySnapshot",
    "DuckDBSpec",
    "ExecutionCapabilities",
    "MySQLSpec",
    "PartitionInspection",
    "PartitionScope",
    "Partitioning",
    "PhysicalExtent",
    "PostgresSpec",
    "SQLiteSpec",
    "SourceInspection",
    "TableColumnBindingIR",
    "TableSource",
    "TrinoSpec",
    "UnprunedScope",
    "clickhouse",
    "connect",
    "csv",
    "describe",
    "duckdb",
    "inspect",
    "json",
    "list",
    "load",
    "mysql",
    "parquet",
    "partition",
    "postgres",
    "raw_sql",
    "register",
    "remove",
    "source_column",
    "source_param",
    "sqlite",
    "table",
    "test",
    "time_range",
    "trino",
    "unpruned",
]


def _install_telemetry() -> None:
    import sys

    from marivo.datasource._capabilities.registry import REGISTRY
    from marivo.telemetry import install_surface_instrumentation

    install_surface_instrumentation(
        surface="datasource",
        descriptors=REGISTRY._descriptors,
        root_module=sys.modules[__name__],
    )


_install_telemetry()
