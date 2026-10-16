"""Pure scalar path binding shared by grouping and contribution predicates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from marivo._definition.types import AxisBinding, CalendarCoordinate, ExactRef, FieldRead
from marivo.analysis.core.model import reject
from marivo.analysis.errors import AnalysisError, AnalysisRepair
from marivo.analysis.observation.relationship_binding import RelationshipResolver
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import SemanticKind, ref
from marivo.semantic.expressions import Field, _Resolver
from marivo.semantic.validator import Registry


@dataclass(frozen=True, slots=True)
class CoordinateBinding:
    """Ordered complete output axes and each root's single-valued classification."""

    axes: tuple[AxisBinding | CalendarCoordinate, ...]
    mode: Literal["calendar", "grouped", "singleton"]


def field_read(field: Field) -> FieldRead:
    return FieldRead(field.ref, field.root, tuple(step.ref for step in field.path), field.anchor)


def bind_read(
    read: FieldRead,
    root: ExactRef,
    registry: Registry,
    resolver: _Resolver,
    paths: RelationshipResolver,
) -> FieldRead:
    if root.ref.path not in registry.entities or read.entity.ref.path not in registry.entities:
        reject(
            "declared contribution and field Entities",
            f"{root.ref.path} -> {read.entity.ref.path}",
            "Resolve the inputs from this Session's completed Catalog. Available Entities: "
            + ", ".join(sorted(registry.entities)[:6]),
            "session.observe",
        )
    current = native_read(read.field, registry, resolver)
    if current.entity.ref != read.entity.ref:
        reject(
            "a field bound to its declared Entity",
            f"{read.field.ref.path} belongs to {current.entity.ref.path}",
            "Resolve the field from the completed Catalog and rebuild its scalar binding.",
            "session.observe",
        )
    explicit = tuple(ref.relationship(step.ref.path) for step in read.path) if read.path else None
    try:
        path = paths.resolve(
            root.ref.path, read.entity.ref.path, explicit=explicit, target="session.observe"
        )
    except AnalysisError as error:
        raise AnalysisError(
            message="The scalar field cannot be bound to this contribution occurrence.",
            expected=error.expected,
            received=f"{root.ref.path} -> {read.field.ref.path}: {error.received}",
            location="session.observe",
            repair=AnalysisRepair(
                kind="user_choice" if error.repair and error.repair.candidates else "inspect",
                action="Resolve a declared single-valued path and bind the field with field.via((relationship, ...)); use separate observations for incompatible roots.",
                candidates=error.repair.candidates if error.repair else (),
                help_target=LiveHelpTarget(surface="analysis", canonical_id="session.observe"),
            ),
        ) from error
    entity = registry.entities[read.entity.ref.path]
    if entity.versioning is None and read.at is not None:
        reject(
            "a versioned field owner for an attribute anchor",
            read.entity.ref.path,
            "Resolve the field in this completed semantic scope and bind its declared version contract.",
            "session.observe",
        )
    if entity.versioning is not None and read.entity.ref != root.ref and read.at is None:
        reject(
            "an exact attribute anchor for a versioned field",
            read.field.ref.path,
            "Bind the field with field.at(session.instant(...)).",
            "session.observe",
        )
    for step in path:
        relation = registry.relationships[step.path]
        if (
            relation.to_entity != read.entity.ref.path
            and registry.entities[relation.to_entity].versioning is not None
        ):
            reject(
                "a path with resolved intermediate versions",
                relation.to_entity,
                "Choose a path without unbound versioned intermediates.",
                "session.observe",
            )
    return FieldRead(
        current.field, current.entity, tuple(resolver.exact(step) for step in path), read.at
    )


def native_read(target: ExactRef, registry: Registry, resolver: _Resolver) -> FieldRead:
    if target.ref.kind in (SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION):
        dimension = registry.dimensions.get(target.ref.path)
        if dimension is None or dimension.is_time_dimension != (
            target.ref.kind is SemanticKind.TIME_DIMENSION
        ):
            reject(
                "a declared field with the required kind",
                target.ref.key,
                "Resolve an existing field from catalog.fields. Available fields: "
                + ", ".join(sorted(registry.dimensions)[:6]),
                "session.observe",
            )
        owner = dimension.entity
    else:
        measure = registry.measures.get(target.ref.path)
        if measure is None:
            reject(
                "a declared Measure",
                target.ref.key,
                "Resolve an existing Measure from catalog.fields. Available Measures: "
                + ", ".join(sorted(registry.measures)[:6]),
                "session.observe",
            )
        owner = measure.entity
    return FieldRead(resolver.exact(target.ref), resolver.entity_ref(owner))
