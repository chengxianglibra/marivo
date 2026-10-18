"""Dependency fingerprints for consumed physical Entity contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from typing import TYPE_CHECKING

from marivo._definition.graph import fingerprint
from marivo.refs import Ref, RefPayloadV1, SemanticKind, _create_ref

if TYPE_CHECKING:
    from marivo.semantic.validator import Registry


def _freeze(value: object) -> object:
    if type(value) is RefPayloadV1:
        return value
    if isinstance(value, Ref):
        return RefPayloadV1.from_ref(value)
    if value is None or isinstance(value, str | bool | int | float):
        return value
    if isinstance(value, Enum):
        return _freeze(value.value)
    if is_dataclass(value) and not isinstance(value, type):
        return tuple((field.name, _freeze(getattr(value, field.name))) for field in fields(value))
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("semantic dependency mappings require string keys")
        return tuple((key, _freeze(value[key])) for key in sorted(value))
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return tuple(_freeze(item) for item in value)
    raise TypeError(f"unsupported semantic dependency value: {type(value).__name__}")


def _fields(**values: object) -> tuple[tuple[str, object], ...]:
    return tuple((name, _freeze(value)) for name, value in values.items())


@dataclass(frozen=True, slots=True)
class _DependencyEntry:
    ref: RefPayloadV1
    body_digest: str | None
    fields: tuple[tuple[str, object], ...]
    bindings: tuple[()] = ()


def entity_dependency_fingerprint(registry: Registry, entity_id: str) -> str:
    """Hash an Entity's source, datasource, validity fields and internal relationships.

    The field projection and encoding preserve existing TargetEntityContract identity.
    This contract has no compiled-body sidecar or Metric expression input.
    """
    keys: set[tuple[SemanticKind, str]] = set()

    def collect_entity(path: str) -> None:
        key = (SemanticKind.ENTITY, path)
        if key in keys:
            return
        entity = registry.entities[path]
        keys.add(key)
        if entity.datasource in registry.datasources:
            keys.add((SemanticKind.DATASOURCE, entity.datasource))
        for field_name in ("valid_from", "valid_to"):
            field_path = getattr(entity.versioning, field_name, None)
            if isinstance(field_path, str) and field_path in registry.dimensions:
                dimension = registry.dimensions[field_path]
                kind = (
                    SemanticKind.TIME_DIMENSION
                    if dimension.is_time_dimension
                    else SemanticKind.DIMENSION
                )
                keys.add((kind, field_path))
                collect_entity(dimension.entity)

    collect_entity(entity_id)
    entity_ids = {path for kind, path in keys if kind is SemanticKind.ENTITY}
    for relationship in registry.relationships.values():
        if relationship.from_entity in entity_ids and relationship.to_entity in entity_ids:
            keys.add((SemanticKind.RELATIONSHIP, relationship.semantic_id))

    def ref(kind: SemanticKind, path: str) -> RefPayloadV1:
        return RefPayloadV1.from_ref(_create_ref(kind, path))

    entries: list[_DependencyEntry] = []
    for kind, path in sorted(keys):
        body_digest: str | None = None
        if kind is SemanticKind.ENTITY:
            entity = registry.entities[path]
            values = _fields(
                datasource_ref=ref(SemanticKind.DATASOURCE, entity.datasource),
                source=entity.source.to_dict(),
                primary_key=entity.primary_key,
                versioning=entity.versioning,
            )
        elif kind is SemanticKind.DATASOURCE:
            datasource = registry.datasources[path]
            values = _fields(
                backend_type=datasource.backend_type,
                fields=datasource.fields,
                env_refs=datasource.env_refs,
            )
        elif kind is SemanticKind.RELATIONSHIP:
            relationship = registry.relationships[path]
            values = _fields(
                from_entity_ref=ref(SemanticKind.ENTITY, relationship.from_entity),
                to_entity_ref=ref(SemanticKind.ENTITY, relationship.to_entity),
                keys=relationship.keys,
            )
        else:
            dimension = registry.dimensions[path]
            body_digest = dimension.body_ast_hash
            values = _fields(
                entity_ref=ref(SemanticKind.ENTITY, dimension.entity),
                kind=dimension.kind,
                granularity=dimension.granularity,
                parse=dimension.parse,
                is_default=dimension.is_default,
            )
        entries.append(_DependencyEntry(ref(kind, path), body_digest, values))
    return fingerprint(tuple(entries))
