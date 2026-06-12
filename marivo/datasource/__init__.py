"""Project-level datasource authoring and management API."""

from __future__ import annotations

from marivo.datasource.authoring import DatasourceRef, DatasourceSpec, datasource, ref
from marivo.datasource.help import help, help_text
from marivo.datasource.ir import (
    AiContextIR,
    DatasourceAiContextIR,
    DatasourceIR,
    DatasourceSourceLocation,
)
from marivo.datasource.loader import load_datasources
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
    file,
    table,
)
from marivo.preview import PreviewResult, PreviewSamplePolicy, PreviewWarning

__all__ = [
    "AiContextIR",
    "ColumnInspection",
    "ColumnMetadata",
    "ColumnProfile",
    "DatasourceAiContextIR",
    "DatasourceConnectionService",
    "DatasourceDescription",
    "DatasourceIR",
    "DatasourceRef",
    "DatasourceSourceLocation",
    "DatasourceSpec",
    "DatasourceSummary",
    "DatasourceTestResult",
    "JoinKeyProbe",
    "JoinSide",
    "MetadataWarning",
    "PartitionMetadata",
    "PreviewResult",
    "PreviewSamplePolicy",
    "PreviewWarning",
    "ScanReport",
    "ScanScope",
    "TableMetadata",
    "connect",
    "datasource",
    "describe",
    "file",
    "help",
    "help_text",
    "inspect_columns",
    "inspect_source",
    "inspect_table",
    "list",
    "load_datasources",
    "preview",
    "probe_join_keys",
    "ref",
    "register",
    "remove",
    "table",
    "test",
]
