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
from marivo.datasource.ir import (
    AiContextIR,
    CsvSourceIR,
    DatasourceAiContextIR,
    DatasourceIR,
    DatasourceSourceLocation,
    ParquetSourceIR,
)
from marivo.datasource.manage import (
    DatasourceDescription,
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
from marivo.datasource.metadata import (
    ColumnMetadata,
    MetadataWarning,
    PartitionMetadata,
    TableMetadata,
)
from marivo.datasource.runtime import DatasourceConnectionService
from marivo.datasource.scan import (
    ColumnInspection,
    ColumnProfile,
    JoinKeyProbe,
    JoinSide,
    ScanReport,
    ScanScope,
    csv,
    parquet,
    table,
)
from marivo.preview import PreviewResult, PreviewSamplePolicy, PreviewWarning

__all__ = [
    "AiContextIR",
    "ColumnInspection",
    "ColumnMetadata",
    "ColumnProfile",
    "CsvSourceIR",
    "DatasourceAiContextIR",
    "DatasourceCatalog",
    "DatasourceConnectionService",
    "DatasourceDescription",
    "DatasourceIR",
    "DatasourceRef",
    "DatasourceSourceLocation",
    "DatasourceSummary",
    "DatasourceTestResult",
    "JoinKeyProbe",
    "JoinSide",
    "MetadataWarning",
    "ParquetSourceIR",
    "PartitionMetadata",
    "PreviewResult",
    "PreviewSamplePolicy",
    "PreviewWarning",
    "ScanReport",
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
