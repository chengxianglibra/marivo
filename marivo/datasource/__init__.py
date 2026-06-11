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
    inspect_source,
    inspect_table,
    list,
    preview,
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
from marivo.preview import PreviewResult, PreviewSamplePolicy, PreviewWarning

__all__ = [
    "AiContextIR",
    "ColumnMetadata",
    "DatasourceAiContextIR",
    "DatasourceDescription",
    "DatasourceIR",
    "DatasourceRef",
    "DatasourceSourceLocation",
    "DatasourceSpec",
    "DatasourceSummary",
    "DatasourceTestResult",
    "MetadataWarning",
    "PartitionMetadata",
    "PreviewResult",
    "PreviewSamplePolicy",
    "PreviewWarning",
    "TableMetadata",
    "connect",
    "datasource",
    "describe",
    "help",
    "help_text",
    "inspect_source",
    "inspect_table",
    "list",
    "load_datasources",
    "preview",
    "ref",
    "register",
    "remove",
    "test",
]
