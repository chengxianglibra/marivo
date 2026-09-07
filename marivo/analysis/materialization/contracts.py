"""Closed immutable v3 metadata and explicit, non-executable value codecs."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.handles import BoundedLineage, CanonicalValue
from marivo.analysis.evidence.types import QualitySummary
from marivo.analysis.materialization.errors import IntegrityError

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_MAX_PAYLOAD_BYTES = 1_048_576


def invalid(received: str) -> IntegrityError:
    return IntegrityError(
        expected="one supported complete canonical v3 metadata value",
        received=received,
        repair="Preserve the selected Artifact and inspect its exact metadata; do not replay its origin.",
        stage="publication",
    )


def canonical_json(value: object) -> str:
    try:
        text = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise invalid("non-canonical metadata") from exc
    if len(text.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise invalid("metadata byte bound exceeded")
    return text


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise invalid("duplicate JSON member")
        result[key] = value
    return result


def parse_json(text: str) -> object:
    if len(text.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise invalid("metadata byte bound exceeded")
    try:
        value: object = json.loads(text, object_pairs_hook=_pairs)
        if canonical_json(value) != text:
            raise invalid("non-canonical JSON encoding")
        return value
    except (TypeError, ValueError, RecursionError) as exc:
        raise invalid("invalid JSON metadata") from exc


def _obj(value: object, names: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != set(names.split()):
        raise invalid("unknown or missing metadata members")
    return {str(key): item for key, item in value.items()}


def _text(value: object, *, empty: bool = False) -> str:
    if type(value) is not str or (not empty and not value) or len(value.encode("utf-8")) > 4096:
        raise invalid("invalid bounded text")
    return value


def _int(value: object, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise invalid("invalid integer")
    return value


def _array(value: object) -> tuple[object, ...]:
    if type(value) is not list or len(value) > 4096:
        raise invalid("invalid bounded array")
    return tuple(value)


def _texts(value: object) -> tuple[str, ...]:
    return tuple(_text(item) for item in _array(value))


def _hash(value: str) -> None:
    if _HASH.fullmatch(value) is None:
        raise invalid("invalid SHA-256 digest")


def _relative(value: str) -> None:
    path = PurePosixPath(value)
    if (
        not value
        or value == "."
        or path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != value
        or "\\" in value
    ):
        raise invalid("unsafe relative storage path")


@dataclass(frozen=True, slots=True)
class FileEntry:
    relative_path: str
    size_bytes: int
    sha256: str

    def __post_init__(self) -> None:
        _relative(self.relative_path)
        _int(self.size_bytes)
        _hash(self.sha256)


def manifest_payload(entries: tuple[FileEntry, ...]) -> list[dict[str, object]]:
    return [
        {"relative_path": item.relative_path, "size_bytes": item.size_bytes, "sha256": item.sha256}
        for item in entries
    ]


def manifest_digest(entries: tuple[FileEntry, ...]) -> str:
    return digest(manifest_payload(entries))


@dataclass(frozen=True, slots=True)
class LocalReceipt:
    project_relative_path: str
    file_manifest: tuple[FileEntry, ...]
    manifest_hash: str
    bytes_hash: str
    schema_fingerprint: str
    realized_row_count: int
    realized_byte_count: int
    parquet_contract_version: int = 1

    def __post_init__(self) -> None:
        _relative(self.project_relative_path)
        if type(self.file_manifest) is not tuple or not self.file_manifest:
            raise invalid("empty or mutable file manifest")
        paths = tuple(item.relative_path for item in self.file_manifest)
        if paths != tuple(sorted(set(paths))):
            raise invalid("non-canonical file manifest order")
        if self.manifest_hash != manifest_digest(self.file_manifest):
            raise invalid("manifest digest mismatch")
        for value in (self.manifest_hash, self.bytes_hash, self.schema_fingerprint):
            _hash(value)
        _int(self.realized_row_count)
        _int(self.realized_byte_count)
        if self.realized_byte_count < sum(item.size_bytes for item in self.file_manifest):
            raise invalid("receipt bytes smaller than manifest")
        if type(self.parquet_contract_version) is not int or self.parquet_contract_version != 1:
            raise invalid("unsupported Parquet contract")

    @property
    def kind(self) -> Literal["local"]:
        return "local"

    @property
    def format(self) -> Literal["parquet"]:
        return "parquet"

    @property
    def identity_digest(self) -> str:
        return digest(receipt_payload(self))


def receipt_payload(value: LocalReceipt) -> dict[str, object]:
    return {
        "kind": "local",
        "project_relative_path": value.project_relative_path,
        "format": "parquet",
        "parquet_contract_version": value.parquet_contract_version,
        "file_manifest": manifest_payload(value.file_manifest),
        "manifest_hash": value.manifest_hash,
        "bytes_hash": value.bytes_hash,
        "schema_fingerprint": value.schema_fingerprint,
        "realized_row_count": value.realized_row_count,
        "realized_byte_count": {"kind": "exact", "byte_count": value.realized_byte_count},
    }


def decode_receipt(value: object) -> LocalReceipt:
    obj = _obj(
        value,
        "kind project_relative_path format parquet_contract_version file_manifest manifest_hash bytes_hash schema_fingerprint realized_row_count realized_byte_count",
    )
    if obj["kind"] != "local" or obj["format"] != "parquet":
        raise invalid("unsupported storage receipt")
    entries = []
    for item in _array(obj["file_manifest"]):
        entry = _obj(item, "relative_path size_bytes sha256")
        entries.append(
            FileEntry(
                _text(entry["relative_path"]), _int(entry["size_bytes"]), _text(entry["sha256"])
            )
        )
    byte_count = _obj(obj["realized_byte_count"], "kind byte_count")
    if byte_count["kind"] != "exact":
        raise invalid("local receipt requires exact bytes")
    return LocalReceipt(
        _text(obj["project_relative_path"]),
        tuple(entries),
        _text(obj["manifest_hash"]),
        _text(obj["bytes_hash"]),
        _text(obj["schema_fingerprint"]),
        _int(obj["realized_row_count"]),
        _int(byte_count["byte_count"]),
        _int(obj["parquet_contract_version"], minimum=1),
    )


@dataclass(frozen=True, slots=True)
class RetainedPart:
    role: str
    contract_id: str
    contract_version: int
    storage_receipt: LocalReceipt

    def __post_init__(self) -> None:
        _text(self.role)
        _text(self.contract_id)
        _int(self.contract_version, minimum=1)


@dataclass(frozen=True, slots=True)
class MaterializationContract:
    producer_id: str
    producer_contract_version: int
    shape_id: d.DatasetShapeId
    quality_contract_id: str
    quality_contract_version: int
    evidence_extractor_id: str
    evidence_extractor_version: int
    finding_extractor_id: str
    finding_extractor_version: int
    validation_output_contract_ids: tuple[str, ...]
    retained_private_state_contract_ids: tuple[str, ...]
    finding_policy_id: str


@dataclass(frozen=True, slots=True)
class PopulationAuthority:
    definition_fingerprint: str
    entity_ref: str
    identity_signature: tuple[tuple[str, str], ...]
    membership_scope: CanonicalValue = None
    version_selection: CanonicalValue = None
    validation_results: tuple[tuple[str, int], ...] = ()


def _scope(value: object) -> CanonicalValue:
    if value is None:
        return None
    items = _array(value)
    if len(items) != 3 or items[2] != "closed_open":
        raise invalid("unsupported membership scope")
    return (_text(items[0]), _text(items[1]), "closed_open")


def _semantic_ref(value: object) -> CanonicalValue:
    items = _array(value)
    if len(items) != 3 or items[0] != "marivo.semantic_ref/v1" or items[1] != "time_dimension":
        raise invalid("unsupported version coordinate ref")
    return ("marivo.semantic_ref/v1", "time_dimension", _text(items[2]))


def _selection(value: object) -> CanonicalValue:
    if value is None:
        return None
    parts = _array(value)
    if len(parts) == 4 and parts[0] == "snapshot" and parts[3] in ("instant", "before_endpoint"):
        return ("snapshot", _semantic_ref(parts[1]), _text(parts[2]), _text(parts[3]))
    if (
        len(parts) == 8
        and parts[0] == "validity"
        and parts[4] in ("lt", "le")
        and parts[5] in ("gt", "ge")
        and parts[7] in ("instant", "before_endpoint")
    ):
        open_end = tuple(None if item is None else _text(item) for item in _array(parts[6]))
        return (
            "validity",
            _semantic_ref(parts[1]),
            _semantic_ref(parts[2]),
            _text(parts[3]),
            _text(parts[4]),
            _text(parts[5]),
            open_end,
            _text(parts[7]),
        )
    raise invalid("unsupported exact version selection")


def _validation_results(value: object) -> tuple[tuple[str, int], ...]:
    result = []
    for raw in _array(value):
        pair = _array(raw)
        if len(pair) != 2 or pair[1] != 0:
            raise invalid("unsatisfied publication validation")
        result.append((_text(pair[0]), _int(pair[1])))
    if len({name for name, _ in result}) != len(result):
        raise invalid("duplicate validation result")
    return tuple(result)


@dataclass(frozen=True, slots=True)
class MaterializationIssue:
    kind: str
    expected: str
    received: str
    repair: str


@dataclass(frozen=True, slots=True)
class ArtifactDescriptor:
    definition_fingerprint: str
    row_contract: d.DatasetRowContract
    row_set_contract: d.DatasetRowSetContract
    realized_schema: d.DatasetSchema
    bounded_lineage: BoundedLineage
    semantic_dependency_digest: str
    population_authority: PopulationAuthority
    sampling_execution: None
    operator_implementation_versions: tuple[tuple[str, int], ...]
    dataset_materialization_contract: MaterializationContract
    storage_receipt: LocalReceipt
    retained_parts: tuple[RetainedPart, ...]
    quality_summary: QualitySummary
    typed_issues: tuple[MaterializationIssue, ...] = ()

    @property
    def row_contract_fingerprint(self) -> str:
        return d._row_contract_fingerprint(self.row_contract)

    @property
    def row_set_contract_fingerprint(self) -> str:
        return d._row_set_contract_fingerprint(self.row_set_contract)

    @property
    def realized_schema_fingerprint(self) -> str:
        return schema_fingerprint(self.realized_schema)


def schema_fingerprint(value: d.DatasetSchema) -> str:
    return d._canonical_digest(d._descriptor_payload(value))


def _shape_payload(value: d.DatasetShapeId) -> dict[str, object]:
    return {
        "family_id": value.family_id,
        "local_shape_id": value.local_shape_id,
        "semantic_version": value.semantic_version,
    }


def _shape(value: object, ids: d._StableIdRegistry) -> d.DatasetShapeId:
    obj = _obj(value, "family_id local_shape_id semantic_version")
    return d._make_shape_id(
        _text(obj["family_id"]),
        _text(obj["local_shape_id"]),
        _int(obj["semantic_version"], minimum=1),
        ids=ids,
    )


def _identity_payload(value: d.DatasetFieldIdentity) -> dict[str, object]:
    if isinstance(value, d._EntityFieldIdentity):
        return {
            "kind": value.kind,
            "entity_ref": value.entity_ref.path,
            "identity_signature": value.identity_signature,
        }
    if isinstance(value, d._CatalogFieldIdentity):
        return {"kind": value.kind, "identity_id": value.identity_id}
    if isinstance(value, d._RuntimeMetricFieldIdentity):
        return {"kind": value.kind, "expression_fingerprint": value.expression_fingerprint}
    if isinstance(value, d._GeneratedFieldIdentity):
        return {"kind": value.kind, "producer_field_id": value.producer_field_id.value}
    raise invalid("unregistered field identity")


def _signature(value: object) -> tuple[tuple[str, str], ...]:
    result = []
    for pair in _array(value):
        items = _array(pair)
        if len(items) != 2:
            raise invalid("invalid identity component")
        result.append((_text(items[0]), _text(items[1])))
    return tuple(result)


def _identity(value: object, ids: d._StableIdRegistry) -> d.DatasetFieldIdentity:
    from marivo.analysis.observation.contracts import entity_ref

    if not isinstance(value, dict):
        raise invalid("invalid field identity")
    kind = value.get("kind")
    if kind == "entity_identity":
        obj = _obj(value, "kind entity_ref identity_signature")
        return d._entity_identity(
            entity_ref(_text(obj["entity_ref"])),
            _signature(obj["identity_signature"]),
            ids=ids,
        )
    if kind == "catalog_ref":
        return d._catalog_identity(_text(_obj(value, "kind identity_id")["identity_id"]))
    if kind == "runtime_metric":
        return d._runtime_metric_identity(
            _text(_obj(value, "kind expression_fingerprint")["expression_fingerprint"])
        )
    if kind == "generated":
        return d._generated_identity(
            d._make_field_id(_text(_obj(value, "kind producer_field_id")["producer_field_id"]))
        )
    raise invalid("unsupported field identity")


def schema_payload(value: d.DatasetSchema) -> dict[str, object]:
    columns = []
    for column in value.columns:
        physical = column.physical_type_state
        if isinstance(physical, d._ResolvedPhysicalType):
            state: dict[str, object] = {
                "kind": "resolved",
                "physical_type_id": physical.physical_type_id,
            }
        elif isinstance(physical, d._DeferredPhysicalType):
            state = {"kind": "deferred", "admitted_type_class_id": physical.admitted_type_class_id}
        else:
            raise invalid("unsupported physical type state")
        columns.append(
            {
                "field_id": column.field_id.value,
                "name": column.name,
                "role_id": column.role_id,
                "identity": _identity_payload(column.identity),
                "derivation_identity": column.derivation_identity,
                "logical_type_id": column.logical_type_id,
                "physical_type_state": state,
                "nullable": column.nullable,
            }
        )
    return {"columns": columns}


def decode_schema(value: object, ids: d._StableIdRegistry) -> d.DatasetSchema:
    columns = []
    for item in _array(_obj(value, "columns")["columns"]):
        obj = _obj(
            item,
            "field_id name role_id identity derivation_identity logical_type_id physical_type_state nullable",
        )
        physical = obj["physical_type_state"]
        if not isinstance(physical, dict):
            raise invalid("invalid physical type")
        if physical.get("kind") == "resolved":
            state: d.DatasetPhysicalTypeState = d._resolved_type(
                _text(_obj(physical, "kind physical_type_id")["physical_type_id"]), ids=ids
            )
        elif physical.get("kind") == "deferred":
            state = d._deferred_type(
                _text(_obj(physical, "kind admitted_type_class_id")["admitted_type_class_id"]),
                ids=ids,
            )
        else:
            raise invalid("unsupported physical type")
        nullable = obj["nullable"]
        if type(nullable) is not bool:
            raise invalid("non-boolean nullability")
        columns.append(
            d._make_field(
                field_id=d._make_field_id(_text(obj["field_id"])),
                name=_text(obj["name"]),
                role_id=_text(obj["role_id"]),
                identity=_identity(obj["identity"], ids),
                derivation_identity=_text(obj["derivation_identity"]),
                logical_type_id=_text(obj["logical_type_id"]),
                physical_type_state=state,
                nullable=nullable,
                ids=ids,
            )
        )
    return d._make_schema(tuple(columns))


def _semantics_payload(value: d.DatasetFamilyRowSemantics) -> dict[str, object]:
    from marivo.analysis.observation.contracts import (
        EntityPresentMetricSemantics,
        EntityReducedMetricSemantics,
    )

    if isinstance(value, d._CompleteFromSchema):
        return {"kind": "complete_from_schema"}
    if isinstance(value, (EntityPresentMetricSemantics, EntityReducedMetricSemantics)):
        result: dict[str, object] = {
            "kind": value.kind,
            "metric_bindings": [
                (field.value, unit, dependency, state, nulls, empty)
                for field, unit, dependency, state, nulls, empty in value.metric_bindings
            ],
            "coordinate_semantics": [
                (field.value, kind, parameters)
                for field, kind, parameters in value.coordinate_semantics
            ],
        }
        if isinstance(value, EntityReducedMetricSemantics):
            result["reduced_entity_ref"] = value.reduced_entity_ref
            result["reduced_identity_signature"] = value.reduced_identity_signature
        return result
    raise invalid("unsupported family row semantics")


def _semantics(value: object) -> d.DatasetFamilyRowSemantics:
    from marivo.analysis.observation.contracts import (
        CoordinateBinding,
        EntityPresentMetricSemantics,
        EntityReducedMetricSemantics,
        MetricBinding,
    )

    if not isinstance(value, dict):
        raise invalid("invalid family row semantics")
    kind = value.get("kind")
    if kind == "complete_from_schema":
        _obj(value, "kind")
        return d._complete_from_schema()
    if kind not in ("metric/entity-present@v1", "metric/entity-reduced@v1"):
        raise invalid("unregistered family row semantics")
    obj = _obj(
        value,
        "kind metric_bindings coordinate_semantics"
        + (
            " reduced_entity_ref reduced_identity_signature"
            if kind == "metric/entity-reduced@v1"
            else ""
        ),
    )
    metrics: list[MetricBinding] = []
    coordinates: list[CoordinateBinding] = []
    for item in _array(obj["metric_bindings"]):
        parts = _array(item)
        if len(parts) != 6:
            raise invalid("invalid Metric binding")
        unit = None if parts[1] is None else _text(parts[1])
        metrics.append(
            (
                d._make_field_id(_text(parts[0])),
                unit,
                _text(parts[2]),
                _texts(parts[3]),
                _text(parts[4]),
                _text(parts[5]),
            )
        )
    for item in _array(obj["coordinate_semantics"]):
        parts = _array(item)
        if len(parts) != 3:
            raise invalid("invalid coordinate binding")
        coordinates.append((d._make_field_id(_text(parts[0])), _text(parts[1]), _texts(parts[2])))
    if kind == "metric/entity-present@v1":
        return EntityPresentMetricSemantics(
            _token=d._CORE_TOKEN,
            metric_bindings=tuple(metrics),
            coordinate_semantics=tuple(coordinates),
        )
    return EntityReducedMetricSemantics(
        _token=d._CORE_TOKEN,
        reduced_entity_ref=_text(obj["reduced_entity_ref"]),
        reduced_identity_signature=_signature(obj["reduced_identity_signature"]),
        metric_bindings=tuple(metrics),
        coordinate_semantics=tuple(coordinates),
    )


def row_payload(value: d.DatasetRowContract) -> dict[str, object]:
    return {
        "schema_version": value.schema_version,
        "shape_id": _shape_payload(value.shape_id),
        "schema": schema_payload(value.schema),
        "coordinate_field_ids": [item.value for item in value.coordinate_field_ids],
        "key_field_ids": [item.value for item in value.key_field_ids],
        "family_semantics": _semantics_payload(value.family_semantics),
    }


def decode_row(value: object, ids: d._StableIdRegistry) -> d.DatasetRowContract:
    obj = _obj(
        value, "schema_version shape_id schema coordinate_field_ids key_field_ids family_semantics"
    )
    if obj["schema_version"] != 1:
        raise invalid("unsupported row contract")
    return d._make_row_contract(
        schema_version=1,
        shape_id=_shape(obj["shape_id"], ids),
        schema=decode_schema(obj["schema"], ids),
        coordinate_field_ids=tuple(
            d._make_field_id(item) for item in _texts(obj["coordinate_field_ids"])
        ),
        key_field_ids=tuple(d._make_field_id(item) for item in _texts(obj["key_field_ids"])),
        family_semantics=_semantics(obj["family_semantics"]),
    )


def row_set_payload(value: d.DatasetRowSetContract) -> dict[str, object]:
    cardinality = value.cardinality
    if isinstance(cardinality, d._SingletonCardinality):
        card: dict[str, object] = {"kind": "singleton"}
    elif isinstance(cardinality, d._KeyedCardinality):
        bound = cardinality.row_bound
        if isinstance(bound, d._UnknownRowBound):
            bound_payload: dict[str, object] = {"kind": "unknown"}
        elif isinstance(bound, d._StaticRowBound):
            bound_payload = {"kind": "static", "max_rows": bound.max_rows}
        elif isinstance(bound, d._RuntimePolicyRowBound):
            bound_payload = {"kind": "runtime_policy", "policy_id": bound.policy_id}
        else:
            raise invalid("unsupported row bound")
        card = {"kind": "keyed", "row_bound": bound_payload}
    else:
        raise invalid("unsupported cardinality")
    if isinstance(value.ordering, d._UnorderedOrdering):
        order: dict[str, object] = {"kind": "unordered"}
    elif isinstance(value.ordering, d._OrderedOrdering):
        order = {
            "kind": "ordered",
            "terms": [
                {
                    "field_id": term.field_id.value,
                    "direction": term.direction,
                    "nulls": term.nulls,
                    "value_order_contract_id": term.value_order_contract_id,
                }
                for term in value.ordering.terms
            ],
        }
    else:
        raise invalid("unsupported ordering")
    return {"schema_version": value.schema_version, "cardinality": card, "ordering": order}


def decode_row_set(value: object, ids: d._StableIdRegistry) -> d.DatasetRowSetContract:
    obj = _obj(value, "schema_version cardinality ordering")
    if obj["schema_version"] != 1:
        raise invalid("unsupported row-set contract")
    card = obj["cardinality"]
    if not isinstance(card, dict):
        raise invalid("invalid cardinality")
    if card.get("kind") == "singleton":
        _obj(card, "kind")
        cardinality: d.DatasetCardinality = d._singleton_cardinality()
    elif card.get("kind") == "keyed":
        bound = _obj(card, "kind row_bound")["row_bound"]
        if not isinstance(bound, dict):
            raise invalid("invalid row bound")
        if bound.get("kind") == "unknown":
            _obj(bound, "kind")
            row_bound: d.DatasetRowBound = d._unknown_row_bound()
        elif bound.get("kind") == "static":
            row_bound = d._static_row_bound(_int(_obj(bound, "kind max_rows")["max_rows"]))
        elif bound.get("kind") == "runtime_policy":
            row_bound = d._runtime_policy_row_bound(
                _text(_obj(bound, "kind policy_id")["policy_id"]), ids=ids
            )
        else:
            raise invalid("unsupported row bound")
        cardinality = d._keyed_cardinality(row_bound)
    else:
        raise invalid("unsupported cardinality")
    ordering = obj["ordering"]
    if not isinstance(ordering, dict):
        raise invalid("invalid ordering")
    if ordering.get("kind") == "unordered":
        _obj(ordering, "kind")
        order: d.DatasetOrdering = d._unordered_ordering()
    elif ordering.get("kind") == "ordered":
        terms = []
        for raw_term in _array(_obj(ordering, "kind terms")["terms"]):
            term = _obj(raw_term, "field_id direction nulls value_order_contract_id")
            direction = term["direction"]
            nulls = term["nulls"]
            if direction not in ("ascending", "descending") or nulls not in ("first", "last"):
                raise invalid("unsupported order term")
            terms.append(
                d._make_order_term(
                    field_id=d._make_field_id(_text(term["field_id"])),
                    direction="ascending" if direction == "ascending" else "descending",
                    nulls="first" if nulls == "first" else "last",
                    value_order_contract_id=_text(term["value_order_contract_id"]),
                    ids=ids,
                )
            )
        order = d._ordered_ordering(tuple(terms))
    else:
        raise invalid("unsupported ordering")
    return d._make_row_set_contract(schema_version=1, cardinality=cardinality, ordering=order)


def materialization_payload(value: MaterializationContract) -> dict[str, object]:
    return {
        "producer_id": value.producer_id,
        "producer_contract_version": value.producer_contract_version,
        "shape_id": _shape_payload(value.shape_id),
        "quality_contract_id": value.quality_contract_id,
        "quality_contract_version": value.quality_contract_version,
        "evidence_extractor_id": value.evidence_extractor_id,
        "evidence_extractor_version": value.evidence_extractor_version,
        "finding_extractor_id": value.finding_extractor_id,
        "finding_extractor_version": value.finding_extractor_version,
        "validation_output_contract_ids": value.validation_output_contract_ids,
        "retained_private_state_contract_ids": value.retained_private_state_contract_ids,
        "finding_policy_id": value.finding_policy_id,
    }


def _materialization(value: object, ids: d._StableIdRegistry) -> MaterializationContract:
    obj = _obj(
        value,
        "producer_id producer_contract_version shape_id quality_contract_id quality_contract_version evidence_extractor_id evidence_extractor_version finding_extractor_id finding_extractor_version validation_output_contract_ids retained_private_state_contract_ids finding_policy_id",
    )
    return MaterializationContract(
        _text(obj["producer_id"]),
        _int(obj["producer_contract_version"], minimum=1),
        _shape(obj["shape_id"], ids),
        _text(obj["quality_contract_id"]),
        _int(obj["quality_contract_version"], minimum=1),
        _text(obj["evidence_extractor_id"]),
        _int(obj["evidence_extractor_version"], minimum=1),
        _text(obj["finding_extractor_id"]),
        _int(obj["finding_extractor_version"], minimum=1),
        _texts(obj["validation_output_contract_ids"]),
        _texts(obj["retained_private_state_contract_ids"]),
        _text(obj["finding_policy_id"]),
    )


def issue_payload(value: MaterializationIssue) -> dict[str, object]:
    return {
        "kind": value.kind,
        "expected": value.expected,
        "received": value.received,
        "repair": value.repair,
    }


def descriptor_payload(value: ArtifactDescriptor) -> dict[str, object]:
    return {
        "schema": "marivo.dataset_artifact_descriptor/v1",
        "definition_fingerprint": value.definition_fingerprint,
        "row_contract": row_payload(value.row_contract),
        "row_contract_fingerprint": value.row_contract_fingerprint,
        "row_set_contract": row_set_payload(value.row_set_contract),
        "row_set_contract_fingerprint": value.row_set_contract_fingerprint,
        "realized_schema": schema_payload(value.realized_schema),
        "realized_schema_fingerprint": value.realized_schema_fingerprint,
        "bounded_lineage": {
            "facts": value.bounded_lineage.facts,
            "omitted_count": value.bounded_lineage.omitted_count,
        },
        "semantic_dependency_digest": value.semantic_dependency_digest,
        "population_authority": {
            "definition_fingerprint": value.population_authority.definition_fingerprint,
            "entity_ref": value.population_authority.entity_ref,
            "identity_signature": value.population_authority.identity_signature,
            "membership_scope": value.population_authority.membership_scope,
            "version_selection": value.population_authority.version_selection,
            "validation_results": value.population_authority.validation_results,
        },
        "sampling_execution": value.sampling_execution,
        "operator_implementation_versions": value.operator_implementation_versions,
        "dataset_materialization_contract": materialization_payload(
            value.dataset_materialization_contract
        ),
        "storage_receipt": receipt_payload(value.storage_receipt),
        "retained_parts": [
            {
                "role": item.role,
                "contract_id": item.contract_id,
                "contract_version": item.contract_version,
                "storage_receipt": receipt_payload(item.storage_receipt),
            }
            for item in value.retained_parts
        ],
        "quality_summary": value.quality_summary.model_dump(mode="json"),
        "typed_issues": [issue_payload(item) for item in value.typed_issues],
    }


def encode_descriptor(value: ArtifactDescriptor) -> str:
    return canonical_json(descriptor_payload(value))


def decode_descriptor(text: str) -> ArtifactDescriptor:
    from marivo.analysis.observation.contracts import (
        make_family_registry,
        make_ids,
        producer_contract,
    )

    ids = make_ids(())
    obj = _obj(
        parse_json(text),
        "schema definition_fingerprint row_contract row_contract_fingerprint row_set_contract row_set_contract_fingerprint realized_schema realized_schema_fingerprint bounded_lineage semantic_dependency_digest population_authority sampling_execution operator_implementation_versions dataset_materialization_contract storage_receipt retained_parts quality_summary typed_issues",
    )
    if (
        obj["schema"] != "marivo.dataset_artifact_descriptor/v1"
        or obj["sampling_execution"] is not None
    ):
        raise invalid("unsupported Artifact descriptor or sampling contract")
    row = decode_row(obj["row_contract"], ids)
    row_set = decode_row_set(obj["row_set_contract"], ids)
    realized = decode_schema(obj["realized_schema"], ids)
    registry = make_family_registry(ids)
    registry.get(row.shape_id.family_id).validate(row, row_set)
    d._validate_realized_schema(row.schema, realized, ids=ids)
    if any(
        not isinstance(column.physical_type_state, d._ResolvedPhysicalType)
        for column in realized.columns
    ):
        raise invalid("unresolved committed schema")
    lineage = _obj(obj["bounded_lineage"], "facts omitted_count")
    lineage_facts = _texts(lineage["facts"])
    if len(lineage_facts) > 16 or any(
        len(item) > 512 or "\n" in item or "\r" in item for item in lineage_facts
    ):
        raise invalid("unbounded lineage")
    population = _obj(
        obj["population_authority"],
        "definition_fingerprint entity_ref identity_signature membership_scope version_selection validation_results",
    )
    versions = []
    for item in _array(obj["operator_implementation_versions"]):
        pair = _array(item)
        if len(pair) != 2:
            raise invalid("invalid implementation version")
        versions.append((_text(pair[0]), _int(pair[1], minimum=1)))
    parts = []
    for item in _array(obj["retained_parts"]):
        part = _obj(item, "role contract_id contract_version storage_receipt")
        parts.append(
            RetainedPart(
                _text(part["role"]),
                _text(part["contract_id"]),
                _int(part["contract_version"], minimum=1),
                decode_receipt(part["storage_receipt"]),
            )
        )
    issues = []
    for item in _array(obj["typed_issues"]):
        issue = _obj(item, "kind expected received repair")
        issues.append(
            MaterializationIssue(
                _text(issue["kind"]),
                _text(issue["expected"]),
                _text(issue["received"]),
                _text(issue["repair"]),
            )
        )
    try:
        quality = QualitySummary.model_validate(obj["quality_summary"])
    except ValueError as exc:
        raise invalid("invalid quality summary") from exc
    if any(
        isinstance(item, float) and not math.isfinite(item)
        for item in quality.model_dump().values()
    ):
        raise invalid("non-finite quality summary")
    result = ArtifactDescriptor(
        _text(obj["definition_fingerprint"]),
        row,
        row_set,
        realized,
        BoundedLineage(lineage_facts, _int(lineage["omitted_count"])),
        _text(obj["semantic_dependency_digest"]),
        PopulationAuthority(
            _text(population["definition_fingerprint"]),
            _text(population["entity_ref"]),
            _signature(population["identity_signature"]),
            _scope(population["membership_scope"]),
            _selection(population["version_selection"]),
            _validation_results(population["validation_results"]),
        ),
        None,
        tuple(versions),
        _materialization(obj["dataset_materialization_contract"], ids),
        decode_receipt(obj["storage_receipt"]),
        tuple(parts),
        quality,
        tuple(issues),
    )
    _hash(result.semantic_dependency_digest)
    if not re.fullmatch(r"ds_[0-9a-f]{64}", result.definition_fingerprint):
        raise invalid("invalid definition fingerprint")
    if (
        result.row_contract_fingerprint != obj["row_contract_fingerprint"]
        or result.row_set_contract_fingerprint != obj["row_set_contract_fingerprint"]
        or result.realized_schema_fingerprint != obj["realized_schema_fingerprint"]
        or result.storage_receipt.schema_fingerprint != result.realized_schema_fingerprint
    ):
        raise invalid("contract fingerprint mismatch")
    contract = result.dataset_materialization_contract
    if str(contract.shape_id) != str(row.shape_id):
        raise invalid("materialization shape mismatch")
    registration = producer_contract(contract.producer_id)
    expected_contract = MaterializationContract(
        registration.producer_id,
        1,
        row.shape_id,
        registration.quality_id,
        1,
        registration.evidence_id,
        1,
        "none",
        1,
        (registration.validation_id,),
        registration.retained_contract_ids,
        "zero_findings@v1",
    )
    if materialization_payload(contract) != materialization_payload(expected_contract):
        raise invalid("unregistered materialization contract")
    if len({item.role for item in parts}) != len(parts):
        raise invalid("duplicate retained role")
    registered_parts = contract.retained_private_state_contract_ids
    if any(item.contract_id not in registered_parts for item in parts) or any(
        not any(item.contract_id == expected for item in parts) for expected in registered_parts
    ):
        raise invalid("retained contract set mismatch")
    if row.shape_id.family_id == "metric":
        expected_roles = {
            "metric_components." + d._canonical_digest(column.identity.identity_id[7:])[:20]
            for column in row.schema.columns
            if column.role_id == "metric"
            and isinstance(column.identity, d._CatalogFieldIdentity)
            and column.identity.identity_id.startswith("metric:")
        }
        if {item.role for item in parts} != expected_roles:
            raise invalid("retained Metric component roles mismatch")
        if any(
            item.contract_id != "metric.sufficient_components"
            or item.contract_version != 1
            or item.storage_receipt.realized_row_count != result.storage_receipt.realized_row_count
            for item in parts
        ):
            raise invalid("retained Metric component contract or count mismatch")
    if (
        isinstance(row_set.cardinality, d._SingletonCardinality)
        and result.storage_receipt.realized_row_count != 1
    ):
        raise invalid("singleton row count mismatch")
    if (
        isinstance(row_set.cardinality, d._KeyedCardinality)
        and isinstance(row_set.cardinality.row_bound, d._StaticRowBound)
        and result.storage_receipt.realized_row_count > row_set.cardinality.row_bound.max_rows
    ):
        raise invalid("static row count exceeded")
    if encode_descriptor(result) != text:
        raise invalid("non-canonical descriptor value")
    return result


@dataclass(frozen=True, slots=True)
class RunDatasetInput:
    definition_fingerprint: str
    shape_id: str
    row_contract_fingerprint: str
    row_set_contract_fingerprint: str
    bounded_operator_ids: tuple[str, ...]
    bounded_semantic_dependency_refs: tuple[str, ...]


def run_input_payload(value: RunDatasetInput) -> dict[str, object]:
    return {
        "definition_fingerprint": value.definition_fingerprint,
        "shape_id": value.shape_id,
        "row_contract_fingerprint": value.row_contract_fingerprint,
        "row_set_contract_fingerprint": value.row_set_contract_fingerprint,
        "bounded_operator_ids": value.bounded_operator_ids,
        "bounded_semantic_dependency_refs": value.bounded_semantic_dependency_refs,
    }


def decode_run_input(text: str) -> RunDatasetInput:
    obj = _obj(
        parse_json(text),
        "definition_fingerprint shape_id row_contract_fingerprint row_set_contract_fingerprint bounded_operator_ids bounded_semantic_dependency_refs",
    )
    result = RunDatasetInput(
        _text(obj["definition_fingerprint"]),
        _text(obj["shape_id"]),
        _text(obj["row_contract_fingerprint"]),
        _text(obj["row_set_contract_fingerprint"]),
        _texts(obj["bounded_operator_ids"]),
        _texts(obj["bounded_semantic_dependency_refs"]),
    )
    if len(result.bounded_operator_ids) > 64 or len(result.bounded_semantic_dependency_refs) > 64:
        raise invalid("Run input projection exceeded its bound")
    return result


@dataclass(frozen=True, slots=True)
class RunFailure:
    phase: str
    kind: str
    safe_message: str
    safe_location: str
    expected: str
    received: str
    repair: str
    backend_class: str | None = None
    retry_disposition: Literal["retryable", "not_retryable"] = "retryable"


_PHASES = frozenset(
    [
        "authority_resolution",
        "semantic_validation",
        "graph_validation",
        "implementation_registration",
        "execution_boundary",
        "source_binding",
        "ibis_expression_construction",
        "storage_selection",
        "ibis_backend_compile",
        "stage_execution",
        "transfer_guard",
        "output_validation",
        "storage_staging",
        "storage_finalization",
        "quality",
        "evidence",
        "publication",
        "cleanup",
        "presentation",
        "process_lost",
    ]
)


def failure_payload(value: RunFailure) -> dict[str, object]:
    return {
        "phase": value.phase,
        "kind": value.kind,
        "safe_message": value.safe_message,
        "safe_location": value.safe_location,
        "expected": value.expected,
        "received": value.received,
        "repair": value.repair,
        "backend_class": value.backend_class,
        "retry_disposition": value.retry_disposition,
    }


def decode_failure(text: str) -> RunFailure:
    obj = _obj(
        parse_json(text),
        "phase kind safe_message safe_location expected received repair backend_class retry_disposition",
    )
    phase = _text(obj["phase"])
    disposition = obj["retry_disposition"]
    if phase not in _PHASES or disposition not in ("retryable", "not_retryable"):
        raise invalid("unsupported failure variant")
    return RunFailure(
        phase,
        _text(obj["kind"]),
        _text(obj["safe_message"]),
        _text(obj["safe_location"]),
        _text(obj["expected"]),
        _text(obj["received"]),
        _text(obj["repair"]),
        None if obj["backend_class"] is None else _text(obj["backend_class"]),
        "retryable" if disposition == "retryable" else "not_retryable",
    )


@dataclass(frozen=True, slots=True)
class SessionRecord:
    session_ref: str
    name: str
    question: str | None
    report_timezone_name: str
    report_timezone_resolution: Literal["iana", "fixed_offset"]
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class RunRecord:
    run_ref: str
    session_ref: str
    execution_key_digest: str
    admitted_at: str
    dataset_input: RunDatasetInput
    input_artifact_refs: tuple[str, ...]
    lifecycle: Literal["incomplete", "succeeded", "failed"]
    terminal_at: str | None = None
    output_artifact_ref: str | None = None
    failure: RunFailure | None = None


@dataclass(frozen=True, slots=True)
class ResourceRecord:
    run_ref: str
    resource_kind: str
    execution_domain_id: str
    ownership_nonce: str
    cleanup_capability_id: str
    safe_locator: str


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    evidence_digest: str
    finding_count: int
    finding_set_digest: str
    extractor_contract_versions: tuple[str, ...]
    quality_summary_digest: str
    typed_issue_digest: str


def evidence_for(descriptor: ArtifactDescriptor) -> EvidenceRecord:
    contract = descriptor.dataset_materialization_contract
    quality = digest(descriptor.quality_summary.model_dump(mode="json"))
    issues = digest([issue_payload(item) for item in descriptor.typed_issues])
    versions = (
        f"{contract.evidence_extractor_id}@v{contract.evidence_extractor_version}",
        f"{contract.finding_extractor_id}@v{contract.finding_extractor_version}",
    )
    empty = digest([])
    value = {
        "schema": "marivo.dataset_evidence/v1",
        "quality_summary_digest": quality,
        "typed_issue_digest": issues,
        "finding_count": 0,
        "finding_set_digest": empty,
        "extractor_contract_versions": versions,
    }
    return EvidenceRecord(digest(value), 0, empty, versions, quality, issues)


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    artifact_ref: str
    session_ref: str
    execution_key_digest: str
    descriptor: ArtifactDescriptor
    committed_at: str
    producing_run_ref: str
    evidence: EvidenceRecord
