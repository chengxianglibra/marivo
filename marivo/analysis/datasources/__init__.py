"""Project-level datasource registry for ``marivo.analysis``."""

from __future__ import annotations

from marivo.analysis.datasources.audit import DatasourceAuditResult, audit_project
from marivo.analysis.datasources.metadata import (
    ColumnMetadata,
    MetadataWarning,
    PartitionMetadata,
    TableMetadata,
    inspect_table,
)
from marivo.analysis.datasources.registry import (
    DatasourceDescription,
    DatasourceSummary,
    DatasourceTestResult,
    all,
    build_backend,
    describe,
    preview,
    register,
    remove,
    test,
)

__all__ = [
    "ColumnMetadata",
    "DatasourceAuditResult",
    "DatasourceDescription",
    "DatasourceSummary",
    "DatasourceTestResult",
    "MetadataWarning",
    "PartitionMetadata",
    "TableMetadata",
    "all",
    "audit_project",
    "build_backend",
    "describe",
    "inspect_table",
    "preview",
    "register",
    "remove",
    "test",
]
