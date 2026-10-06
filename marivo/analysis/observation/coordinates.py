"""Governed source coordinate paths, grains, and contribution admission."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from marivo.analysis.observation.contracts import (
    construction_error,
)
from marivo.semantic.ir import RelationshipIR
from marivo.semantic.validator import (
    Registry,
    normalize_target_entity,
    normalize_target_relationship,
)


def relationship_columns(
    registry: Registry, relationship: RelationshipIR
) -> tuple[tuple[str, str], ...]:
    """Resolve authored Dimension refs to their exact endpoint source columns."""
    return normalize_target_relationship(registry, relationship.semantic_id).keys


def functional_path(
    registry: Registry,
    source: str,
    target: str,
    *,
    allow_versioned_target: bool = False,
    allow_versioned_source: bool = False,
    allow_versioned_intermediates: bool = False,
    entity_columns: Callable[[str], Mapping[str, str]] | None = None,
) -> tuple[str, ...]:
    """Prove a unique path whose every destination join key is its full identity."""
    if source == target:
        return ()
    edges: dict[str, list[tuple[str, str]]] = {}
    for relation in registry.relationships.values():
        mapping = normalize_target_relationship(registry, relation.semantic_id)
        if mapping.cardinality in {"one_to_one", "many_to_one"}:
            edges.setdefault(relation.from_entity, []).append(
                (relation.to_entity, relation.semantic_id)
            )
        if mapping.cardinality in {"one_to_one", "one_to_many"}:
            edges.setdefault(relation.to_entity, []).append(
                (relation.from_entity, relation.semantic_id)
            )
    found: list[tuple[str, ...]] = []

    def walk(current: str, visited: frozenset[str], path: tuple[str, ...]) -> None:
        if len(found) > 1:
            return
        for destination, relation in sorted(edges.get(current, ())):
            if destination in visited:
                continue
            candidate = (*path, relation)
            if destination == target:
                found.append(candidate)
            else:
                walk(destination, visited | {destination}, candidate)

    walk(source, frozenset({source}), ())
    if len(found) != 1:
        raise construction_error(
            "one unique functional governed relationship path",
            "missing, ambiguous, or fanout path",
            repair="Use one directly bound or uniquely to-one governed mapping for this operation; a to-many contribution mapping requires its own coordinate admission.",
        )
    current = source
    for name in found[0]:
        relation = registry.relationships[name]
        left_contract = normalize_target_entity(registry, relation.from_entity)
        right_contract = normalize_target_entity(registry, relation.to_entity)
        left_types, right_types = (
            (dict(left_contract.columns), dict(right_contract.columns))
            if entity_columns is None
            else (entity_columns(relation.from_entity), entity_columns(relation.to_entity))
        )
        if any(
            left_key not in left_types
            or right_key not in right_types
            or left_types[left_key] != right_types[right_key]
            for left_key, right_key in relationship_columns(registry, relation)
        ):
            raise construction_error(
                "exact compatible declared relationship key types",
                "missing or incompatible join keys",
            )
        destination = (
            relation.to_entity if current == relation.from_entity else relation.from_entity
        )
        for entity in (left_contract, right_contract):
            if entity.version is not None and not (
                (allow_versioned_target and entity.ref.path == target)
                or (allow_versioned_source and entity.ref.path == source)
                or (allow_versioned_intermediates and entity.ref.path not in (source, target))
            ):
                raise construction_error(
                    "resolved atemporal relationship representations",
                    "versioned source or intermediate path",
                    repair="Use a non-versioned path or an already selected AnalysisDomain endpoint; historical enrichment requires its own temporal contract.",
                )
        current = destination
    return found[0]


def path_is_functional(registry: Registry, source: str, path: tuple[str, ...]) -> bool:
    current = source
    for name in path:
        relation = registry.relationships[name]
        forward = current == relation.from_entity
        current = relation.to_entity if forward else relation.from_entity
        mapping = normalize_target_relationship(registry, name)
        if (forward and mapping.cardinality not in {"one_to_one", "many_to_one"}) or (
            not forward and mapping.cardinality not in {"one_to_one", "one_to_many"}
        ):
            return False
    return True
