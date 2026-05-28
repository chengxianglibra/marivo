"""Project-level datasource registry for ``marivo.analysis``."""

from __future__ import annotations

from marivo.analysis.datasources.audit import DatasourceAuditResult, audit_project
from marivo.analysis.datasources.registry import (
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
