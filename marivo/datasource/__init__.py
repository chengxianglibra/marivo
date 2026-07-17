"""Project-level datasource authoring and management API."""

from __future__ import annotations

from marivo.datasource.authoring import (
    ClickHouseSpec,
    DatasourceRef,
    DatasourceSpec,
    DuckDBSpec,
    MySQLSpec,
    PostgresSpec,
    TrinoSpec,
    clickhouse,
    duckdb,
    mysql,
    postgres,
    ref,
    trino,
)
from marivo.datasource.catalog import DatasourceCatalog, load
from marivo.datasource.help import help, help_text
from marivo.datasource.inspection import (
    ExecutionCapabilities,
    Partitioning,
    PartitionInspection,
    PhysicalExtent,
    SourceInspection,
    inspect,
)
from marivo.datasource.manage import (
    DatasourceConnection,
    DatasourceDescription,
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
    table,
    unpruned,
)

__all__ = [
    "ClickHouseSpec",
    "DatasourceCatalog",
    "DatasourceConnection",
    "DatasourceDescription",
    "DatasourceList",
    "DatasourceRef",
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
    "SourceInspection",
    "TableSource",
    "TrinoSpec",
    "UnprunedScope",
    "clickhouse",
    "connect",
    "csv",
    "describe",
    "duckdb",
    "help",
    "help_text",
    "inspect",
    "json",
    "list",
    "load",
    "mysql",
    "parquet",
    "partition",
    "postgres",
    "raw_sql",
    "ref",
    "register",
    "remove",
    "table",
    "test",
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
