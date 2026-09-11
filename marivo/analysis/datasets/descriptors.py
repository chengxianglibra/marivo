"""Closed immutable descriptors shared by private lazy Dataset families."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import InitVar, dataclass, field, fields, is_dataclass
from typing import Literal, TypeAlias

from marivo._compat import Never
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.refs import EntityKind, Ref, SemanticKind

_CORE_TOKEN = object()
_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,159}\Z")
_TRUSTED_CLASSES: set[type[_Descriptor]] = set()
_CanonicalValue: TypeAlias = None | bool | int | float | str | tuple["_CanonicalValue", ...]


def _fail(expected: str, received: str, location: str) -> Never:
    raise DatasetConstructionError(
        expected=expected,
        received=received,
        location=location,
        repair="Reconstruct this value through its owning Dataset family with the exact registered contract.",
    )


def _is_stable_identifier(value: object) -> bool:
    return type(value) is str and _ID_PATTERN.fullmatch(value) is not None


def _bool_tuple_arity(logical_type_id: str) -> int | None:
    """Read the exact positive arity of the registered boolean-tuple primitive."""
    match = re.fullmatch(r"bool_tuple:([1-9][0-9]*)", logical_type_id)
    return None if match is None else int(match[1])


def _bool_tuple_value(value: object, *, arity: int | None = None) -> tuple[bool, ...] | None:
    """Normalize exact booleans; callers own container admission and typed errors."""
    if (
        not isinstance(value, (tuple, list))
        or (arity is not None and len(value) != arity)
        or any(type(item) is not bool for item in value)
    ):
        return None
    return tuple(item for item in value if isinstance(item, bool))


def _stable_text(value: str, location: str) -> None:
    if not _is_stable_identifier(value):
        _fail("a bounded canonical stable identifier", type(value).__name__, location)


def _positive(value: int, location: str, *, zero: bool = False) -> None:
    if type(value) is not int or value < (0 if zero else 1):
        _fail("a non-negative integer" if zero else "a positive integer", str(value), location)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class _Descriptor:
    _token: InitVar[object]

    def __post_init__(self, _token: object) -> None:
        if _token is not _CORE_TOKEN:
            _fail(
                "a helper-produced immutable descriptor", "direct construction", type(self).__name__
            )
        if type(self) in (
            DatasetFieldIdentity,
            DatasetPhysicalTypeState,
            DatasetRowBound,
            DatasetCardinality,
            DatasetOrdering,
            DatasetFamilyRowSemantics,
            DatasetByteCount,
        ):
            _fail("a closed descriptor variant", "abstract descriptor", type(self).__name__)

    def __init_subclass__(cls, *, _token: object | None = None) -> None:
        # dataclass(slots=True) creates one replacement class. Recognize that
        # exact reconstruction by the field map shared with its trusted source,
        # never by a caller-controlled module or qualified name alone.
        own_fields: object = cls.__dict__.get("__dataclass_fields__")
        slot_replacement = own_fields is not None and any(
            trusted.__module__ == cls.__module__
            and trusted.__name__ == cls.__name__
            and trusted.__dict__.get("__dataclass_fields__") is own_fields
            for trusted in _TRUSTED_CLASSES
        )
        if _token is not _CORE_TOKEN and not slot_replacement:
            _fail("a Core-owned descriptor variant", "unregistered subclass", cls.__name__)
        _TRUSTED_CLASSES.add(cls)

    def __repr__(self) -> str:
        kind = getattr(self, "kind", None)
        identity = getattr(self, "value", None)
        detail = f"kind={kind}" if isinstance(kind, str) else "immutable"
        if isinstance(identity, str):
            detail = f"id={identity[:80]}"
        return f"<{type(self).__name__.lstrip('_')} {detail}>"


@dataclass(frozen=True, slots=True, repr=False)
class _StableIdRegistry:
    """One immutable closed vocabulary supplied by an owning private registry."""

    families: frozenset[str] = frozenset()
    shapes: frozenset[tuple[str, str, int]] = frozenset()
    roles: frozenset[str] = frozenset()
    logical_types: frozenset[str] = frozenset()
    physical_types: frozenset[str] = frozenset()
    admitted_types: frozenset[str] = frozenset()
    physical_type_classes: frozenset[tuple[str, str]] = frozenset()
    policies: frozenset[str] = frozenset()
    value_orders: frozenset[str] = frozenset()
    byte_unavailable_reasons: frozenset[str] = frozenset()
    storage_kinds: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        for inventory in (
            self.families,
            self.roles,
            self.logical_types,
            self.physical_types,
            self.admitted_types,
            self.policies,
            self.value_orders,
            self.byte_unavailable_reasons,
            self.storage_kinds,
        ):
            if not isinstance(inventory, frozenset):
                _fail("an immutable stable-id inventory", type(inventory).__name__, "stable_ids")
            for value in inventory:
                _stable_text(value, "stable_ids")
        if not isinstance(self.physical_type_classes, frozenset):
            _fail(
                "an immutable physical-type admission inventory",
                type(self.physical_type_classes).__name__,
                "stable_ids.physical_type_classes",
            )
        for physical, admitted in self.physical_type_classes:
            _registered(physical, self.physical_types, "stable_ids.physical_type_classes")
            _registered(admitted, self.admitted_types, "stable_ids.physical_type_classes")
        if not isinstance(self.shapes, frozenset):
            _fail("an immutable shape inventory", type(self.shapes).__name__, "stable_ids.shapes")
        for family, local, version in self.shapes:
            _stable_text(family, "shape.family")
            _stable_text(local, "shape.local")
            _positive(version, "shape.version")
            _registered(family, self.families, "shape.family")


def _registered(value: str, inventory: frozenset[str], location: str) -> None:
    _stable_text(value, location)
    parameterized_tuple = "bool_tuple" in inventory and _bool_tuple_arity(value) is not None
    if value not in inventory and not parameterized_tuple:
        _fail("a registered stable id", value, location)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class DatasetShapeId(_Descriptor, _token=_CORE_TOKEN):
    family_id: str
    local_shape_id: str
    semantic_version: int

    def __str__(self) -> str:
        return f"{self.family_id}/{self.local_shape_id}@v{self.semantic_version}"

    def __repr__(self) -> str:
        return f"<DatasetShapeId {str(self)[:160]}>"


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class DatasetFieldId(_Descriptor, _token=_CORE_TOKEN):
    value: str


class DatasetFieldIdentity(_Descriptor, _token=_CORE_TOKEN):
    __slots__ = ()
    kind: Literal["catalog_ref", "runtime_metric", "generated", "entity_identity"]


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class _EntityFieldIdentity(DatasetFieldIdentity, _token=_CORE_TOKEN):
    entity_ref: Ref[EntityKind]
    identity_signature: tuple[tuple[str, str], ...]
    kind: Literal["entity_identity"] = field(default="entity_identity", init=False)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class _CatalogFieldIdentity(DatasetFieldIdentity, _token=_CORE_TOKEN):
    identity_id: str
    kind: Literal["catalog_ref"] = field(default="catalog_ref", init=False)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class _RuntimeMetricFieldIdentity(DatasetFieldIdentity, _token=_CORE_TOKEN):
    expression_fingerprint: str
    kind: Literal["runtime_metric"] = field(default="runtime_metric", init=False)

    @property
    def identity_id(self) -> str:
        return "runtime_metric:" + self.expression_fingerprint


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class _GeneratedFieldIdentity(DatasetFieldIdentity, _token=_CORE_TOKEN):
    producer_field_id: DatasetFieldId
    kind: Literal["generated"] = field(default="generated", init=False)


class DatasetPhysicalTypeState(_Descriptor, _token=_CORE_TOKEN):
    __slots__ = ()
    kind: Literal["resolved", "deferred"]


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class _ResolvedPhysicalType(DatasetPhysicalTypeState, _token=_CORE_TOKEN):
    physical_type_id: str
    kind: Literal["resolved"] = field(default="resolved", init=False)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class _DeferredPhysicalType(DatasetPhysicalTypeState, _token=_CORE_TOKEN):
    admitted_type_class_id: str
    kind: Literal["deferred"] = field(default="deferred", init=False)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class DatasetField(_Descriptor, _token=_CORE_TOKEN):
    field_id: DatasetFieldId
    name: str
    role_id: str
    identity: DatasetFieldIdentity
    derivation_identity: str
    logical_type_id: str
    physical_type_state: DatasetPhysicalTypeState
    nullable: bool

    def __repr__(self) -> str:
        return f"<DatasetField id={self.field_id.value[:64]} role={self.role_id[:32]}>"


class DatasetRowBound(_Descriptor, _token=_CORE_TOKEN):
    __slots__ = ()
    kind: Literal["unknown", "static", "runtime_policy"]


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class _UnknownRowBound(DatasetRowBound, _token=_CORE_TOKEN):
    kind: Literal["unknown"] = field(default="unknown", init=False)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class _StaticRowBound(DatasetRowBound, _token=_CORE_TOKEN):
    max_rows: int
    kind: Literal["static"] = field(default="static", init=False)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class _RuntimePolicyRowBound(DatasetRowBound, _token=_CORE_TOKEN):
    policy_id: str
    kind: Literal["runtime_policy"] = field(default="runtime_policy", init=False)


class DatasetCardinality(_Descriptor, _token=_CORE_TOKEN):
    __slots__ = ()
    kind: Literal["singleton", "keyed"]


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class _SingletonCardinality(DatasetCardinality, _token=_CORE_TOKEN):
    kind: Literal["singleton"] = field(default="singleton", init=False)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class _KeyedCardinality(DatasetCardinality, _token=_CORE_TOKEN):
    row_bound: DatasetRowBound
    kind: Literal["keyed"] = field(default="keyed", init=False)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class DatasetOrderTerm(_Descriptor, _token=_CORE_TOKEN):
    field_id: DatasetFieldId
    direction: Literal["ascending", "descending"]
    nulls: Literal["first", "last"]
    value_order_contract_id: str


class DatasetOrdering(_Descriptor, _token=_CORE_TOKEN):
    __slots__ = ()
    kind: Literal["unordered", "ordered"]


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class _UnorderedOrdering(DatasetOrdering, _token=_CORE_TOKEN):
    kind: Literal["unordered"] = field(default="unordered", init=False)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class _OrderedOrdering(DatasetOrdering, _token=_CORE_TOKEN):
    terms: tuple[DatasetOrderTerm, ...]
    kind: Literal["ordered"] = field(default="ordered", init=False)


class DatasetFamilyRowSemantics(_Descriptor, _token=_CORE_TOKEN):
    """Sealed extension seam for immutable family-owned row meaning variants."""

    __slots__ = ()
    kind: str


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class _CompleteFromSchema(DatasetFamilyRowSemantics, _token=_CORE_TOKEN):
    kind: Literal["complete_from_schema"] = field(default="complete_from_schema", init=False)


class DatasetByteCount(_Descriptor, _token=_CORE_TOKEN):
    __slots__ = ()
    kind: Literal["exact", "unavailable"]


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class _ExactByteCount(DatasetByteCount, _token=_CORE_TOKEN):
    byte_count: int
    kind: Literal["exact"] = field(default="exact", init=False)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class _UnavailableByteCount(DatasetByteCount, _token=_CORE_TOKEN):
    reason_id: str
    kind: Literal["unavailable"] = field(default="unavailable", init=False)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class DatasetSchema(_Descriptor, _token=_CORE_TOKEN):
    columns: tuple[DatasetField, ...]

    def __repr__(self) -> str:
        return f"<DatasetSchema fields={len(self.columns)}>"


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class DatasetRowContract(_Descriptor, _token=_CORE_TOKEN):
    schema_version: int
    shape_id: DatasetShapeId
    schema: DatasetSchema
    coordinate_field_ids: tuple[DatasetFieldId, ...]
    key_field_ids: tuple[DatasetFieldId, ...]
    family_semantics: DatasetFamilyRowSemantics

    def __repr__(self) -> str:
        return f"<DatasetRowContract shape={str(self.shape_id)[:100]} fields={len(self.schema.columns)}>"


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class DatasetRowSetContract(_Descriptor, _token=_CORE_TOKEN):
    schema_version: int
    cardinality: DatasetCardinality
    ordering: DatasetOrdering


def _make_shape_id(
    family_id: str,
    local_shape_id: str,
    semantic_version: int,
    *,
    ids: _StableIdRegistry,
) -> DatasetShapeId:
    _stable_text(family_id, "shape.family_id")
    _stable_text(local_shape_id, "shape.local_shape_id")
    _positive(semantic_version, "shape.semantic_version")
    if (family_id, local_shape_id, semantic_version) not in ids.shapes:
        _fail(
            "an exact registered family-qualified shape",
            f"{family_id}/{local_shape_id}@v{semantic_version}",
            "shape",
        )
    return DatasetShapeId(
        _token=_CORE_TOKEN,
        family_id=family_id,
        local_shape_id=local_shape_id,
        semantic_version=semantic_version,
    )


def _make_field_id(value: str) -> DatasetFieldId:
    _stable_text(value, "field_id")
    return DatasetFieldId(_token=_CORE_TOKEN, value=value)


def _catalog_identity(identity_id: str) -> DatasetFieldIdentity:
    _stable_text(identity_id, "identity.catalog_ref")
    return _CatalogFieldIdentity(_token=_CORE_TOKEN, identity_id=identity_id)


def _entity_identity(
    entity_ref: Ref[EntityKind],
    identity_signature: tuple[tuple[str, str], ...],
    *,
    ids: _StableIdRegistry,
) -> _EntityFieldIdentity:
    location = "identity.entity_identity"
    if type(entity_ref) is not Ref or entity_ref.kind is not SemanticKind.ENTITY:
        _fail("an exact Entity ref", type(entity_ref).__name__, location)
    _stable_text(entity_ref.key, location)
    if type(identity_signature) is not tuple or not identity_signature:
        _fail("a non-empty immutable identity signature", "invalid signature", location)
    names: list[str] = []
    for component in identity_signature:
        if type(component) is not tuple or len(component) != 2:
            _fail("ordered key-name and logical-type pairs", "invalid component", location)
        name, logical_type = component
        _stable_text(name, location)
        _registered(logical_type, ids.logical_types, location)
        names.append(name)
    if len(set(names)) != len(names):
        _fail("unique ordered primary-key names", "duplicate component", location)
    return _EntityFieldIdentity(
        _token=_CORE_TOKEN, entity_ref=entity_ref, identity_signature=identity_signature
    )


def _runtime_metric_identity(expression_fingerprint: str) -> DatasetFieldIdentity:
    _stable_text(expression_fingerprint, "identity.runtime_metric")
    return _RuntimeMetricFieldIdentity(
        _token=_CORE_TOKEN, expression_fingerprint=expression_fingerprint
    )


def _metric_identity_from_key(key: str) -> DatasetFieldIdentity:
    """Decode the disjoint private Metric key without inventing catalog refs."""
    if key.startswith("runtime_metric:"):
        return _runtime_metric_identity(key.removeprefix("runtime_metric:"))
    return _catalog_identity("metric:" + key)


def _metric_identity_id(key: str) -> str:
    return key if key.startswith("runtime_metric:") else "metric:" + key


def _generated_identity(producer_field_id: DatasetFieldId) -> DatasetFieldIdentity:
    if type(producer_field_id) is not DatasetFieldId:
        _fail("a DatasetFieldId", type(producer_field_id).__name__, "identity.generated")
    return _GeneratedFieldIdentity(_token=_CORE_TOKEN, producer_field_id=producer_field_id)


def _resolved_type(physical_type_id: str, *, ids: _StableIdRegistry) -> DatasetPhysicalTypeState:
    _registered(physical_type_id, ids.physical_types, "physical_type")
    return _ResolvedPhysicalType(_token=_CORE_TOKEN, physical_type_id=physical_type_id)


def _deferred_type(
    admitted_type_class_id: str, *, ids: _StableIdRegistry
) -> DatasetPhysicalTypeState:
    _registered(admitted_type_class_id, ids.admitted_types, "admitted_type_class")
    return _DeferredPhysicalType(_token=_CORE_TOKEN, admitted_type_class_id=admitted_type_class_id)


def _make_field(
    *,
    field_id: DatasetFieldId,
    name: str,
    role_id: str,
    identity: DatasetFieldIdentity,
    derivation_identity: str,
    logical_type_id: str,
    physical_type_state: DatasetPhysicalTypeState,
    nullable: bool,
    ids: _StableIdRegistry,
) -> DatasetField:
    if type(field_id) is not DatasetFieldId:
        _fail("a DatasetFieldId", type(field_id).__name__, "field.field_id")
    if type(identity) not in (
        _CatalogFieldIdentity,
        _RuntimeMetricFieldIdentity,
        _GeneratedFieldIdentity,
        _EntityFieldIdentity,
    ):
        _fail("a closed field identity", type(identity).__name__, "field.identity")
    if isinstance(identity, _EntityFieldIdentity):
        _validate_variant_kind(identity, "entity_identity")
        _entity_identity(identity.entity_ref, identity.identity_signature, ids=ids)
    if type(physical_type_state) not in (_ResolvedPhysicalType, _DeferredPhysicalType):
        _fail(
            "a closed physical type state",
            type(physical_type_state).__name__,
            "field.physical_type_state",
        )
    if (
        not isinstance(name, str)
        or not name
        or len(name) > 160
        or any(ord(char) < 32 for char in name)
    ):
        _fail(
            "a non-empty bounded public name without control characters",
            type(name).__name__,
            "field.name",
        )
    if unicodedata.normalize("NFC", name) != name:
        _fail("an NFC-normalized public field name", name, "field.name")
    if type(nullable) is not bool:
        _fail("a boolean nullability", type(nullable).__name__, "field.nullable")
    _registered(role_id, ids.roles, "field.role")
    _registered(logical_type_id, ids.logical_types, "field.logical_type")
    _stable_text(derivation_identity, "field.derivation_identity")
    return DatasetField(
        _token=_CORE_TOKEN,
        field_id=field_id,
        name=name,
        role_id=role_id,
        identity=identity,
        derivation_identity=derivation_identity,
        logical_type_id=logical_type_id,
        physical_type_state=physical_type_state,
        nullable=nullable,
    )


def _make_schema(columns: tuple[DatasetField, ...]) -> DatasetSchema:
    if not isinstance(columns, tuple) or any(
        type(column) is not DatasetField for column in columns
    ):
        _fail(
            "an immutable tuple of DatasetField bindings", type(columns).__name__, "schema.columns"
        )
    if len({column.field_id for column in columns}) != len(columns):
        _fail("unique field ids", "duplicate field ids", "schema.columns")
    if len({column.name for column in columns}) != len(columns):
        _fail("unique normalized public names", "duplicate names", "schema.columns")
    return DatasetSchema(_token=_CORE_TOKEN, columns=columns)


def _unknown_row_bound() -> DatasetRowBound:
    return _UnknownRowBound(_token=_CORE_TOKEN)


def _static_row_bound(max_rows: int) -> DatasetRowBound:
    _positive(max_rows, "row_bound.max_rows")
    return _StaticRowBound(_token=_CORE_TOKEN, max_rows=max_rows)


def _runtime_policy_row_bound(policy_id: str, *, ids: _StableIdRegistry) -> DatasetRowBound:
    _registered(policy_id, ids.policies, "row_bound.policy_id")
    return _RuntimePolicyRowBound(_token=_CORE_TOKEN, policy_id=policy_id)


def _singleton_cardinality() -> DatasetCardinality:
    return _SingletonCardinality(_token=_CORE_TOKEN)


def _keyed_cardinality(row_bound: DatasetRowBound) -> DatasetCardinality:
    if type(row_bound) not in (_UnknownRowBound, _StaticRowBound, _RuntimePolicyRowBound):
        _fail("a closed row bound", type(row_bound).__name__, "cardinality.row_bound")
    return _KeyedCardinality(_token=_CORE_TOKEN, row_bound=row_bound)


def _make_order_term(
    field_id: DatasetFieldId,
    *,
    direction: Literal["ascending", "descending"],
    nulls: Literal["first", "last"],
    value_order_contract_id: str,
    ids: _StableIdRegistry,
) -> DatasetOrderTerm:
    if type(field_id) is not DatasetFieldId:
        _fail("a DatasetFieldId", type(field_id).__name__, "ordering.field_id")
    if direction not in ("ascending", "descending") or nulls not in ("first", "last"):
        _fail("a closed direction and null placement", f"{direction}/{nulls}", "ordering")
    _registered(value_order_contract_id, ids.value_orders, "ordering.value_order_contract_id")
    return DatasetOrderTerm(
        _token=_CORE_TOKEN,
        field_id=field_id,
        direction=direction,
        nulls=nulls,
        value_order_contract_id=value_order_contract_id,
    )


def _unordered_ordering() -> DatasetOrdering:
    return _UnorderedOrdering(_token=_CORE_TOKEN)


def _ordered_ordering(terms: tuple[DatasetOrderTerm, ...]) -> DatasetOrdering:
    if (
        not isinstance(terms, tuple)
        or not terms
        or any(type(term) is not DatasetOrderTerm for term in terms)
    ):
        _fail("a non-empty immutable tuple of order terms", type(terms).__name__, "ordering.terms")
    if len({term.field_id for term in terms}) != len(terms):
        _fail("unique order field ids", "duplicate order field ids", "ordering.terms")
    return _OrderedOrdering(_token=_CORE_TOKEN, terms=terms)


def _complete_from_schema() -> DatasetFamilyRowSemantics:
    return _CompleteFromSchema(_token=_CORE_TOKEN)


def _exact_byte_count(byte_count: int) -> DatasetByteCount:
    _positive(byte_count, "byte_count", zero=True)
    return _ExactByteCount(_token=_CORE_TOKEN, byte_count=byte_count)


def _unavailable_byte_count(reason_id: str, *, ids: _StableIdRegistry) -> DatasetByteCount:
    _registered(reason_id, ids.byte_unavailable_reasons, "byte_count.reason_id")
    return _UnavailableByteCount(_token=_CORE_TOKEN, reason_id=reason_id)


def _make_row_contract(
    *,
    schema_version: int,
    shape_id: DatasetShapeId,
    schema: DatasetSchema,
    coordinate_field_ids: tuple[DatasetFieldId, ...],
    key_field_ids: tuple[DatasetFieldId, ...],
    family_semantics: DatasetFamilyRowSemantics,
) -> DatasetRowContract:
    _positive(schema_version, "row_contract.schema_version")
    if type(shape_id) is not DatasetShapeId or type(schema) is not DatasetSchema:
        _fail("an exact shape and schema descriptor", "invalid descriptor type", "row_contract")
    _validate_field_ids(coordinate_field_ids, schema, "row_contract.coordinates")
    _validate_field_ids(key_field_ids, schema, "row_contract.key")
    _validate_key_coordinates(coordinate_field_ids, key_field_ids)
    _semantics_payload(family_semantics)
    return DatasetRowContract(
        _token=_CORE_TOKEN,
        schema_version=schema_version,
        shape_id=shape_id,
        schema=schema,
        coordinate_field_ids=coordinate_field_ids,
        key_field_ids=key_field_ids,
        family_semantics=family_semantics,
    )


def _make_row_set_contract(
    *,
    schema_version: int,
    cardinality: DatasetCardinality,
    ordering: DatasetOrdering,
) -> DatasetRowSetContract:
    _positive(schema_version, "row_set_contract.schema_version")
    if type(cardinality) not in (_SingletonCardinality, _KeyedCardinality):
        _fail("a closed cardinality", type(cardinality).__name__, "row_set_contract.cardinality")
    if type(ordering) not in (_UnorderedOrdering, _OrderedOrdering):
        _fail("a closed ordering", type(ordering).__name__, "row_set_contract.ordering")
    return DatasetRowSetContract(
        _token=_CORE_TOKEN,
        schema_version=schema_version,
        cardinality=cardinality,
        ordering=ordering,
    )


def _validate_field_ids(
    ids: tuple[DatasetFieldId, ...], schema: DatasetSchema, location: str
) -> None:
    if not isinstance(ids, tuple) or any(type(item) is not DatasetFieldId for item in ids):
        _fail("an immutable tuple of field ids", type(ids).__name__, location)
    if len(set(ids)) != len(ids):
        _fail("duplicate-free field ids", "duplicate field ids", location)
    present = {column.field_id for column in schema.columns}
    if any(item not in present for item in ids):
        _fail("only current schema field ids", "missing or stale field id", location)


def _validate_key_coordinates(
    coordinates: tuple[DatasetFieldId, ...], key: tuple[DatasetFieldId, ...]
) -> None:
    if tuple(item for item in coordinates if item in key) != key:
        _fail(
            "a key that is an ordered subset of coordinates",
            "key/coordinate mismatch",
            "row_contract.key",
        )


def _validate_row_contract_pair(
    row: DatasetRowContract,
    row_set: DatasetRowSetContract,
    *,
    unique_tie_breakers: tuple[tuple[DatasetFieldId, ...], ...] = (),
) -> None:
    if type(row) is not DatasetRowContract or type(row_set) is not DatasetRowSetContract:
        _fail(
            "an exact row and row-set contract pair", "invalid descriptor type", "dataset.contracts"
        )
    _make_schema(row.schema.columns)
    _validate_field_ids(row.coordinate_field_ids, row.schema, "row_contract.coordinates")
    _validate_field_ids(row.key_field_ids, row.schema, "row_contract.key")
    _validate_key_coordinates(row.coordinate_field_ids, row.key_field_ids)
    if isinstance(row_set.cardinality, _SingletonCardinality):
        if row.coordinate_field_ids or row.key_field_ids:
            _fail(
                "empty coordinates and key for singleton",
                "non-empty row identity",
                "dataset.contracts",
            )
    elif isinstance(row_set.cardinality, _KeyedCardinality):
        if not row.key_field_ids:
            _fail("a non-empty key for keyed cardinality", "empty key", "dataset.contracts")
    else:
        _fail("a closed cardinality", type(row_set.cardinality).__name__, "dataset.contracts")
    for unique in unique_tie_breakers:
        _validate_field_ids(unique, row.schema, "row_contract.unique_tie_breaker")
        if not unique:
            _fail(
                "a non-empty proven unique tie-breaker",
                "empty field ids",
                "row_contract.unique_tie_breaker",
            )
    if isinstance(row_set.ordering, _OrderedOrdering):
        terms = row_set.ordering.terms
        _ordered_ordering(terms)
        order_ids = tuple(term.field_id for term in terms)
        _validate_field_ids(order_ids, row.schema, "row_set_contract.ordering")
        candidates = (row.key_field_ids, *unique_tie_breakers)
        if not isinstance(row_set.cardinality, _SingletonCardinality) and not any(
            candidate and set(candidate).issubset(order_ids) for candidate in candidates
        ):
            _fail(
                "a total order containing the key or an owner-proven unique tie-breaker",
                "partial order",
                "row_set_contract.ordering",
            )
    elif not isinstance(row_set.ordering, _UnorderedOrdering):
        _fail("a closed ordering", type(row_set.ordering).__name__, "dataset.contracts")


def _semantics_payload(value: DatasetFamilyRowSemantics) -> _CanonicalValue:
    if not isinstance(value, DatasetFamilyRowSemantics) or not is_dataclass(value):
        _fail(
            "a sealed immutable family semantics variant",
            type(value).__name__,
            "row_contract.family_semantics",
        )
    parameters = getattr(type(value), "__dataclass_params__", None)
    if not getattr(parameters, "frozen", False) or hasattr(value, "__dict__"):
        _fail(
            "a frozen slotted family semantics variant",
            type(value).__name__,
            "row_contract.family_semantics",
        )
    entries: list[_CanonicalValue] = []
    for item in fields(value):
        raw: object = getattr(value, item.name)
        if item.name in {
            "family_id",
            "shape_id",
            "schema",
            "coordinate_field_ids",
            "key_field_ids",
            "cardinality",
            "ordering",
            "name",
            "role_id",
            "identity",
            "logical_type_id",
            "physical_type_state",
            "nullable",
            "definition_fingerprint",
            "lineage",
        }:
            _fail(
                "family-specific facts without duplicated common contract facts",
                item.name,
                "row_contract.family_semantics",
            )
        entries.append((item.name, _semantics_atom(raw)))
    kind = getattr(value, "kind", None)
    if not isinstance(kind, str):
        _fail(
            "a registered kind-discriminated family semantics variant",
            "missing kind",
            "row_contract.family_semantics",
        )
    _stable_text(kind, "row_contract.family_semantics.kind")
    return tuple(entries)


def _semantics_atom(value: object) -> _CanonicalValue:
    if type(value) is float:
        import math

        if not math.isfinite(value):
            _fail(
                "finite family numeric facts", "non-finite float", "row_contract.family_semantics"
            )
        return ("float", value.hex())
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, DatasetFieldId):
        return ("field_id", value.value)
    if isinstance(value, tuple):
        return ("tuple", tuple(_semantics_atom(item) for item in value))
    _fail(
        "closed immutable family facts and field-id references",
        type(value).__name__,
        "row_contract.family_semantics",
    )


def _descriptor_payload(value: _Descriptor) -> _CanonicalValue:
    if isinstance(value, DatasetFamilyRowSemantics):
        return ("family_semantics", _semantics_payload(value))
    values: list[_CanonicalValue] = []
    for item in fields(value):
        raw: object = getattr(value, item.name)
        values.append((item.name, _descriptor_atom(raw)))
    return (type(value).__name__, tuple(values))


def _descriptor_atom(value: object) -> _CanonicalValue:
    if isinstance(value, _Descriptor):
        return _descriptor_payload(value)
    if type(value) is Ref:
        return ("semantic_ref", value.kind.value, value.path)
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, tuple):
        return tuple(_descriptor_atom(item) for item in value)
    _fail("closed canonical descriptor values", type(value).__name__, "descriptor.fingerprint")


def _canonical(value: _CanonicalValue) -> object:
    if value is None:
        return ["null"]
    if type(value) is bool:
        return ["bool", value]
    if type(value) is int:
        return ["int", str(value)]
    if type(value) is float:
        if not math.isfinite(value):
            _fail("finite canonical numbers", "non-finite float", "dataset.fingerprint")
        return ["float", value.hex()]
    if type(value) is str:
        return ["str", value]
    if type(value) is tuple:
        return ["tuple", [_canonical(item) for item in value]]
    _fail("closed canonical scalar or tuple", type(value).__name__, "dataset.fingerprint")


def _canonical_digest(value: _CanonicalValue) -> str:
    """Encode shared value primitives; each owner supplies its own identity payload."""
    encoded = json.dumps(_canonical(value), ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _field_binding_fingerprint(value: DatasetField) -> str:
    return _canonical_digest(_descriptor_payload(value))


def _row_contract_fingerprint(value: DatasetRowContract) -> str:
    return _canonical_digest(_descriptor_payload(value))


def _row_set_contract_fingerprint(value: DatasetRowSetContract) -> str:
    return _canonical_digest(_descriptor_payload(value))


def _validate_realized_schema(
    logical: DatasetSchema,
    realized: DatasetSchema,
    *,
    ids: _StableIdRegistry,
) -> None:
    """Validate exact retained bindings and explicit deferred type refinement."""
    if type(logical) is not DatasetSchema or type(realized) is not DatasetSchema:
        _fail(
            "logical and realized DatasetSchema descriptors",
            "invalid schema type",
            "state.realized_schema",
        )
    _make_schema(logical.columns)
    _make_schema(realized.columns)
    if len(logical.columns) != len(realized.columns):
        _fail(
            "the exact logical field count", "realized field count differs", "state.realized_schema"
        )
    for expected, actual in zip(logical.columns, realized.columns, strict=True):
        if type(actual.physical_type_state) is not _ResolvedPhysicalType:
            _fail(
                "a resolved realized physical type",
                "deferred physical type",
                "state.realized_schema",
            )
        if (
            expected.field_id != actual.field_id
            or expected.name != actual.name
            or expected.role_id != actual.role_id
            or expected.identity != actual.identity
            or expected.derivation_identity != actual.derivation_identity
            or expected.logical_type_id != actual.logical_type_id
            or expected.nullable != actual.nullable
        ):
            _fail(
                "the exact ordered logical field bindings",
                "realized field binding differs",
                "state.realized_schema",
            )
        physical = actual.physical_type_state.physical_type_id
        _registered(physical, ids.physical_types, "state.realized_schema.physical_type")
        if isinstance(expected.physical_type_state, _ResolvedPhysicalType):
            admitted = expected.physical_type_state.physical_type_id == physical
        elif isinstance(expected.physical_type_state, _DeferredPhysicalType):
            admitted = (
                physical,
                expected.physical_type_state.admitted_type_class_id,
            ) in ids.physical_type_classes
            if (
                physical == expected.physical_type_state.admitted_type_class_id
                and _bool_tuple_arity(physical) is not None
                and ("bool_tuple", "bool_tuple") in ids.physical_type_classes
            ):
                admitted = True
        else:
            admitted = False
        if not admitted:
            _fail(
                "a physical type admitted by the logical field constraint",
                physical,
                "state.realized_schema",
            )


def _validate_variant_kind(value: _Descriptor, expected: str) -> None:
    actual: object = getattr(value, "kind", None)
    if actual != expected:
        _fail(
            "the exact closed variant discriminator", "corrupt variant kind", type(value).__name__
        )


def _validate_registered_schema(schema: DatasetSchema, *, ids: _StableIdRegistry) -> None:
    """Revalidate every binding against the consuming family's exact vocabulary."""
    if type(schema) is not DatasetSchema:
        _fail("an exact DatasetSchema", type(schema).__name__, "schema")
    _make_schema(schema.columns)
    for column in schema.columns:
        if type(column.field_id) is not DatasetFieldId:
            _fail("an exact DatasetFieldId", type(column.field_id).__name__, "field.field_id")
        _make_field_id(column.field_id.value)
        physical = column.physical_type_state
        if type(physical) is _ResolvedPhysicalType:
            _validate_variant_kind(physical, "resolved")
            _resolved_type(physical.physical_type_id, ids=ids)
        elif type(physical) is _DeferredPhysicalType:
            _validate_variant_kind(physical, "deferred")
            _deferred_type(physical.admitted_type_class_id, ids=ids)
        else:
            _fail(
                "a closed physical type state", type(physical).__name__, "field.physical_type_state"
            )
        identity = column.identity
        if type(identity) is _CatalogFieldIdentity:
            _validate_variant_kind(identity, "catalog_ref")
            _catalog_identity(identity.identity_id)
        elif type(identity) is _RuntimeMetricFieldIdentity:
            _validate_variant_kind(identity, "runtime_metric")
            _runtime_metric_identity(identity.expression_fingerprint)
        elif type(identity) is _EntityFieldIdentity:
            _validate_variant_kind(identity, "entity_identity")
            _entity_identity(identity.entity_ref, identity.identity_signature, ids=ids)
        elif type(identity) is _GeneratedFieldIdentity:
            _validate_variant_kind(identity, "generated")
            if type(identity.producer_field_id) is not DatasetFieldId:
                _fail(
                    "an exact DatasetFieldId",
                    type(identity.producer_field_id).__name__,
                    "field.identity",
                )
            _make_field_id(identity.producer_field_id.value)
        else:
            _fail("a closed field identity", type(identity).__name__, "field.identity")
        _make_field(
            field_id=column.field_id,
            name=column.name,
            role_id=column.role_id,
            identity=column.identity,
            derivation_identity=column.derivation_identity,
            logical_type_id=column.logical_type_id,
            physical_type_state=physical,
            nullable=column.nullable,
            ids=ids,
        )


