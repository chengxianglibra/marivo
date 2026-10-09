"""Closed graph-local value tables, independent of method and Store semantics."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Generic, Literal, NoReturn, TypeAlias, TypeVar, cast

from pydantic import JsonValue, TypeAdapter
from pydantic_core import (
    PydanticSerializationError,
    SchemaSerializer,
    SchemaValidator,
    core_schema,
)

from marivo.analysis.core.model import Binding, DomainSignature, Evidence, Fact
from marivo.analysis.materialization.contracts import canonical_json

MAX_VALUE_RECORDS = 16384
MAX_VALUE_REFERENCES = 65536

ValueKind: TypeAlias = Literal["binding", "domain", "fact", "evidence"]
ContractValue: TypeAlias = Binding | DomainSignature | Fact | Evidence
JsonObject: TypeAlias = dict[str, JsonValue]
T = TypeVar("T")
KINDS: tuple[ValueKind, ...] = ("binding", "domain", "fact", "evidence")
TYPES: dict[ValueKind, type[ContractValue]] = {
    "binding": Binding,
    "domain": DomainSignature,
    "fact": Fact,
    "evidence": Evidence,
}


def _invalid(message: str) -> NoReturn:
    from marivo.analysis.materialization.graph_protocol import invalid

    raise invalid(message)


def _json(value: object) -> JsonValue:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, list):
        return [_json(item) for item in value]
    if isinstance(value, dict):
        result: JsonObject = {}
        for key, item in value.items():
            if not isinstance(key, str):
                _invalid("non-string graph value key")
            result[key] = _json(item)
        return result
    _invalid("non-JSON graph value")


def _object(value: object) -> JsonObject:
    checked = _json(value)
    if not isinstance(checked, dict):
        _invalid("graph value must be an object")
    return checked


@dataclass(frozen=True, slots=True)
class ValueTables:
    bindings: tuple[JsonObject, ...]
    domains: tuple[JsonObject, ...]
    facts: tuple[JsonObject, ...]
    evidence: tuple[JsonObject, ...]

    def ordered(self) -> dict[ValueKind, tuple[JsonObject, ...]]:
        return dict(
            zip(KINDS, (self.bindings, self.domains, self.facts, self.evidence), strict=True)
        )


@dataclass(frozen=True, slots=True)
class WireDocument:
    schema: Literal["marivo.analysis.graph_dag/v6"]
    root: str
    nodes: tuple[JsonObject, ...]
    tables: ValueTables


WIRE = TypeAdapter(WireDocument)


def _reference_budget(document: WireDocument) -> None:
    pending: list[JsonValue] = [*document.nodes]
    for entries in document.tables.ordered().values():
        pending.extend(entries)
    count = 0
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if "$ref" in value:
                count += 1
                if count > MAX_VALUE_REFERENCES:
                    _invalid("definition value reference budget exceeded")
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)


class _Writer:
    def __init__(self) -> None:
        self.entries: dict[ValueKind, list[JsonObject]] = {kind: [] for kind in KINDS}
        self.indices: dict[ValueKind, dict[str, int]] = {kind: {} for kind in KINDS}
        self.content_memo: dict[ValueKind, dict[bytes, JsonObject]] = {kind: {} for kind in KINDS}
        self.memo: dict[int, JsonObject] = {}
        self.count = 0

    def write(
        self, value: object, handler: core_schema.SerializerFunctionWrapHandler, kind: ValueKind
    ) -> JsonObject:
        prior = self.memo.get(id(value))
        if prior is not None:
            return prior
        # Exact native bytes are a fast positive cache for separately decoded
        # equal values. Canonical pooled content remains the interning authority.
        native_key = VALUE_SERIALIZERS[kind].to_json(value, warnings="error")
        prior = self.content_memo[kind].get(native_key)
        if prior is not None:
            self.memo[id(value)] = prior
            return prior
        payload = _object(handler(value))
        key = canonical_json(payload)
        index = self.indices[kind].get(key)
        if index is None:
            self.count += 1
            if self.count > MAX_VALUE_RECORDS:
                _invalid("definition value record budget exceeded")
            index = len(self.entries[kind])
            self.indices[kind][key] = index
            self.entries[kind].append(payload)
        reference: JsonObject = {"$ref": {"kind": kind, "index": index}}
        self.memo[id(value)] = reference
        self.content_memo[kind][native_key] = reference
        return reference

    def tables(self) -> ValueTables:
        return ValueTables(*(tuple(self.entries[kind]) for kind in KINDS))


class _Reader:
    def __init__(self, tables: ValueTables) -> None:
        self.raw = tables.ordered()
        if sum(len(entries) for entries in self.raw.values()) > MAX_VALUE_RECORDS:
            _invalid("definition value record budget exceeded")
        self.values: dict[ValueKind, list[ContractValue]] = {kind: [] for kind in KINDS}

    def prepare(self) -> None:
        for kind in KINDS:
            for payload in self.raw[kind]:
                value: object = VALUE_VALIDATORS[kind].validate_json(
                    canonical_json(payload), strict=True, context=self
                )
                if (
                    not isinstance(value, (Binding, DomainSignature, Fact, Evidence))
                    or type(value) is not TYPES[kind]
                ):
                    _invalid("value table entry has the wrong contract type")
                self.values[kind].append(value)

    def read(self, kind: ValueKind, index: int) -> ContractValue:
        # Only already constructed lower-level tables are visible. Same-table and
        # forward references cannot substitute a root entry or construct a cycle.
        if not 0 <= index < len(self.values[kind]):
            _invalid("missing, forward or cyclic definition value reference")
        return self.values[kind][index]


def _adapt_schema(schema: core_schema.CoreSchema) -> core_schema.CoreSchema:
    def rewrite(value: object) -> object:
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, tuple):
            return tuple(rewrite(item) for item in value)
        if not isinstance(value, dict):
            return value
        result = {key: rewrite(item) for key, item in value.items()}
        if result.get("type") != "dataclass":
            return result
        kind = next((kind for kind in KINDS if result.get("cls") is TYPES[kind]), None)
        if kind is None:
            return result
        schema_ref = result.pop("ref", None)
        if schema_ref is not None and not isinstance(schema_ref, str):
            raise TypeError("Pydantic schema reference must be a string")

        def serialize(
            item: object,
            handler: core_schema.SerializerFunctionWrapHandler,
            info: core_schema.SerializationInfo,
        ) -> JsonObject:
            if not isinstance(info.context, _Writer):
                raise TypeError("graph value serialization requires its local writer")
            return info.context.write(item, handler, kind)

        def resolve(item: JsonObject, info: core_schema.ValidationInfo) -> ContractValue:
            if not isinstance(info.context, _Reader):
                raise TypeError("graph value validation requires its local reader")
            reference = item["$ref"]
            assert isinstance(reference, dict)
            index = reference["index"]
            assert type(index) is int
            return info.context.read(kind, index)

        reference = core_schema.typed_dict_schema(
            {
                "$ref": core_schema.typed_dict_field(
                    core_schema.typed_dict_schema(
                        {
                            "kind": core_schema.typed_dict_field(
                                core_schema.literal_schema([kind])
                            ),
                            "index": core_schema.typed_dict_field(
                                core_schema.int_schema(strict=True, ge=0)
                            ),
                        },
                        extra_behavior="forbid",
                    )
                )
            },
            extra_behavior="forbid",
        )
        # This cast is limited to a generated Pydantic dataclass schema, after
        # selecting its exact class. It never admits untrusted metadata as a schema.
        native = cast("core_schema.CoreSchema", result)
        return core_schema.json_or_python_schema(
            json_schema=core_schema.union_schema(
                [core_schema.with_info_after_validator_function(resolve, reference), native],
                mode="left_to_right",
            ),
            python_schema=native,
            ref=schema_ref,
            serialization=core_schema.wrap_serializer_function_ser_schema(serialize, info_arg=True),
        )

    # The rewrite preserves the generated schema envelope and changes only the
    # four selected dataclasses; CoreSchema's union cannot express this traversal.
    return cast("core_schema.CoreSchema", rewrite(schema))


VALUE_SCHEMAS = {kind: TypeAdapter(TYPES[kind]).core_schema for kind in KINDS}
VALUE_SERIALIZERS = {kind: SchemaSerializer(schema) for kind, schema in VALUE_SCHEMAS.items()}
VALUE_VALIDATORS = {
    kind: SchemaValidator(_adapt_schema(schema)) for kind, schema in VALUE_SCHEMAS.items()
}


class GraphValueCodec(Generic[T]):
    """Encode one graph record family using the four closed contract value tables."""

    def __init__(self, document_type: type[T]) -> None:
        self.document_type = document_type
        schema = _adapt_schema(TypeAdapter(document_type).core_schema)
        self.serializer = SchemaSerializer(schema)
        self.validator = SchemaValidator(schema)

    def encode(self, document: T) -> str:
        writer = _Writer()
        try:
            payload = _object(
                self.serializer.to_python(document, mode="json", context=writer, warnings="error")
            )
        except PydanticSerializationError:
            _invalid(
                "definition value record budget exceeded"
                if writer.count > MAX_VALUE_RECORDS
                else "invalid graph value serialization"
            )
        root, nodes = payload["root"], payload["nodes"]
        if not isinstance(root, str) or not isinstance(nodes, list):
            _invalid("invalid serialized graph envelope")
        wire = WireDocument(
            "marivo.analysis.graph_dag/v6",
            root,
            tuple(_object(node) for node in nodes),
            writer.tables(),
        )
        _reference_budget(wire)
        return canonical_json(json.loads(WIRE.dump_json(wire, warnings="error")))

    def decode(self, text: str) -> T:
        wire = WIRE.validate_json(text, strict=True)
        if canonical_json(json.loads(WIRE.dump_json(wire, warnings="error"))) != text:
            _invalid("noncanonical, missing or extra graph value metadata")
        _reference_budget(wire)
        reader = _Reader(wire.tables)
        reader.prepare()
        result: object = self.validator.validate_json(
            canonical_json({"schema": wire.schema, "root": wire.root, "nodes": wire.nodes}),
            strict=True,
            context=reader,
        )
        if not isinstance(result, self.document_type):
            _invalid("invalid restored graph record family")
        if self.encode(result) != text:
            _invalid("noncanonical, duplicate, unused or inline graph values")
        return result
