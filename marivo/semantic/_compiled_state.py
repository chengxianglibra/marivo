"""Immutable compiled semantic state constructed by the loader."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast

from marivo.refs import Ref, SemanticKindTag
from marivo.semantic._definition_identity import definition_fingerprint
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.ir import (
    DimensionIR,
    EntityIR,
    MeasureIR,
    MetricIR,
    RelationshipIR,
    SemiAdditive,
    composition_components,
)
from marivo.semantic.validator import Registry


@dataclass(frozen=True, slots=True)
class CompiledRootIdentity:
    """Semantic source-root role and configured order, without machine paths."""

    role: str
    order: int


@dataclass(frozen=True, slots=True)
class CompiledSemanticState:
    """One immutable interpretation of the selected authored semantic world."""

    definition_fingerprint: str
    selected_roots: tuple[CompiledRootIdentity, ...]
    filtered_domains: tuple[str, ...]
    registry: Registry
    definitions: Mapping[Ref[SemanticKindTag], object]
    dependencies: Mapping[Ref[SemanticKindTag], tuple[Ref[SemanticKindTag], ...]]
    sidecar: CompiledExpressionSidecar

    def __post_init__(self) -> None:
        object.__setattr__(self, "selected_roots", tuple(self.selected_roots))
        object.__setattr__(self, "filtered_domains", tuple(self.filtered_domains))
        object.__setattr__(self, "definitions", MappingProxyType(dict(self.definitions)))
        object.__setattr__(
            self,
            "dependencies",
            MappingProxyType({ref: tuple(values) for ref, values in self.dependencies.items()}),
        )


def _definition_rows(registry: Registry) -> dict[Ref[SemanticKindTag], object]:
    rows: dict[Ref[SemanticKindTag], object] = {}
    rows.update(
        (cast("Ref[SemanticKindTag]", Ref.domain(key)), value)
        for key, value in registry.domains.items()
    )
    rows.update(
        (cast("Ref[SemanticKindTag]", Ref.datasource(key)), value)
        for key, value in registry.datasources.items()
    )
    rows.update(
        (cast("Ref[SemanticKindTag]", Ref.entity(key)), value)
        for key, value in registry.entities.items()
    )
    for key, value in registry.dimensions.items():
        ref = Ref.time_dimension(key) if value.is_time_dimension else Ref.dimension(key)
        rows[cast("Ref[SemanticKindTag]", ref)] = value
    rows.update(
        (cast("Ref[SemanticKindTag]", Ref.measure(key)), value)
        for key, value in registry.measures.items()
    )
    rows.update(
        (cast("Ref[SemanticKindTag]", Ref.metric(key)), value)
        for key, value in registry.metrics.items()
    )
    rows.update(
        (cast("Ref[SemanticKindTag]", Ref.relationship(key)), value)
        for key, value in registry.relationships.items()
    )
    return rows


def _ref_for_path(registry: Registry, path: str) -> Ref[SemanticKindTag] | None:
    if path in registry.datasources:
        return cast("Ref[SemanticKindTag]", Ref.datasource(path))
    if path in registry.entities:
        return cast("Ref[SemanticKindTag]", Ref.entity(path))
    dimension = registry.dimensions.get(path)
    if dimension is not None:
        ref = Ref.time_dimension(path) if dimension.is_time_dimension else Ref.dimension(path)
        return cast("Ref[SemanticKindTag]", ref)
    if path in registry.measures:
        return cast("Ref[SemanticKindTag]", Ref.measure(path))
    if path in registry.metrics:
        return cast("Ref[SemanticKindTag]", Ref.metric(path))
    if path in registry.relationships:
        return cast("Ref[SemanticKindTag]", Ref.relationship(path))
    if path in registry.domains:
        return cast("Ref[SemanticKindTag]", Ref.domain(path))
    return None


def _dependencies_for(
    ref: Ref[SemanticKindTag],
    definition: object,
    *,
    registry: Registry,
    sidecar: CompiledExpressionSidecar,
) -> tuple[Ref[SemanticKindTag], ...]:
    paths: list[str] = []
    if isinstance(definition, EntityIR):
        paths.append(definition.datasource)
    elif isinstance(definition, (DimensionIR, MeasureIR)):
        paths.append(definition.entity)
        if isinstance(definition, MeasureIR) and isinstance(definition.additivity, SemiAdditive):
            paths.append(definition.additivity.over)
    elif isinstance(definition, MetricIR):
        paths.extend(definition.entities)
        if definition.measure is not None:
            paths.append(definition.measure)
        if definition.aggregation_target is not None:
            paths.append(definition.aggregation_target)
        if definition.composition is not None:
            paths.extend(composition_components(definition.composition).values())
            composition_over = getattr(definition.composition, "over", None)
            if isinstance(composition_over, str):
                paths.append(composition_over)
        if isinstance(definition.additivity, SemiAdditive):
            paths.append(definition.additivity.over)
    elif isinstance(definition, RelationshipIR):
        paths.extend((definition.from_entity, definition.to_entity))
        for key in definition.keys:
            paths.extend((key.from_key, key.to_key))
    body = sidecar.bodies.get(ref)
    if body is not None:
        paths.extend(binding.field_ref.path for binding in body.bindings)
    dependencies = {
        resolved
        for path in paths
        if (resolved := _ref_for_path(registry, path)) is not None and resolved != ref
    }
    return tuple(sorted(dependencies, key=lambda item: item.key))


def build_compiled_state(
    *,
    registry: Registry,
    sidecar: CompiledExpressionSidecar,
    selected_root_roles: Sequence[str],
    filtered_domains: Sequence[str],
) -> CompiledSemanticState:
    """Copy and freeze all compiled values after successful validation."""
    definitions = _definition_rows(registry)
    dependencies = {
        ref: _dependencies_for(ref, definition, registry=registry, sidecar=sidecar)
        for ref, definition in definitions.items()
    }
    selected_roots = tuple(
        CompiledRootIdentity(role=role, order=index)
        for index, role in enumerate(selected_root_roles)
    )
    fingerprint = definition_fingerprint(
        selected_root_roles=selected_root_roles,
        filtered_domains=filtered_domains,
        definitions=definitions,
        dependencies=dependencies,
        sidecar=sidecar,
    )
    registry.freeze()
    return CompiledSemanticState(
        definition_fingerprint=fingerprint,
        selected_roots=selected_roots,
        filtered_domains=tuple(filtered_domains),
        registry=registry,
        definitions=definitions,
        dependencies=dependencies,
        sidecar=sidecar,
    )
