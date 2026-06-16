"""Internal bridge from SemanticProject to IR-level registry access.

This module exists so that internal consumers (observe planner, materializer,
parity) can access IR objects without going through deleted SemanticProject
catalog read wrappers. It remains the internal IR access path for runtime
code that cannot consume catalog value objects.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from marivo.semantic.ir import (
    DimensionIR,
    EntityIR,
    MetricIR,
    RelationshipIR,
)
from marivo.semantic.validator import Registry, Sidecar

if TYPE_CHECKING:
    from marivo.semantic.reader import SemanticProject


def _reg(project: SemanticProject) -> Registry:
    reg = project._registry
    if reg is None:
        from marivo.semantic.reader import _require_registry

        return _require_registry(reg, project=project)
    return reg


def get_entity_ir(project: SemanticProject, name: str) -> EntityIR | None:
    return _reg(project).entities.get(name)


def get_metric_ir(project: SemanticProject, name: str) -> MetricIR | None:
    return _reg(project).metrics.get(name)


def iter_entity_irs(project: SemanticProject, *, domain: str | None = None) -> list[EntityIR]:
    entities = list(_reg(project).entities.values())
    if domain is not None:
        entities = [e for e in entities if e.domain == domain]
    return entities


def iter_dimension_irs(
    project: SemanticProject,
    *,
    domain: str | None = None,
    entity: str | None = None,
) -> list[DimensionIR]:
    irs = [f for f in _reg(project).dimensions.values() if not f.is_time_dimension]
    if domain is not None:
        irs = [f for f in irs if f.domain == domain]
    if entity is not None:
        irs = [f for f in irs if f.entity == entity]
    return irs


def iter_time_dimension_irs(
    project: SemanticProject,
    *,
    domain: str | None = None,
    entity: str | None = None,
) -> list[DimensionIR]:
    irs = [f for f in _reg(project).dimensions.values() if f.is_time_dimension]
    if domain is not None:
        irs = [f for f in irs if f.domain == domain]
    if entity is not None:
        irs = [f for f in irs if f.entity == entity]
    return irs


def iter_all_dimension_irs(
    project: SemanticProject,
    *,
    entity: str | None = None,
) -> list[DimensionIR]:
    return [
        *iter_dimension_irs(project, entity=entity),
        *iter_time_dimension_irs(project, entity=entity),
    ]


def iter_metric_irs(project: SemanticProject, *, entity: str | None = None) -> list[MetricIR]:
    metrics = list(_reg(project).metrics.values())
    if entity is not None:
        metrics = [m for m in metrics if entity in m.entities]
    return metrics


def iter_relationship_irs(
    project: SemanticProject, *, domain: str | None = None
) -> list[RelationshipIR]:
    rels = list(_reg(project).relationships.values())
    if domain is not None:
        rels = [r for r in rels if r.domain == domain]
    return rels


def get_sidecar(project: SemanticProject) -> Sidecar | None:
    return project._sidecar
