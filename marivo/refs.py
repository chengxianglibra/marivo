"""Dependency-neutral semantic identities shared by every Marivo layer."""

from __future__ import annotations

import re
import types
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import (
    Any,
    Literal,
    Never,
    Self,
    SupportsIndex,
    cast,
    final,
    get_args,
    get_origin,
)


class SemanticKind(StrEnum):
    """Closed runtime kind registry for every semantic identity."""

    DOMAIN = "domain"
    DATASOURCE = "datasource"
    ENTITY = "entity"
    DIMENSION = "dimension"
    MEASURE = "measure"
    TIME_DIMENSION = "time_dimension"
    METRIC = "metric"
    RELATIONSHIP = "relationship"
    EVENT = "event"


class SemanticKindTag:
    """Private static marker base for ``Ref`` generic parameters."""

    __slots__ = ()


class DomainKind(SemanticKindTag):
    __slots__ = ()


class DatasourceKind(SemanticKindTag):
    __slots__ = ()


class EntityKind(SemanticKindTag):
    __slots__ = ()


class DimensionKind(SemanticKindTag):
    __slots__ = ()


class TimeDimensionKind(SemanticKindTag):
    __slots__ = ()


class MeasureKind(SemanticKindTag):
    __slots__ = ()


class MetricKind(SemanticKindTag):
    __slots__ = ()


class RelationshipKind(SemanticKindTag):
    __slots__ = ()


class EventKind(SemanticKindTag):
    __slots__ = ()


type FieldKind = DimensionKind | TimeDimensionKind | MeasureKind


_KIND_BY_MARKER: dict[type[SemanticKindTag], frozenset[SemanticKind]] = {
    DomainKind: frozenset({SemanticKind.DOMAIN}),
    DatasourceKind: frozenset({SemanticKind.DATASOURCE}),
    EntityKind: frozenset({SemanticKind.ENTITY}),
    DimensionKind: frozenset({SemanticKind.DIMENSION}),
    TimeDimensionKind: frozenset({SemanticKind.TIME_DIMENSION}),
    MeasureKind: frozenset({SemanticKind.MEASURE}),
    MetricKind: frozenset({SemanticKind.METRIC}),
    RelationshipKind: frozenset({SemanticKind.RELATIONSHIP}),
    EventKind: frozenset({SemanticKind.EVENT}),
}

_SEGMENT_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SEGMENT_COUNT = {
    SemanticKind.DOMAIN: 1,
    SemanticKind.DATASOURCE: 1,
    SemanticKind.ENTITY: 2,
    SemanticKind.DIMENSION: 3,
    SemanticKind.TIME_DIMENSION: 3,
    SemanticKind.MEASURE: 3,
    SemanticKind.METRIC: 2,
    SemanticKind.RELATIONSHIP: 2,
    SemanticKind.EVENT: 2,
}


def _validate_segment(value: object, *, role: str = "semantic path segment") -> str:
    if type(value) is not str or not _SEGMENT_RE.fullmatch(value):
        raise ValueError(
            f"{role} must match [a-z][a-z0-9_]*; received {value!r}. "
            "Use a lowercase snake_case name."
        )
    return value


def _validate_ref_path(kind: SemanticKind, path: object) -> str:
    if type(kind) is not SemanticKind:
        raise TypeError(f"ref kind must be SemanticKind; received {type(kind).__name__}")
    if type(path) is not str:
        raise TypeError(f"{kind.value} ref path must be str; received {type(path).__name__}")
    if path != path.strip():
        raise ValueError(
            f"{kind.value} ref path must not contain surrounding whitespace; received {path!r}"
        )
    parts = path.split(".")
    expected = _SEGMENT_COUNT[kind]
    if len(parts) != expected:
        raise ValueError(
            f"{kind.value} ref path must contain exactly {expected} segment"
            f"{'s' if expected != 1 else ''}; received {path!r}"
        )
    for part in parts:
        _validate_segment(part, role=f"{kind.value} ref path segment")
    return path


