"""Catalog and semantic-ref access helpers for the observe planner.

Internal to ``marivo.analysis.intents`` — extracted from ``observe_planner``.
"""

from __future__ import annotations

from typing import Any

from marivo.analysis.intents._observe_planner_types import FieldDetails
from marivo.analysis.intents.observe_errors import (
    RepairAction,
    RepairSafety,
    raise_observe_planning_error,
)
from marivo.semantic.catalog import (
    DerivedMetricDetails,
    DimensionDetails,
    EntityDetails,
    MetricDetails,
    SemanticCatalog,
    SemanticKind,
    SimpleMetricDetails,
    TimeDimensionDetails,
)
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError


def _catalog_id(ref: str, kind: SemanticKind) -> str:
    return f"{kind.value}.{ref}"


def _details(catalog: SemanticCatalog, ref: str) -> Any:
    for kind in (
        SemanticKind.METRIC,
        SemanticKind.ENTITY,
        SemanticKind.DIMENSION,
        SemanticKind.TIME_DIMENSION,
        SemanticKind.RELATIONSHIP,
        SemanticKind.MEASURE,
    ):
        try:
            return catalog.get(_catalog_id(ref, kind)).details()
        except SemanticRuntimeError as exc:
            if exc.kind != ErrorKind.NOT_FOUND.value:
                raise
    raise_observe_planning_error(
        code="path-missing",
        message=f"Semantic reference {ref!r} was not found.",
        candidates={"ref": ref},
        repair=[],
    )


def _entity(catalog: SemanticCatalog, ref: str) -> EntityDetails:
    details = _details(catalog, ref)
    if not isinstance(details, EntityDetails):
        raise_observe_planning_error(
            code="path-missing",
            message=f"Entity reference {ref!r} was not found.",
            candidates={"ref": ref},
            repair=[],
        )
    return details


def _metric(catalog: SemanticCatalog, ref: str) -> MetricDetails:
    details = _details(catalog, ref)
    if not isinstance(details, (SimpleMetricDetails, DerivedMetricDetails)):
        raise_observe_planning_error(
            code="derived-shared-planner-unsupported",
            message=f"Metric reference {ref!r} was not found.",
            candidates={"ref": ref},
            repair=[],
        )
    return details


def _fields_for_entity(catalog: SemanticCatalog, entity_ref: str) -> list[FieldDetails]:
    fields: list[FieldDetails] = []
    for kind in (SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION):
        for obj in catalog.list(str(kind), scope=f"entity.{entity_ref}"):
            details = obj.details()
            if isinstance(details, (DimensionDetails, TimeDimensionDetails)):
                fields.append(details)
    return fields


def _fields_for_entities(catalog: SemanticCatalog, entity_refs: set[str]) -> list[FieldDetails]:
    fields: list[FieldDetails] = []
    for entity_ref in sorted(entity_refs):
        fields.extend(_fields_for_entity(catalog, entity_ref))
    return fields


def _ref_id(value: Any) -> str:
    ref = getattr(value, "ref", None)
    if isinstance(ref, str):
        return ref
    nested = getattr(ref, "ref", None)
    if isinstance(nested, str):
        return nested
    semantic_id = getattr(value, "semantic_id", None)
    if isinstance(semantic_id, str):
        return semantic_id
    return str(value)


def _entity_id(field: Any) -> str:
    return _ref_id(field.entity)


def _input_ref_id(value: Any) -> str:
    return _ref_id(value)


def _relationship_id(relationship: Any) -> str:
    return _ref_id(relationship)


def _from_entity_id(relationship: Any) -> str:
    return _ref_id(relationship.from_entity)


def _to_entity_id(relationship: Any) -> str:
    return _ref_id(relationship.to_entity)


def resolve_metric_root(metric_ir: Any) -> str:
    root = getattr(metric_ir, "root_entity", None)
    if isinstance(root, str) and root:
        return root
    entities = tuple(getattr(metric_ir, "entities", ()))
    if len(entities) == 1:
        return entities[0]  # type: ignore[no-any-return]
    if not entities:
        raise_observe_planning_error(
            code="empty-base-entities",
            message=f"Base metric {metric_ir.semantic_id!r} references no entities.",
            candidates={},
            repair=[],
        )
    raise_observe_planning_error(
        code="missing-root",
        message=f"Multi-entity base metric {metric_ir.semantic_id!r} must declare root_entity.",
        candidates={"entities": sorted(entities)},
        repair=[
            RepairAction(
                action="set_metric_root",
                target=metric_ir.semantic_id,
                arg="root_entity",
                value=entities[0],
                safety=RepairSafety.MODELING_DECISION,
                why="the root defines preserved rows and the observe time axis",
            )
        ],
    )


def _fields_for_datasets(catalog: SemanticCatalog, entity_refs: set[str]) -> list[FieldDetails]:
    return _fields_for_entities(catalog, entity_refs)