def _validate_registered_contract(
    row: DatasetRowContract,
    row_set: DatasetRowSetContract,
    *,
    ids: _StableIdRegistry,
) -> None:
    """Bind descriptor ids to the owning family; structural pairing stays separate."""
    if type(row) is not DatasetRowContract or type(row_set) is not DatasetRowSetContract:
        _fail("exact row and row-set descriptors", "invalid contract type", "dataset.contracts")
    if type(row.shape_id) is not DatasetShapeId:
        _fail("an exact DatasetShapeId", type(row.shape_id).__name__, "row_contract.shape")
    _make_shape_id(
        row.shape_id.family_id, row.shape_id.local_shape_id, row.shape_id.semantic_version, ids=ids
    )
    _validate_registered_schema(row.schema, ids=ids)
    _make_row_contract(
        schema_version=row.schema_version,
        shape_id=row.shape_id,
        schema=row.schema,
        coordinate_field_ids=row.coordinate_field_ids,
        key_field_ids=row.key_field_ids,
        family_semantics=row.family_semantics,
    )
    cardinality = row_set.cardinality
    if type(cardinality) is _KeyedCardinality:
        _validate_variant_kind(cardinality, "keyed")
        bound = cardinality.row_bound
        if type(bound) is _StaticRowBound:
            _validate_variant_kind(bound, "static")
            _static_row_bound(bound.max_rows)
        elif type(bound) is _RuntimePolicyRowBound:
            _validate_variant_kind(bound, "runtime_policy")
            _runtime_policy_row_bound(bound.policy_id, ids=ids)
        elif type(bound) is not _UnknownRowBound:
            _fail("a closed row bound", type(bound).__name__, "row_set_contract.cardinality")
        if type(bound) is _UnknownRowBound:
            _validate_variant_kind(bound, "unknown")
        _keyed_cardinality(bound)
    elif type(cardinality) is not _SingletonCardinality:
        _fail("a closed cardinality", type(cardinality).__name__, "row_set_contract.cardinality")
    if type(cardinality) is _SingletonCardinality:
        _validate_variant_kind(cardinality, "singleton")
    ordering = row_set.ordering
    if type(ordering) is _OrderedOrdering:
        _validate_variant_kind(ordering, "ordered")
        _ordered_ordering(ordering.terms)
        for term in ordering.terms:
            _make_order_term(
                term.field_id,
                direction=term.direction,
                nulls=term.nulls,
                value_order_contract_id=term.value_order_contract_id,
                ids=ids,
            )
    elif type(ordering) is not _UnorderedOrdering:
        _fail("a closed ordering", type(ordering).__name__, "row_set_contract.ordering")
    if type(ordering) is _UnorderedOrdering:
        _validate_variant_kind(ordering, "unordered")
    _make_row_set_contract(
        schema_version=row_set.schema_version, cardinality=cardinality, ordering=ordering
    )