@final
class Ref[KindT: SemanticKindTag]:
    """Sealed semantic identity created only by an exact kind factory."""

    __slots__ = ("kind", "path")

    kind: SemanticKind
    path: str

    def __new__(cls, *args: object, **kwargs: object) -> Self:
        del cls, args, kwargs
        raise TypeError(
            "Ref has no public raw constructor; use marivo.semantic.ref.metric(...), "
            "marivo.semantic.ref.dimension(...), or another exact kind factory."
        )

    def __init__(self, _sealed: Never, /) -> None:
        raise AssertionError("Ref initialization is unreachable")

    def __init_subclass__(cls, **kwargs: object) -> Never:
        del cls, kwargs
        raise TypeError("Ref is sealed and cannot be subclassed.")

    def __setattr__(self, name: str, value: object) -> Never:
        del name, value
        raise AttributeError("Ref instances are immutable")

    @property
    def key(self) -> str:
        return f"{self.kind.value}:{self.path}"

    @property
    def name(self) -> str:
        return self.path.rsplit(".", 1)[-1]

    def __str__(self) -> str:
        return self.key

    def __repr__(self) -> str:
        return f"Ref[{self.kind.value}]({self.key})"

    def __eq__(self, other: object) -> bool:
        if type(other) is not Ref:
            return False
        ref = cast("Ref[SemanticKindTag]", other)
        return self.kind is ref.kind and self.path == ref.path

    def __hash__(self) -> int:
        return hash((self.kind, self.path))

    def __copy__(self) -> Ref[KindT]:
        return self

    def __deepcopy__(self, memo: dict[int, object]) -> Ref[KindT]:
        memo[id(self)] = self
        return self

    def __reduce_ex__(self, protocol: SupportsIndex) -> tuple[object, tuple[object, ...]]:
        del protocol
        return (_restore_ref_payload, (RefPayloadV1.from_ref(self),))

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        source_type: object,
        handler: object,
    ) -> Any:
        del cls, handler
        from pydantic_core import core_schema

        allowed_kinds = _allowed_kinds_for_annotation(source_type)
        allowed_values = [kind.value for kind in sorted(allowed_kinds, key=lambda item: item.value)]

        def validate_python(value: object) -> Ref[SemanticKindTag]:
            if type(value) is not Ref:
                raise ValueError(f"expected exact Ref value; received {type(value).__name__}")
            ref = cast("Ref[SemanticKindTag]", value)
            if ref.kind not in allowed_kinds:
                expected = ", ".join(allowed_values)
                raise ValueError(f"expected Ref kind in {{{expected}}}; received {ref.kind.value}")
            return ref

        def validate_json(value: dict[str, object]) -> Ref[SemanticKindTag]:
            ref = _decode_ref_payload(value)
            if ref.kind not in allowed_kinds:
                expected = ", ".join(allowed_values)
                raise ValueError(f"expected Ref kind in {{{expected}}}; received {ref.kind.value}")
            return ref

        def serialize(value: Ref[SemanticKindTag], info: object) -> object:
            mode = getattr(info, "mode", "python")
            if mode == "json":
                return {
                    "schema": "marivo.semantic_ref/v1",
                    "kind": value.kind.value,
                    "path": value.path,
                }
            return value

        json_payload_schema = core_schema.typed_dict_schema(
            {
                "schema": core_schema.typed_dict_field(
                    core_schema.literal_schema(["marivo.semantic_ref/v1"]),
                    required=True,
                ),
                "kind": core_schema.typed_dict_field(
                    core_schema.literal_schema(allowed_values),
                    required=True,
                ),
                "path": core_schema.typed_dict_field(
                    core_schema.str_schema(),
                    required=True,
                ),
            },
            extra_behavior="forbid",
            total=True,
        )
        return core_schema.json_or_python_schema(
            json_schema=core_schema.chain_schema(
                [
                    json_payload_schema,
                    core_schema.no_info_plain_validator_function(validate_json),
                ]
            ),
            python_schema=core_schema.no_info_plain_validator_function(validate_python),
            serialization=core_schema.plain_serializer_function_ser_schema(
                serialize,
                info_arg=True,
            ),
        )


