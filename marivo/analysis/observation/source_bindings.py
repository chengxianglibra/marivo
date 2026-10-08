"""Construction-scoped, immutable and redacted parameterized-source captures."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import InitVar, dataclass
from math import isfinite
from typing import SupportsIndex, TypeAlias

from marivo._compat import Never
from marivo.analysis.datasets.handles import CanonicalValue, _digest
from marivo.analysis.observation.errors import ObservationBindingError
from marivo.datasource.ir import (
    EntitySourceIR,
    JsonSourceIR,
    SourceParamIR,
    json_source_param_names,
)
from marivo.refs import EntityKind, Ref, RefPayloadV1, SemanticKind
from marivo.semantic.ir import TargetEntityContract
from marivo.semantic.validator import Registry, _snapshot_target_source

SourceBindingScalar: TypeAlias = str | bool | int | float
SourceBindingValue: TypeAlias = (
    SourceBindingScalar | list[SourceBindingScalar] | tuple[SourceBindingScalar, ...]
)
SourceBindingMap: TypeAlias = Mapping[Ref[EntityKind], Mapping[str, SourceBindingValue]]
_CapturedValue: TypeAlias = SourceBindingScalar | tuple[SourceBindingScalar, ...]
_TOKEN = object()
_SECRET_NAME = re.compile(
    r"password|passwd|(?:^|[_-])pwd(?:$|[_-])|secret|token|credential|"
    r"api[_-]?key|authorization|authentication|cookie|(?:^|[_-])auth(?:$|[_-])|_env$",
    re.IGNORECASE,
)


def _error(expected: str, received: str) -> ObservationBindingError:
    return ObservationBindingError(
        expected=expected,
        received=received,
        repair="Bind exactly the declared non-secret parameters to current Entity refs before constructing the source.",
        location="observation.source_bindings",
    )


def _scalar(value: object) -> SourceBindingScalar:
    if type(value) is str:
        return value
    if type(value) is bool:
        return value
    if type(value) is int:
        return value
    if type(value) is float and isfinite(value):
        return value
    raise _error(
        "exact str, bool, int or finite float", f"unsupported scalar type: {type(value).__name__}"
    )


def _value(value: object) -> _CapturedValue:
    if type(value) in (list, tuple):
        if not isinstance(value, list | tuple) or not value:
            raise _error("non-empty flat list or tuple", "empty binding sequence")
        return tuple(_scalar(item) for item in value)
    return _scalar(value)


def _binding_digest(
    entity_ref: Ref[EntityKind], names: tuple[str, ...], values: tuple[_CapturedValue, ...]
) -> str:
    return _digest(("source.bindings.v1", entity_ref.kind.value, entity_ref.path, names, values))


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class BoundSourceParametersV1:
    """Private process-local values; only identity_payload is safe to disclose."""

    _token: InitVar[object]
    entity_ref: Ref[EntityKind]
    ordered_parameter_names: tuple[str, ...]
    private_canonical_typed_values: tuple[_CapturedValue, ...]
    exact_value_digest: str

    def __post_init__(self, _token: object) -> None:
        if _token is not _TOKEN:
            raise _error("source-factory capture", "direct parameter capture construction")
        self._validate()

    def _validate(self) -> None:
        if type(self.entity_ref) is not Ref or self.entity_ref.kind is not SemanticKind.ENTITY:
            raise _error("exact Entity ref", "invalid captured Entity")
        names = self.ordered_parameter_names
        values = self.private_canonical_typed_values
        if (
            type(names) is not tuple
            or type(values) is not tuple
            or not names
            or len(names) != len(values)
            or any(type(name) is not str or not name for name in names)
            or len(set(names)) != len(names)
        ):
            raise _error("ordered immutable complete parameter capture", "invalid capture layout")
        for item in values:
            if type(item) is tuple:
                if not item:
                    raise _error("non-empty captured sequence", "empty capture sequence")
                for component in item:
                    _scalar(component)
            else:
                _scalar(item)
        if self.exact_value_digest != _binding_digest(self.entity_ref, names, values):
            raise _error("exact captured-value digest", "corrupt capture identity")

    @property
    def bounded_redacted_projection(self) -> tuple[tuple[str, str], ...]:
        """Return bounded schema facts without parameter values."""
        return (
            ("entity", self.entity_ref.path[:160]),
            ("parameter_count", str(len(self.ordered_parameter_names))),
            ("values", "redacted"),
        )

    def identity_payload(self) -> CanonicalValue:
        self._validate()
        return (
            "source.bindings.v1",
            self.entity_ref.path,
            self.ordered_parameter_names,
            self.exact_value_digest,
        )

    def __repr__(self) -> str:
        return (
            f"<private source binding entity={self.entity_ref.path[:100]} "
            f"parameters={len(self.ordered_parameter_names)}; values redacted>"
        )

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        raise _error("process-local source parameter capture", "serialization")


def _parameter_slots(source: JsonSourceIR) -> tuple[tuple[str, str], ...]:
    slots: list[tuple[str, str]] = []
    for slot_name, declared in source.query_params:
        if isinstance(declared, SourceParamIR):
            slots.append((declared.name, slot_name))
        elif isinstance(declared, Sequence) and not isinstance(declared, str | bytes | bytearray):
            slots.extend(
                (item.name, slot_name) for item in declared if isinstance(item, SourceParamIR)
            )
    for path, declared in source.body_params:
        slots.extend((declared.name, part) for part in path if isinstance(part, str))
    return tuple(slots)


@dataclass(frozen=True, slots=True, repr=False)
class _BindingDeclaration:
    ref: RefPayloadV1
    source: EntitySourceIR
    credential_slots: tuple[str, ...]


def _required(entity: _BindingDeclaration) -> tuple[str, ...]:
    source = entity.source
    if not isinstance(source, JsonSourceIR):
        return ()
    names = json_source_param_names(source)
    credentials = frozenset(name.casefold() for name in entity.credential_slots)
    for name, slot in (*((name, name) for name in names), *_parameter_slots(source)):
        if (
            _SECRET_NAME.search(name)
            or _SECRET_NAME.search(slot)
            or name.casefold() in credentials
            or slot.casefold() in credentials
        ):
            raise _error(
                "non-secret source parameters outside credential slots",
                "sensitive parameter declaration",
            )
    return names


class SourceBindingScopes:
    """One private Session source factory's isolated authoring scopes."""

    __slots__ = ("_active", "_entities")

    def __init__(self, entities: tuple[TargetEntityContract, ...]) -> None:
        if type(entities) is not tuple:
            raise _error("immutable normalized Entity authority", "mutable Entity collection")
        self._initialize(
            tuple(
                _BindingDeclaration(entity.ref, entity.source, entity.credential_slots)
                for entity in entities
            )
        )

    @classmethod
    def from_registry(cls, registry: Registry) -> SourceBindingScopes:
        """Snapshot parameter declarations without validating unrelated identity facts."""
        declarations: list[_BindingDeclaration] = []
        for entity in registry.entities.values():
            datasource = registry.datasources.get(entity.datasource)
            declarations.append(
                _BindingDeclaration(
                    RefPayloadV1("marivo.semantic_ref/v1", SemanticKind.ENTITY, entity.semantic_id),
                    _snapshot_target_source(entity.source),
                    tuple(sorted(datasource.env_refs)) if datasource is not None else (),
                )
            )
        instance = cls.__new__(cls)
        instance._initialize(tuple(declarations))
        return instance

    def _initialize(self, declarations: tuple[_BindingDeclaration, ...]) -> None:
        paths = tuple(entity.ref.path for entity in declarations)
        if len(set(paths)) != len(paths):
            raise _error("unique current Entity definitions", "duplicate Entity authority")
        self._entities = declarations
        self._active: ContextVar[tuple[BoundSourceParametersV1, ...]] = ContextVar(
            "marivo_private_source_bindings", default=()
        )

    @contextmanager
    def scope(self, bindings: SourceBindingMap) -> Iterator[None]:
        """Replace the complete active map and restore it even after an exception."""
        if not isinstance(bindings, Mapping):
            raise _error("exact Entity-to-parameter mapping", "invalid binding mapping")
        normalized: list[BoundSourceParametersV1] = []
        entities = {entity.ref.path: entity for entity in self._entities}
        for entity_ref, values in bindings.items():
            if type(entity_ref) is not Ref or entity_ref.kind is not SemanticKind.ENTITY:
                raise _error("exact current Entity ref key", "invalid binding Entity key")
            entity = entities.get(entity_ref.path)
            if entity is None:
                raise _error(
                    "Entity in this source factory's current authority", "unknown binding Entity"
                )
            names = _required(entity)
            if not names:
                raise _error("parameterized JSON Entity", "Entity has no declared JSON parameters")
            if not isinstance(values, Mapping):
                raise _error("declared parameter mapping", "invalid Entity binding values")
            if any(type(name) is not str for name in values):
                raise _error("exact string parameter names", "invalid parameter name type")
            supplied_names = set(values)
            if supplied_names != set(names):
                raise _error(
                    "exact declared parameters: " + ", ".join(names),
                    f"missing {len(set(names) - supplied_names)}; extra {len(supplied_names - set(names))}",
                )
            captured = tuple(_value(values[name]) for name in names)
            normalized.append(
                BoundSourceParametersV1(
                    _token=_TOKEN,
                    entity_ref=entity_ref,
                    ordered_parameter_names=names,
                    private_canonical_typed_values=captured,
                    exact_value_digest=_binding_digest(entity_ref, names, captured),
                )
            )
        token = self._active.set(tuple(sorted(normalized, key=lambda item: item.entity_ref.path)))
        try:
            yield None
        finally:
            self._active.reset(token)

    def capture(
        self, entities: tuple[TargetEntityContract, ...]
    ) -> tuple[BoundSourceParametersV1, ...]:
        """Capture only reachable parameterized Entities into an immutable source."""
        if type(entities) is not tuple:
            raise _error("immutable reachable Entity definitions", "invalid capture authority")
        authority = {entity.ref.path: entity for entity in self._entities}
        active = {binding.entity_ref.path: binding for binding in self._active.get()}
        captured: list[BoundSourceParametersV1] = []
        seen: set[str] = set()
        for entity in entities:
            path = entity.ref.path
            declaration = authority.get(path)
            if declaration != _BindingDeclaration(
                entity.ref, entity.source, entity.credential_slots
            ):
                raise _error(
                    "exact normalized current Entity definition", "foreign capture authority"
                )
            assert declaration is not None
            names = _required(declaration)
            if not names or path in seen:
                continue
            seen.add(path)
            binding = active.get(path)
            if binding is None:
                raise _error(
                    "active complete bindings for every reachable parameterized Entity",
                    "missing reachable source bindings",
                )
            if binding.ordered_parameter_names != names or binding.entity_ref.path != path:
                raise _error("exact declared source binding", "changed parameter declaration")
            binding._validate()
            captured.append(binding)
        return tuple(sorted(captured, key=lambda item: item.entity_ref.path))
