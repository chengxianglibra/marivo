"""Cross-check a semantic project against configured project datasources."""

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
    from marivo.semantic._registry_bridge import iter_datasource_irs, iter_entity_irs

    configured = {datasource.name for datasource in iter_datasource_irs(project)}
    present: list[str] = []
    missing: list[str] = []
    semantic_ids: dict[str, str] = {}
    for dataset in iter_entity_irs(project):
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
