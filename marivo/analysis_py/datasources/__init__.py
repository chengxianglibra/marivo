"""Project-level datasource registry for ``marivo.analysis_py``."""

from __future__ import annotations

from marivo.analysis_py.datasources.audit import DatasourceAuditResult, audit_project
from marivo.analysis_py.datasources.registry import (
    DatasourceDescription,
    DatasourceSummary,
    DatasourceTestResult,
    all,
    build_backend,
    describe,
    register,
    remove,
    test,
)

__all__ = [
    "DatasourceAuditResult",
    "DatasourceDescription",
    "DatasourceSummary",
    "DatasourceTestResult",
    "all",
    "audit_project",
    "build_backend",
    "describe",
    "register",
    "remove",
    "test",
]