def _create_ref(kind: SemanticKind, path: object) -> Ref[SemanticKindTag]:
    validated = _validate_ref_path(kind, path)
    value: Ref[SemanticKindTag] = object.__new__(Ref)
    object.__setattr__(value, "kind", kind)
    object.__setattr__(value, "path", validated)
    return value


@final
class _RefFactory:
    """Exact semantic-ref factories kept separate from immutable identities."""

    __slots__ = ()

    def domain(self, path: str) -> Ref[DomainKind]:
        return cast("Ref[DomainKind]", _create_ref(SemanticKind.DOMAIN, path))

    def datasource(self, path: str) -> Ref[DatasourceKind]:
        return cast("Ref[DatasourceKind]", _create_ref(SemanticKind.DATASOURCE, path))

    def entity(self, path: str) -> Ref[EntityKind]:
        return cast("Ref[EntityKind]", _create_ref(SemanticKind.ENTITY, path))

    def dimension(self, path: str) -> Ref[DimensionKind]:
        return cast("Ref[DimensionKind]", _create_ref(SemanticKind.DIMENSION, path))

    def time_dimension(self, path: str) -> Ref[TimeDimensionKind]:
        return cast(
            "Ref[TimeDimensionKind]",
            _create_ref(SemanticKind.TIME_DIMENSION, path),
        )

    def measure(self, path: str) -> Ref[MeasureKind]:
        return cast("Ref[MeasureKind]", _create_ref(SemanticKind.MEASURE, path))

    def metric(self, path: str) -> Ref[MetricKind]:
        return cast("Ref[MetricKind]", _create_ref(SemanticKind.METRIC, path))

    def relationship(self, path: str) -> Ref[RelationshipKind]:
        return cast(
            "Ref[RelationshipKind]",
            _create_ref(SemanticKind.RELATIONSHIP, path),
        )

    def event(self, path: str) -> Ref[EventKind]:
        return cast("Ref[EventKind]", _create_ref(SemanticKind.EVENT, path))


ref = _RefFactory()


@dataclass(frozen=True, slots=True)
class RefPayloadV1:
    """Internal versioned wire payload for one exact semantic ref."""

    schema: Literal["marivo.semantic_ref/v1"]
    kind: SemanticKind
    path: str

    def __post_init__(self) -> None:
        if self.schema != "marivo.semantic_ref/v1":
            raise ValueError(
                f"ref payload schema must be 'marivo.semantic_ref/v1'; received {self.schema!r}"
            )
        if type(self.kind) is not SemanticKind:
            raise TypeError(
                f"ref payload kind must be SemanticKind; received {type(self.kind).__name__}"
            )
        _validate_ref_path(self.kind, self.path)

    @classmethod
    def from_ref(cls, ref: Ref[SemanticKindTag]) -> RefPayloadV1:
        if type(ref) is not Ref:
            raise TypeError(f"expected exact Ref value; received {type(ref).__name__}")
        return cls(
            schema="marivo.semantic_ref/v1",
            kind=ref.kind,
            path=ref.path,
        )

    def to_dict(self) -> dict[str, str]:
        """Return the canonical JSON object for this persisted ref."""
        return {
            "schema": self.schema,
            "kind": self.kind.value,
            "path": self.path,
        }


