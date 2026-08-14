"""Immutable public and private ontology value contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, SupportsIndex, TypeAlias, cast, final

from marivo._compat import Never, Self
from marivo.refs import EntityKind, MeasureKind, MetricKind, Ref, RefPayloadV1
from marivo.semantic.ir import AiContextIR, SourceLocation

OntologyEndpointKind: TypeAlias = EntityKind | MeasureKind | MetricKind
OntologyEndpointRef: TypeAlias = Ref[OntologyEndpointKind]
OntologyOutcomeRef: TypeAlias = Ref[EntityKind | MetricKind]
SemanticEdgeRelation: TypeAlias = Literal["influences", "related_to"]


@final
class SemanticEdgeRef:
    """Sealed identity for one authored ontology assertion."""

    __slots__ = ("kind", "path")

    kind: Literal["semantic_edge"]
    path: str

    def __new__(cls, *args: object, **kwargs: object) -> Self:
        del cls, args, kwargs
        raise TypeError(
            "SemanticEdgeRef has no public constructor; use mo.influences or mo.related_to"
        )

    def __init__(self, _sealed: Never, /) -> None:
        raise AssertionError("SemanticEdgeRef initialization is unreachable")

    def __init_subclass__(cls, **kwargs: object) -> Never:
        del cls, kwargs
        raise TypeError("SemanticEdgeRef is sealed and cannot be subclassed")

    def __setattr__(self, name: str, value: object) -> Never:
        del name, value
        raise AttributeError("SemanticEdgeRef instances are immutable")

    @property
    def key(self) -> str:
        return f"semantic_edge:{self.path}"

    def to_dict(self) -> dict[str, str]:
        """Return the one public serialized ontology-ref representation."""
        return {
            "schema": "marivo.ontology_ref/v1",
            "kind": "semantic_edge",
            "path": self.path,
        }

    def __str__(self) -> str:
        return self.key

    def __repr__(self) -> str:
        return f"SemanticEdgeRef({self.key})"

    def __eq__(self, other: object) -> bool:
        return type(other) is SemanticEdgeRef and self.path == other.path

    def __hash__(self) -> int:
        return hash(("semantic_edge", self.path))

    def __copy__(self) -> SemanticEdgeRef:
        return self

    def __deepcopy__(self, memo: dict[int, object]) -> SemanticEdgeRef:
        memo[id(self)] = self
        return self

    def __reduce_ex__(self, protocol: SupportsIndex) -> tuple[object, tuple[object, ...]]:
        del protocol
        return (_restore_semantic_edge_ref, (self.to_dict(),))

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: object, handler: object) -> Any:
        del cls, source_type, handler
        from pydantic_core import core_schema

        def validate_python(value: object) -> SemanticEdgeRef:
            if type(value) is not SemanticEdgeRef:
                raise ValueError(f"expected exact SemanticEdgeRef; received {type(value).__name__}")
            return value

        def validate_json(value: dict[str, object]) -> SemanticEdgeRef:
            return _restore_semantic_edge_ref(value)

        def serialize(value: SemanticEdgeRef, info: object) -> object:
            return value.to_dict() if getattr(info, "mode", "python") == "json" else value

        payload = core_schema.typed_dict_schema(
            {
                "schema": core_schema.typed_dict_field(
                    core_schema.literal_schema(["marivo.ontology_ref/v1"]), required=True
                ),
                "kind": core_schema.typed_dict_field(
                    core_schema.literal_schema(["semantic_edge"]), required=True
                ),
                "path": core_schema.typed_dict_field(core_schema.str_schema(), required=True),
            },
            extra_behavior="forbid",
            total=True,
        )
        return core_schema.json_or_python_schema(
            json_schema=core_schema.no_info_after_validator_function(validate_json, payload),
            python_schema=core_schema.no_info_plain_validator_function(validate_python),
            serialization=core_schema.plain_serializer_function_ser_schema(
                serialize, info_arg=True, when_used="always"
            ),
        )


def _make_semantic_edge_ref(path: str) -> SemanticEdgeRef:
    value = object.__new__(SemanticEdgeRef)
    object.__setattr__(value, "kind", "semantic_edge")
    object.__setattr__(value, "path", path)
    return value


def _restore_semantic_edge_ref(payload: object) -> SemanticEdgeRef:
    if type(payload) is not dict:
        raise ValueError("ontology ref payload must be an object")
    value = cast("dict[str, object]", payload)
    if set(value) != {"schema", "kind", "path"}:
        raise ValueError("ontology ref payload fields must be exactly schema, kind, path")
    if value["schema"] != "marivo.ontology_ref/v1" or value["kind"] != "semantic_edge":
        raise ValueError("ontology ref payload has an unsupported schema or kind")
    path = value["path"]
    if type(path) is not str or not path:
        raise ValueError("ontology ref path must be a non-empty string")
    return _make_semantic_edge_ref(path)


@dataclass(frozen=True, slots=True)
class SemanticEdgeIR:
    """Private normalized assertion retained by an OntologyCatalog."""

    ref: SemanticEdgeRef
    relation: SemanticEdgeRelation
    source: OntologyEndpointRef
    target: OntologyEndpointRef
    context: AiContextIR
    location: SourceLocation

    def canonical_payload(self) -> dict[str, object]:
        return {
            "ref": self.ref.to_dict(),
            "relation": self.relation,
            "source": RefPayloadV1.from_ref(self.source).to_dict(),
            "target": RefPayloadV1.from_ref(self.target).to_dict(),
            "context": {
                "business_definition": self.context.business_definition,
                "guardrails": list(self.context.guardrails),
            },
        }
