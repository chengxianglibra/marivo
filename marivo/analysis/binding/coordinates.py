"""Pure scalar path binding shared by grouping and contribution predicates."""

from __future__ import annotations

from marivo._definition.observations import CoordinateBinding as CoordinateBinding
from marivo._definition.types import (
    ExactRef,
    FieldRead,
    IdentityRead,
)
from marivo.analysis.core.model import reject
from marivo.analysis.errors import AnalysisError, AnalysisRepair
from marivo.analysis.observation.relationship_binding import RelationshipResolver
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import SemanticKind, ref
from marivo.semantic.expressions import _Resolver
from marivo.semantic.validator import Registry, normalize_target_relationship


def identity_projection(
    read: IdentityRead, root: ExactRef, registry: Registry
) -> tuple[str, ...] | None:
    """Use complete declared foreign keys without reading a target profile."""
    if not read.path:
        return (
            tuple(registry.entities[root.ref.path].primary_key)
            if read.entity.ref == root.ref
            else None
        )
    available: dict[str, str] | None = None
    for step in read.path:
        relationship = normalize_target_relationship(registry, step.ref.path)
        mapped = {}
        for left, right in relationship.keys:
            source_column = left if available is None else available.get(left)
            if source_column is None:
                return None
            mapped[right] = source_column
        available = mapped
    assert available is not None
    keys = tuple(registry.entities[read.entity.ref.path].primary_key)
    return tuple(available[key] for key in keys) if all(key in available for key in keys) else None


def bind_identity(
    read: IdentityRead,
    root: ExactRef,
    registry: Registry,
    resolver: _Resolver,
    paths: RelationshipResolver,
) -> IdentityRead:
    """Bind all identity components through the same single-valued route."""
    entity = registry.entities[read.entity.ref.path]
    path = paths.resolve(
        root.ref.path,
        read.entity.ref.path,
        explicit=tuple(ref.relationship(step.ref.path) for step in read.path)
        if read.path
        else None,
        target="session.observe",
    )
    if entity.versioning is not None and read.entity.ref != root.ref and read.at is None:
        reject(
            "an exact attribute anchor for a versioned identity",
            read.entity.ref.path,
            "Select identities at session.instant(...).",
            "session.observe",
        )
    if entity.versioning is None and read.at is not None:
        reject(
            "a versioned identity for an attribute anchor",
            read.entity.ref.path,
            "Remove the anchor from the unversioned identity.",
            "session.observe",
        )
    return IdentityRead(
        resolver.entity_ref(read.entity.ref.path),
        tuple(resolver.exact(step) for step in path),
        read.at,
        tuple(entity.primary_key),
    )


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
                "Resolve an existing field with catalog.search(kind='dimension'). Available fields: "
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
                "Resolve an existing Measure with catalog.search(kind='dimension'). Available Measures: "
                + ", ".join(sorted(registry.measures)[:6]),
                "session.observe",
            )
        owner = measure.entity
    return FieldRead(resolver.exact(target.ref), resolver.entity_ref(owner))
