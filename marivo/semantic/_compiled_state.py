"""Immutable compiled semantic state constructed by the loader."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast

from marivo.refs import Ref, SemanticKindTag
from marivo.refs import ref as ref_factory
from marivo.semantic._definition_identity import definition_fingerprint
from marivo.semantic._dsl_authoring import AdditiveAllV1, AdditiveOverV1, AdditivityPolicy
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.ir import (
    DimensionIR,
    EntityIR,
    HourPrefixParse,
    MeasureIR,
    MetricIR,
    PeriodCalendarIR,
    RelationshipIR,
    SemiAdditive,
    SnapshotVersioningIR,
    TemporalSetIR,
    ValidityVersioningIR,
    WorkScheduleIR,
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
        (cast("Ref[SemanticKindTag]", ref_factory.domain(key)), value)
        for key, value in registry.domains.items()
    )
    rows.update(
        (cast("Ref[SemanticKindTag]", ref_factory.datasource(key)), value)
        for key, value in registry.datasources.items()
    )
    rows.update(
        (cast("Ref[SemanticKindTag]", ref_factory.entity(key)), value)
        for key, value in registry.entities.items()
    )
    for key, value in registry.dimensions.items():
        ref = (
            ref_factory.time_dimension(key)
            if value.is_time_dimension
            else ref_factory.dimension(key)
        )
        rows[cast("Ref[SemanticKindTag]", ref)] = value
    rows.update(
        (cast("Ref[SemanticKindTag]", ref_factory.measure(key)), value)
        for key, value in registry.measures.items()
    )
    rows.update(
        (cast("Ref[SemanticKindTag]", ref_factory.metric(key)), value)
        for key, value in registry.metrics.items()
    )
    rows.update(
        (cast("Ref[SemanticKindTag]", ref_factory.relationship(key)), value)
        for key, value in registry.relationships.items()
    )
    rows.update(
        (cast("Ref[SemanticKindTag]", ref_factory.period_calendar(key)), value)
        for key, value in registry.period_calendars.items()
    )
    rows.update(
        (cast("Ref[SemanticKindTag]", ref_factory.temporal_set(key)), value)
        for key, value in registry.temporal_sets.items()
    )
    rows.update(
        {
            (cast("Ref[SemanticKindTag]", ref_factory.work_schedule(key))): value
            for key, value in registry.work_schedules.items()
        }
    )
    return rows


def _ref_for_path(registry: Registry, path: str) -> Ref[SemanticKindTag] | None:
    if path in registry.datasources:
        return cast("Ref[SemanticKindTag]", ref_factory.datasource(path))
    if path in registry.entities:
        return cast("Ref[SemanticKindTag]", ref_factory.entity(path))
    dimension = registry.dimensions.get(path)
    if dimension is not None:
        ref = (
            ref_factory.time_dimension(path)
            if dimension.is_time_dimension
            else ref_factory.dimension(path)
        )
        return cast("Ref[SemanticKindTag]", ref)
    if path in registry.measures:
        return cast("Ref[SemanticKindTag]", ref_factory.measure(path))
    if path in registry.metrics:
        return cast("Ref[SemanticKindTag]", ref_factory.metric(path))
    if path in registry.relationships:
        return cast("Ref[SemanticKindTag]", ref_factory.relationship(path))
    if path in registry.period_calendars:
        return cast("Ref[SemanticKindTag]", ref_factory.period_calendar(path))
    if path in registry.temporal_sets:
        return cast("Ref[SemanticKindTag]", ref_factory.temporal_set(path))
    if path in registry.work_schedules:
        return cast("Ref[SemanticKindTag]", ref_factory.work_schedule(path))
    if path in registry.domains:
        return cast("Ref[SemanticKindTag]", ref_factory.domain(path))
    return None


def _additivity_axes(policy: AdditivityPolicy | None) -> tuple[str, ...]:
    """Coordinate paths a declared additivity policy names; they define its meaning."""
    if isinstance(policy, AdditiveOverV1):
        return policy.coordinates
    if isinstance(policy, AdditiveAllV1):
        return policy.exceptions
    return ()


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
        if isinstance(definition.versioning, SnapshotVersioningIR):
            paths.append(definition.versioning.partition_field)
        elif isinstance(definition.versioning, ValidityVersioningIR):
            paths.extend((definition.versioning.valid_from, definition.versioning.valid_to))
    elif isinstance(definition, (DimensionIR, MeasureIR)):
        paths.append(definition.entity)
        if isinstance(definition, DimensionIR) and isinstance(definition.parse, HourPrefixParse):
            paths.append(definition.parse.prefix)
        if isinstance(definition, MeasureIR):
            if isinstance(definition.additivity, SemiAdditive):
                paths.append(definition.additivity.over)
            paths.extend(_additivity_axes(definition.dsl_additivity))
            if definition.status_time_dimension is not None:
                paths.append(definition.status_time_dimension)
    elif isinstance(definition, MetricIR):
        paths.extend(definition.entities)
        paths.extend(_additivity_axes(definition.dsl_additivity))
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
    elif isinstance(definition, PeriodCalendarIR):
        paths.append(definition.date)
        paths.extend(ref for _level, ref in definition.levels)
        paths.extend(ref for _name, _level, ref in definition.correspondences)
    elif isinstance(definition, TemporalSetIR):
        paths.extend(
            value
            for value in (
                definition.occurrence_id,
                definition.start,
                definition.end,
                definition.category,
            )
            if isinstance(value, str)
        )
    elif isinstance(definition, WorkScheduleIR):
        paths.extend((definition.date, definition.is_working))
    body = sidecar.bodies.get(ref)
    if body is not None:
        paths.extend(binding.field_ref.path for binding in body.bindings)
    dependencies = {
        resolved
        for path in paths
        if (resolved := _ref_for_path(registry, path)) is not None and resolved != ref
    }
    return tuple(sorted(dependencies, key=lambda item: item.key))


def definition_graph(
    registry: Registry, sidecar: CompiledExpressionSidecar
) -> tuple[
    dict[Ref[SemanticKindTag], object],
    dict[Ref[SemanticKindTag], tuple[Ref[SemanticKindTag], ...]],
]:
    """Definition rows and their direct semantic dependencies for one registry."""
    definitions = _definition_rows(registry)
    dependencies = {
        ref: _dependencies_for(ref, definition, registry=registry, sidecar=sidecar)
        for ref, definition in definitions.items()
    }
    return definitions, dependencies


def build_compiled_state(
    *,
    registry: Registry,
    sidecar: CompiledExpressionSidecar,
    selected_root_roles: Sequence[str],
    filtered_domains: Sequence[str],
) -> CompiledSemanticState:
    """Copy and freeze all compiled values after successful validation."""
    definitions, dependencies = definition_graph(registry, sidecar)
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
