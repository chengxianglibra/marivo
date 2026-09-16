"""Pure, binding-owned source columns shared by admission and execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from marivo.analysis.compiler.errors import compilation_error
from marivo.analysis.observation.contracts import ObservationOwner
from marivo.analysis.observation.coordinates import relationship_columns
from marivo.refs import RefPayloadV1, SemanticKind
from marivo.semantic._expression_binding import expression_column_accesses
from marivo.semantic.ir import (
    HourPrefixParse,
    TableSourceIR,
    TargetDimensionContract,
    TargetEntityContract,
    TargetSnapshotVersion,
    TargetValidityVersion,
)
from marivo.semantic.validator import Registry, normalize_target_dimension

ColumnUse = Literal["value", "identity", "relationship", "version", "event"]


@dataclass(frozen=True, slots=True)
class SourceColumnDependency:
    logical: str
    physical: str
    declared_type: str
    uses: tuple[ColumnUse, ...]


@dataclass(frozen=True, slots=True, repr=False)
class EntitySourceDependency:
    owner: ObservationOwner
    entity: TargetEntityContract
    columns: tuple[SourceColumnDependency, ...]

    @property
    def namespace(self) -> tuple[str | None, str | None]:
        source = self.entity.source
        if not isinstance(source, TableSourceIR):
            return None, None
        database = source.database
        if isinstance(database, tuple):
            if len(database) == 2:
                return database[0], database[1]
            if len(database) == 1:
                return None, database[0]
            raise compilation_error("catalog/schema table namespace", "invalid namespace")
        return None, database

    @property
    def physical_columns(self) -> frozenset[str]:
        return frozenset(column.physical for column in self.columns)

    def validate_request(self, name: str, database: str | None, catalog: str | None) -> None:
        source = self.entity.source
        if not isinstance(source, TableSourceIR) or (
            source.table != name or self.namespace != (catalog, database)
        ):
            raise compilation_error("the exact dependency relation", "schema request mismatch")


@dataclass(frozen=True, slots=True, repr=False)
class SourceDependencies:
    entries: tuple[EntitySourceDependency, ...]

    def for_entity(self, entity: TargetEntityContract) -> EntitySourceDependency:
        for entry in self.entries:
            if entry.entity == entity:
                return entry
        raise compilation_error("an exact source Entity dependency", "missing source dependency")


class ColumnCollector:
    """Collect column facts while normalization traverses its existing paths."""

    def __init__(self, owner: ObservationOwner, registry: Registry) -> None:
        self.owner = owner
        self.registry = registry
        self.columns: dict[str, dict[str, set[ColumnUse]]] = {}
        self.active: set[tuple[SemanticKind, str]] = set()

    def add(self, entity: str, column: str, use: ColumnUse = "value") -> None:
        self.columns.setdefault(entity, {}).setdefault(column, set()).add(use)

    def axis(self, axis: TargetDimensionContract | None, use: ColumnUse = "value") -> None:
        if axis is None:
            return
        self.add(axis.entity_ref.path, axis.source_column, use)
        if isinstance(axis.parse, HourPrefixParse):
            prefix = normalize_target_dimension(self.registry, axis.parse.prefix)
            self.add(prefix.entity_ref.path, prefix.source_column, use)

    def route(self, route: tuple[str, ...]) -> None:
        for name in route:
            relation = self.registry.relationships[name]
            for left, right in relationship_columns(self.registry, relation):
                self.add(relation.from_entity, left, "relationship")
                self.add(relation.to_entity, right, "relationship")

    def field(self, reference: RefPayloadV1, entity: str) -> None:
        if reference.kind is SemanticKind.ENTITY:
            return
        if reference.kind in (SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION):
            self.axis(normalize_target_dimension(self.registry, reference.path))
            return
        self.body(reference.kind, reference.path, (entity,))

    def body(self, kind: SemanticKind, path: str, entities: tuple[str, ...]) -> None:
        key = (kind, path)
        if key in self.active:
            raise compilation_error("acyclic expression dependencies", "cyclic field binding")
        body = next(
            (
                body
                for ref, body in self.owner.sidecar.bodies.items()
                if ref.kind is kind and ref.path == path
            ),
            None,
        )
        if body is None or body.parameter_count != len(entities):
            raise compilation_error(
                "a captured expression with exact Entity arguments", "missing expression binding"
            )
        self.active.add(key)
        try:
            for position, column in expression_column_accesses(body):
                self.add(
                    entities[position], column, "event" if kind is SemanticKind.EVENT else "value"
                )
            for binding in body.bindings:
                bound_entity = entities[binding.entity_position]
                field_owner = next(
                    (
                        owner.path
                        for ref, owner in self.owner.sidecar.field_owners.items()
                        if ref.kind is binding.field_ref.kind and ref.path == binding.field_ref.path
                    ),
                    None,
                )
                if field_owner != bound_entity:
                    raise compilation_error(
                        "field bindings on their exact Entity", "expression owner mismatch"
                    )
                self.field(binding.field_ref, bound_entity)
        finally:
            self.active.remove(key)

    def finish(self, entities: tuple[TargetEntityContract, ...]) -> SourceDependencies:
        entries: list[EntitySourceDependency] = []
        for entity in entities:
            for name in (*entity.primary_key, *entity.version_row_key):
                self.add(entity.ref.path, name, "identity")
            version = entity.version
            if isinstance(version, TargetSnapshotVersion):
                self.axis(
                    normalize_target_dimension(self.registry, version.coordinate_ref.path),
                    "version",
                )
            elif isinstance(version, TargetValidityVersion):
                for ref in (version.valid_from_ref, version.valid_to_ref):
                    self.axis(normalize_target_dimension(self.registry, ref.path), "version")
            required = self.columns.get(entity.ref.path, {})
            declared = dict(entity.columns)
            if set(required) - declared.keys():
                raise compilation_error(
                    "declared logical source columns",
                    f"unknown necessary column on Entity {entity.ref.path}",
                )
            bindings = (
                dict(entity.source.columns) if isinstance(entity.source, TableSourceIR) else {}
            )
            columns = tuple(
                SourceColumnDependency(
                    name,
                    bindings[name].source if bindings else name,
                    kind,
                    tuple(sorted(required[name])),
                )
                for name, kind in entity.columns
                if name in required
            )
            entries.append(EntitySourceDependency(self.owner, entity, columns))
        return SourceDependencies(tuple(entries))
