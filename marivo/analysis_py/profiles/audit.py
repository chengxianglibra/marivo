"""Cross-check a semantic project against the profile registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from marivo.analysis_py.profiles import store as _store

if TYPE_CHECKING:
    from marivo.semantic_py import SemanticProject


@dataclass(frozen=True)
class ProfileAuditResult:
    """Result of :func:`audit_project`.

    ``present`` and ``missing`` use the short datasource ``name`` (the value
    callers pass to ``backend_factory``); ``semantic_ids`` is the
    ``model.name`` qualified id for diagnostic display.
    """

    present: list[str]
    missing: list[str]
    semantic_ids: dict[str, str]


def audit_project(project: SemanticProject) -> ProfileAuditResult:
    configured = set(_store.list_names())
    present: list[str] = []
    missing: list[str] = []
    semantic_ids: dict[str, str] = {}
    for summary in project.list_datasources():
        semantic_ids[summary.name] = summary.semantic_id
        if summary.name in configured:
            present.append(summary.name)
        else:
            missing.append(summary.name)
    present.sort()
    missing.sort()
    return ProfileAuditResult(
        present=present,
        missing=missing,
        semantic_ids=semantic_ids,
    )
