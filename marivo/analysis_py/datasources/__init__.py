"""Project-level datasource registry for ``marivo.analysis_py``."""

from __future__ import annotations

from marivo.analysis_py.datasources.audit import DatasourceAuditResult, audit_project
from marivo.analysis_py.datasources.registry import (
    DatasourceDescription,
    DatasourceSummary,
    DatasourceTestResult,
    build_backend,
    describe,
    list,
    remove,
    set,
    test,
)

__all__ = [
    "DatasourceAuditResult",
    "DatasourceDescription",
    "DatasourceSummary",
    "DatasourceTestResult",
    "audit_project",
    "build_backend",
    "describe",
    "list",
    "remove",
    "set",
    "test",
]