def _allowed_kinds_for_annotation(source_type: object) -> frozenset[SemanticKind]:
    if get_origin(source_type) is not Ref:
        raise TypeError("Ref must be parameterized with one exact semantic kind marker")
    arguments = get_args(source_type)
    if len(arguments) != 1:
        raise TypeError("Ref must be parameterized with one exact semantic kind marker")

    def collect(marker: object) -> set[SemanticKind]:
        if marker in _KIND_BY_MARKER:
            return set(_KIND_BY_MARKER[marker])
        if get_origin(marker) is types.UnionType:
            kinds: set[SemanticKind] = set()
            for member in get_args(marker):
                kinds.update(collect(member))
            return kinds
        raise TypeError(f"unsupported Ref kind marker {marker!r}")

    allowed = frozenset(collect(arguments[0]))
    if not allowed:
        raise TypeError("Ref kind marker must resolve to at least one semantic kind")
    return allowed


_FACTORY_BY_KIND: dict[SemanticKind, Callable[[str], Ref[SemanticKindTag]]] = {
    SemanticKind.DOMAIN: cast("Callable[[str], Ref[SemanticKindTag]]", ref.domain),
    SemanticKind.DATASOURCE: cast("Callable[[str], Ref[SemanticKindTag]]", ref.datasource),
    SemanticKind.ENTITY: cast("Callable[[str], Ref[SemanticKindTag]]", ref.entity),
    SemanticKind.DIMENSION: cast("Callable[[str], Ref[SemanticKindTag]]", ref.dimension),
    SemanticKind.TIME_DIMENSION: cast(
        "Callable[[str], Ref[SemanticKindTag]]",
        ref.time_dimension,
    ),
    SemanticKind.MEASURE: cast("Callable[[str], Ref[SemanticKindTag]]", ref.measure),
    SemanticKind.METRIC: cast("Callable[[str], Ref[SemanticKindTag]]", ref.metric),
    SemanticKind.RELATIONSHIP: cast(
        "Callable[[str], Ref[SemanticKindTag]]",
        ref.relationship,
    ),
    SemanticKind.EVENT: cast("Callable[[str], Ref[SemanticKindTag]]", ref.event),
}


def _decode_ref_payload(
    value: RefPayloadV1 | Mapping[str, object],
) -> Ref[SemanticKindTag]:
    if type(value) is RefPayloadV1:
        payload = value
    else:
        if type(value) is not dict:
            raise TypeError(f"ref payload must be an exact object; received {type(value).__name__}")
        if set(value) != {"schema", "kind", "path"}:
            raise ValueError("ref payload must contain exactly schema, kind, and path")
        schema = value["schema"]
        kind_value = value["kind"]
        path = value["path"]
        if schema != "marivo.semantic_ref/v1":
            raise ValueError(
                f"ref payload schema must be 'marivo.semantic_ref/v1'; received {schema!r}"
            )
        if type(kind_value) is not str:
            raise TypeError(f"ref payload kind must be str; received {type(kind_value).__name__}")
        try:
            kind = SemanticKind(kind_value)
        except ValueError as exc:
            raise ValueError(f"unsupported semantic ref kind {kind_value!r}") from exc
        if type(path) is not str:
            raise TypeError(f"ref payload path must be str; received {type(path).__name__}")
        payload = RefPayloadV1(
            schema="marivo.semantic_ref/v1",
            kind=kind,
            path=path,
        )
    return _FACTORY_BY_KIND[payload.kind](payload.path)


def _decode_ref_key(value: object) -> Ref[SemanticKindTag]:
    if type(value) is not str:
        raise TypeError(f"ref key must be str; received {type(value).__name__}")
    kind_value, separator, path = value.partition(":")
    if not separator:
        raise ValueError("ref key must be '<kind>:<path>'")
    try:
        kind = SemanticKind(kind_value)
    except ValueError as exc:
        raise ValueError(f"unsupported semantic ref kind {kind_value!r}") from exc
    return _FACTORY_BY_KIND[kind](path)


def _restore_ref_payload(payload: RefPayloadV1) -> Ref[SemanticKindTag]:
    return _decode_ref_payload(payload)
