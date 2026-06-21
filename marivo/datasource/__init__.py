"""Project-level datasource authoring and management API."""

from __future__ import annotations

from marivo.datasource.authoring import (
    DatasourceRef,
    clickhouse,
    duckdb,
    mysql,
    postgres,
    ref,
    trino,
)
from marivo.datasource.catalog import DatasourceCatalog, load
from marivo.datasource.help import help, help_text
from marivo.datasource.manage import (
    DatasourceDescription,
    DatasourceList,
    DatasourceSummary,
    DatasourceTestResult,
    connect,
    describe,
    inspect_columns,
    inspect_source,
    inspect_table,
    list,
    preview,
    probe_join_keys,
    register,
    remove,
    test,
)
from marivo.datasource.metadata import TableMetadata
from marivo.datasource.scan import (
    ColumnInspection,
    JoinKeyProbe,
    JoinSide,
    ScanScope,
    csv,
    parquet,
    table,
)
from marivo.preview import PreviewResult

__all__ = [
    "ColumnInspection",
    "DatasourceCatalog",
    "DatasourceDescription",
    "DatasourceList",
    "DatasourceRef",
    "DatasourceSummary",
    "DatasourceTestResult",
    "JoinKeyProbe",
    "JoinSide",
    "PreviewResult",
    "ScanScope",
    "TableMetadata",
    "clickhouse",
    "connect",
    "csv",
    "describe",
    "duckdb",
    "help",
    "help_text",
    "inspect_columns",
    "inspect_source",
    "inspect_table",
    "list",
    "load",
    "mysql",
    "parquet",
    "postgres",
    "preview",
    "probe_join_keys",
    "ref",
    "register",
    "remove",
    "table",
    "test",
    "trino",
]
