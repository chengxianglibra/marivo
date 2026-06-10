"""Cross-check a semantic project against project-level datasources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from marivo.semantic.reader import SemanticProject


@dataclass(frozen=True)
class DatasourceAuditResult:
    present: list[str]
    missing: list[str]
    semantic_ids: dict[str, str]


def audit_project(project: SemanticProject) -> DatasourceAuditResult:
    configured = {summary.name for summary in project.list_datasources()}
    present: list[str] = []
    missing: list[str] = []
    semantic_ids: dict[str, str] = {}
    for dataset in project.list_entities():
        semantic_ids[dataset.datasource] = dataset.datasource
        if dataset.datasource in configured:
            present.append(dataset.datasource)
        else:
            missing.append(dataset.datasource)
    return DatasourceAuditResult(
        present=sorted(set(present)),
        missing=sorted(set(missing)),
        semantic_ids=semantic_ids,
    )
