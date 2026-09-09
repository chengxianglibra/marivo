"""Closed immutable v3 metadata and explicit, non-executable value codecs."""

from __future__ import annotations

import hashlib
import json
import math
import re
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Literal, TypeAlias, cast, get_args

from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.datasets.handles import BoundedLineage, CanonicalValue
from marivo.analysis.errors import AnalysisRepair
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.observation.distribution_contracts import semantic_approximation
from marivo.render import Card, RenderableResult
from marivo.semantic._quantile import approximation_class, decode_approximation

if TYPE_CHECKING:
    from marivo.analysis.evidence.types import QualitySummary

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_MAX_PAYLOAD_BYTES = 1_048_576
# Bound realization metadata independently of the generic JSON byte envelope.
_MAX_SAMPLING_REALIZATIONS = 64


def parse_timestamp(value: str) -> datetime:
    """Decode an exact timezone-aware persisted timestamp without raw diagnostics."""
    result: datetime | None = None
    with suppress(ValueError):
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result is None or result.tzinfo is None or result.utcoffset() is None:
        raise invalid("invalid selected runtime timestamp")
    return result


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


@dataclass(frozen=True, slots=True, repr=False)
class EngineReceipt:
    datasource_ref: str
    execution_domain_id: str
    qualified_relation_ref: str
    relation_version_or_snapshot_token: str
    schema_fingerprint: str
    realized_row_count: int
    realized_byte_count: int | None
    immutable_relation_protocol: str = "version_addressed_relation"

    def __post_init__(self) -> None:
        _text(self.datasource_ref)
        _hash(self.execution_domain_id)
        _relative(self.qualified_relation_ref)
        if not self.qualified_relation_ref.endswith(".duckdb"):
            raise invalid("unsupported immutable relation locator")
        for value in (self.relation_version_or_snapshot_token, self.schema_fingerprint):
            _hash(value)
        _int(self.realized_row_count)
        if self.realized_byte_count is not None:
            _int(self.realized_byte_count)
        if self.immutable_relation_protocol != "version_addressed_relation":
            raise invalid("unsupported immutable relation protocol")

    @property
    def kind(self) -> Literal["engine"]:
        return "engine"

    @property
    def identity_digest(self) -> str:
        return digest(receipt_payload(self))


@dataclass(frozen=True, slots=True, repr=False)
class ObjectReceipt:
    object_store_ref: str
    immutable_prefix_or_manifest_ref: str
    object_version_or_manifest_hash: str
    manifest_hash: str
    schema_fingerprint: str
    realized_row_count: int
    realized_byte_count: int
    file_count: int = 1
    parquet_contract_version: int = 1

    def __post_init__(self) -> None:
        _text(self.object_store_ref)
        _relative(self.immutable_prefix_or_manifest_ref)
        _text(self.object_version_or_manifest_hash)
        if self.object_version_or_manifest_hash == "null":
            raise invalid("unversioned object manifest")
        for value in (self.manifest_hash, self.schema_fingerprint):
            _hash(value)
        _int(self.realized_row_count)
        _int(self.realized_byte_count)
        if self.file_count != 1 or self.parquet_contract_version != 1:
            raise invalid("unsupported object Parquet protocol")

    @property
    def kind(self) -> Literal["object"]:
        return "object"

    @property
    def format(self) -> Literal["parquet"]:
        return "parquet"

    @property
    def identity_digest(self) -> str:
        return digest(receipt_payload(self))


StorageReceipt: TypeAlias = LocalReceipt | EngineReceipt | ObjectReceipt


def receipt_payload(value: StorageReceipt) -> dict[str, object]:
    byte_count: dict[str, object] = (
        {"kind": "unavailable"}
        if value.realized_byte_count is None
        else {"kind": "exact", "byte_count": value.realized_byte_count}
    )
    if isinstance(value, EngineReceipt):
        return {
            "kind": "engine",
            "datasource_ref": value.datasource_ref,
            "execution_domain_id": value.execution_domain_id,
            "qualified_relation_ref": value.qualified_relation_ref,
            "immutable_relation_protocol": value.immutable_relation_protocol,
            "relation_version_or_snapshot_token": value.relation_version_or_snapshot_token,
            "schema_fingerprint": value.schema_fingerprint,
            "realized_row_count": value.realized_row_count,
            "realized_byte_count": byte_count,
        }
    if isinstance(value, ObjectReceipt):
        return {
            "kind": "object",
            "object_store_ref": value.object_store_ref,
            "immutable_prefix_or_manifest_ref": value.immutable_prefix_or_manifest_ref,
            "object_version_or_manifest_hash": value.object_version_or_manifest_hash,
            "format": "parquet",
            "parquet_contract_version": value.parquet_contract_version,
            "file_count": value.file_count,
            "manifest_hash": value.manifest_hash,
            "schema_fingerprint": value.schema_fingerprint,
            "realized_row_count": value.realized_row_count,
            "realized_byte_count": byte_count,
        }
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


def decode_receipt(value: object) -> StorageReceipt:
    if isinstance(value, dict) and value.get("kind") == "engine":
        obj = _obj(
            value,
            "kind datasource_ref execution_domain_id qualified_relation_ref immutable_relation_protocol relation_version_or_snapshot_token schema_fingerprint realized_row_count realized_byte_count",
        )
        size = obj["realized_byte_count"]
        count = None
        if size != {"kind": "unavailable"}:
            size_obj = _obj(size, "kind byte_count")
            if size_obj["kind"] != "exact":
                raise invalid("unsupported engine byte count")
            count = _int(size_obj["byte_count"])
        return EngineReceipt(
            _text(obj["datasource_ref"]),
            _text(obj["execution_domain_id"]),
            _text(obj["qualified_relation_ref"]),
            _text(obj["relation_version_or_snapshot_token"]),
            _text(obj["schema_fingerprint"]),
            _int(obj["realized_row_count"]),
            count,
            _text(obj["immutable_relation_protocol"]),
        )
    if isinstance(value, dict) and value.get("kind") == "object":
        obj = _obj(
            value,
            "kind object_store_ref immutable_prefix_or_manifest_ref object_version_or_manifest_hash format parquet_contract_version file_count manifest_hash schema_fingerprint realized_row_count realized_byte_count",
        )
        size_obj = _obj(obj["realized_byte_count"], "kind byte_count")
        if obj["format"] != "parquet" or size_obj["kind"] != "exact":
            raise invalid("unsupported object storage format or byte count")
        return ObjectReceipt(
            _text(obj["object_store_ref"]),
            _text(obj["immutable_prefix_or_manifest_ref"]),
            _text(obj["object_version_or_manifest_hash"]),
            _text(obj["manifest_hash"]),
            _text(obj["schema_fingerprint"]),
            _int(obj["realized_row_count"]),
            _int(size_obj["byte_count"]),
            _int(obj["file_count"]),
            _int(obj["parquet_contract_version"]),
        )
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
    storage_receipt: StorageReceipt

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


@dataclass(frozen=True, slots=True)
class ComparisonInputAuthority:
    """One ordered comparison operand's immutable authority, without member values."""

    role: Literal["current", "baseline"]
    definition_fingerprint: str
    population_authority: PopulationAuthority
    sampling_execution: tuple[SamplingRealization, ...] | None
    source_artifact_refs: tuple[str, ...]
    comparison_basis: str


@dataclass(frozen=True, slots=True)
class DeltaEvidenceSummary:
    """Complete bounded comparison evidence committed with its exact Finding set."""

    coordinate_presence_counts: tuple[tuple[str, int], ...]
    calculation_status_counts: tuple[tuple[str, int], ...]
    relative_status_counts: tuple[tuple[str, int], ...]
    matched_count: int
    unpaired_count: int
    numeric_promotion_id: str
    approximate: bool
    eligible_finding_count: int
    emitted_finding_count: int
    finding_truncated: bool
    finding_set_digest: str


@dataclass(frozen=True, slots=True)
class AttributionEvidenceSummary:
    """Bounded complete-resolution proof retained through result-only selection."""

    method: str
    origin_definition_fingerprint: str
    complete_row_count: int
    scope_count: int
    resolution_count: int
    mapped_membership_digest: str
    max_reconciliation_error: float
    status_counts: tuple[tuple[str, int], ...]
    approximate: bool
    complete: bool
    top_k: int | None
    eligible_finding_count: int
    emitted_finding_count: int
    finding_truncated: bool
    finding_set_digest: str


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
    severity: Literal["warning", "blocking"]
    kind: str
    expected: str
    received: str
    repair: str


@dataclass(frozen=True, slots=True)
class SamplingRealization:
    """Bounded facts for one physically fenced Entity sample, without member values."""

    ordinal: int
    population_definition_fingerprint: str
    target_population_definition_fingerprint: str
    target_rows: int
    seed: int | None
    realized_entity_count: int
    membership_digest: str
    implementation_id: str = "duckdb.entity_reservoir@v1"


def sampling_payload(value: tuple[SamplingRealization, ...] | None) -> object:
    if value is None:
        return None
    return {
        "schema": "marivo.population_sampling_execution/v1",
        "realizations": [
            {
                "ordinal": item.ordinal,
                "population_definition_fingerprint": item.population_definition_fingerprint,
                "target_population_definition_fingerprint": item.target_population_definition_fingerprint,
                "target_rows": item.target_rows,
                "seed": item.seed,
                "realized_entity_count": item.realized_entity_count,
                "membership_digest": item.membership_digest,
                "implementation_id": item.implementation_id,
            }
            for item in value
        ],
    }


def decode_sampling(value: object) -> tuple[SamplingRealization, ...] | None:
    if value is None:
        return None
    obj = _obj(value, "schema realizations")
    if obj["schema"] != "marivo.population_sampling_execution/v1":
        raise invalid("unsupported sampling execution contract")
    records = _array(obj["realizations"])
    if not 1 <= len(records) <= _MAX_SAMPLING_REALIZATIONS:
        raise invalid("invalid sampling realization count")
    result = []
    for ordinal, record in enumerate(records):
        item = _obj(
            record,
            "ordinal population_definition_fingerprint target_population_definition_fingerprint target_rows seed realized_entity_count membership_digest implementation_id",
        )
        sampled = _text(item["population_definition_fingerprint"])
        target = _text(item["target_population_definition_fingerprint"])
        member_digest = _text(item["membership_digest"])
        _hash(member_digest)
        if any(re.fullmatch(r"ds_[0-9a-f]{64}", text) is None for text in (sampled, target)):
            raise invalid("invalid sampling Population definition")
        seed = None if item["seed"] is None else _int(item["seed"])
        target_rows = _int(item["target_rows"], minimum=1)
        count = _int(item["realized_entity_count"])
        if (
            _int(item["ordinal"]) != ordinal
            or item["implementation_id"] != "duckdb.entity_reservoir@v1"
            or (seed is not None and seed > 2**31 - 1)
            or target_rows > 1_000_000_000
            or count > target_rows
            or sampled == target
        ):
            raise invalid("inconsistent sampled Entity realization")
        result.append(
            SamplingRealization(ordinal, sampled, target, target_rows, seed, count, member_digest)
        )
    return tuple(result)


def required_retained_contracts(
    row: d.DatasetRowContract,
    registered: tuple[str, ...],
    *,
    sampled: bool,
) -> tuple[str, ...]:
    """Select required registered state from the exact row semantics and sampling authority."""
    from marivo.analysis.observation.contracts import (
        EntityPresentMetricSemantics,
        EntityReducedMetricSemantics,
    )
    from marivo.analysis.observation.distinct_contracts import (
        DISTINCT_MEMBERSHIP_CONTRACT_IDS,
        membership_part_authorities,
    )

    semantics = row.family_semantics
    component_state = isinstance(
        semantics, (EntityPresentMetricSemantics, EntityReducedMetricSemantics)
    ) and any(binding[3] for binding in semantics.metric_bindings)
    from marivo.analysis.observation.distribution_contracts import (
        DISTRIBUTION_CONTRACT_IDS,
        distribution_part_authorities,
    )

    distribution_state = bool(distribution_part_authorities(row))
    membership_state = bool(membership_part_authorities(row))
    result = tuple(
        name
        for name in registered
        if (name != "metric.sufficient_components" or component_state)
        and (name not in DISTINCT_MEMBERSHIP_CONTRACT_IDS or membership_state)
        and (name not in DISTRIBUTION_CONTRACT_IDS or distribution_state)
    )
    if sampled and "population_sampling_state" not in result:
        result += ("population_sampling_state",)
    return result


def finding_extractor(row: d.DatasetRowContract, producer_id: str) -> str:
    if row.shape_id.family_id == "delta":
        return "delta_finding"
    if row.shape_id.family_id == "attribution" and producer_id in (
        "delta.attribute",
        "delta.attribute_expanded",
    ):
        return "contribution_finding"
    return "none"


def finding_policy(row: d.DatasetRowContract, producer_id: str) -> str:
    if row.shape_id.family_id == "delta" and row.shape_id.local_shape_id != "entity":
        return "delta_findings@v1"
    if (
        row.shape_id.family_id == "attribution"
        and producer_id in ("delta.attribute", "delta.attribute_expanded")
        and not any(field.identity.kind == "entity_identity" for field in row.schema.columns)
    ):
        return "contribution_findings@v1"
    return "zero_findings@v1"


@dataclass(frozen=True, slots=True)
class ArtifactDescriptor:
    definition_fingerprint: str
    row_contract: d.DatasetRowContract
    row_set_contract: d.DatasetRowSetContract
    realized_schema: d.DatasetSchema
    bounded_lineage: BoundedLineage
    semantic_dependency_digest: str
    population_authority: PopulationAuthority
    sampling_execution: tuple[SamplingRealization, ...] | None
    operator_implementation_versions: tuple[tuple[str, int], ...]
    dataset_materialization_contract: MaterializationContract
    storage_receipt: StorageReceipt
    retained_parts: tuple[RetainedPart, ...]
    quality_summary: QualitySummary
    typed_issues: tuple[MaterializationIssue, ...] = ()
    comparison_basis: str | None = None
    comparison_inputs: tuple[ComparisonInputAuthority, ...] = ()
    delta_evidence: DeltaEvidenceSummary | None = None
    attribution_evidence: AttributionEvidenceSummary | None = None
    attribution_fold_authority: tuple[str, str] | None = None

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
    from marivo.analysis.operators.attribution_contracts import AttributionSemantics
    from marivo.analysis.operators.contracts import DeltaSemantics

    if isinstance(value, AttributionSemantics):
        from marivo.analysis.materialization.attribution_codec import attribution_semantics_payload

        return attribution_semantics_payload(value)

    if isinstance(value, DeltaSemantics):
        return {
            "kind": value.kind,
            "metric_ref": value.metric_ref,
            "metric_unit": value.metric_unit,
            "numeric_type": value.numeric_type,
            "exact_empty_zero": value.exact_empty_zero,
            "current_time_field_name": value.current_time_field_name,
            "baseline_time_field_name": value.baseline_time_field_name,
            "current_fold_authority": value.current_fold_authority,
            "baseline_fold_authority": value.baseline_fold_authority,
            "approximation_class": value.approximation_class,
        }
    from marivo.analysis.observation.contracts import (
        EntityPresentMetricSemantics,
        EntityReducedMetricSemantics,
    )

    if isinstance(value, d._CompleteFromSchema):
        return {"kind": "complete_from_schema"}
    if isinstance(value, (EntityPresentMetricSemantics, EntityReducedMetricSemantics)):
        result: dict[str, object] = {
            "kind": value.kind,
            "fold_authority": value.fold_authority,
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


def _retained_fold_payload(value: object) -> str:
    """Decode the bounded typed fold closure, rather than an ordinary text label."""
    from marivo.analysis.observation.fold_contracts import decode_fold_authority

    if type(value) is not str or not value or len(value.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise invalid("invalid retained fold authority byte bound")
    try:
        decode_fold_authority(value)
    except (ValueError, TypeError, RecursionError) as exc:
        raise invalid("invalid retained fold authority") from exc
    return value


def _attribution_fold_pair(value: object) -> tuple[str, str] | None:
    if value is None:
        return None
    pair = _array(value)
    if len(pair) != 2:
        raise invalid("Attribution requires two exact side fold authorities")
    return (_retained_fold_payload(pair[0]), _retained_fold_payload(pair[1]))


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
    if kind == "attribution/metric@v1":
        from marivo.analysis.materialization.attribution_codec import decode_attribution_semantics

        return decode_attribution_semantics(value)
    if kind == "delta/metric@v1":
        from marivo.analysis.operators.contracts import DeltaSemantics

        obj = _obj(
            value,
            "kind metric_ref metric_unit numeric_type exact_empty_zero current_time_field_name baseline_time_field_name current_fold_authority baseline_fold_authority approximation_class",
        )
        exact_empty_zero = obj["exact_empty_zero"]
        if type(exact_empty_zero) is not bool:
            raise invalid("invalid Delta empty-set policy")
        if obj["approximation_class"] not in (
            "exact",
            "sampled_population",
            "semantic_percentile",
            "sampled_semantic_percentile",
        ):
            raise invalid("invalid Delta approximation class")
        return DeltaSemantics(
            _token=d._CORE_TOKEN,
            metric_ref=_text(obj["metric_ref"]),
            metric_unit=None if obj["metric_unit"] is None else _text(obj["metric_unit"]),
            numeric_type=_text(obj["numeric_type"]),
            exact_empty_zero=exact_empty_zero,
            current_time_field_name=None
            if obj["current_time_field_name"] is None
            else _text(obj["current_time_field_name"]),
            baseline_time_field_name=None
            if obj["baseline_time_field_name"] is None
            else _text(obj["baseline_time_field_name"]),
            current_fold_authority=_retained_fold_payload(obj["current_fold_authority"]),
            baseline_fold_authority=_retained_fold_payload(obj["baseline_fold_authority"]),
            approximation_class=decode_approximation(obj["approximation_class"]),
        )
    if kind == "complete_from_schema":
        _obj(value, "kind")
        return d._complete_from_schema()
    if kind not in ("metric/entity-present@v1", "metric/entity-reduced@v1"):
        raise invalid("unregistered family row semantics")
    obj = _obj(
        value,
        "kind fold_authority metric_bindings coordinate_semantics"
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
    fold_authority = _retained_fold_payload(obj["fold_authority"])
    if kind == "metric/entity-present@v1":
        return EntityPresentMetricSemantics(
            _token=d._CORE_TOKEN,
            fold_authority=fold_authority,
            metric_bindings=tuple(metrics),
            coordinate_semantics=tuple(coordinates),
        )
    return EntityReducedMetricSemantics(
        _token=d._CORE_TOKEN,
        reduced_entity_ref=_text(obj["reduced_entity_ref"]),
        reduced_identity_signature=_signature(obj["reduced_identity_signature"]),
        fold_authority=fold_authority,
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
        "severity": value.severity,
        "kind": value.kind,
        "expected": value.expected,
        "received": value.received,
        "repair": value.repair,
    }


def descriptor_payload(value: ArtifactDescriptor) -> dict[str, object]:
    from marivo.analysis.materialization.attribution_codec import attribution_evidence_payload
    from marivo.analysis.materialization.comparison_codec import (
        comparison_inputs_payload,
        delta_evidence_payload,
    )

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
        "sampling_execution": sampling_payload(value.sampling_execution),
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
        "comparison_basis": value.comparison_basis,
        "comparison_inputs": comparison_inputs_payload(value.comparison_inputs),
        "delta_evidence": delta_evidence_payload(value.delta_evidence),
        "attribution_evidence": attribution_evidence_payload(value.attribution_evidence),
        "attribution_fold_authority": value.attribution_fold_authority,
    }


def encode_descriptor(value: ArtifactDescriptor) -> str:
    return canonical_json(descriptor_payload(value))


def decode_descriptor(text: str) -> ArtifactDescriptor:
    from marivo.analysis.evidence.types import QualitySummary
    from marivo.analysis.materialization.attribution_codec import decode_attribution_evidence
    from marivo.analysis.materialization.comparison_codec import (
        comparison_basis_text,
        decode_comparison_inputs,
        decode_delta_evidence,
    )
    from marivo.analysis.observation.contracts import (
        make_family_registry,
        make_ids,
        producer_contract,
    )

    ids = make_ids(())
    obj = _obj(
        parse_json(text),
        "schema definition_fingerprint row_contract row_contract_fingerprint row_set_contract row_set_contract_fingerprint realized_schema realized_schema_fingerprint bounded_lineage semantic_dependency_digest population_authority sampling_execution operator_implementation_versions dataset_materialization_contract storage_receipt retained_parts quality_summary typed_issues comparison_basis comparison_inputs delta_evidence attribution_evidence attribution_fold_authority",
    )
    if obj["schema"] != "marivo.dataset_artifact_descriptor/v1":
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
        issue = _obj(item, "severity kind expected received repair")
        if issue["severity"] not in ("warning", "blocking"):
            raise invalid("unsupported typed issue severity")
        issues.append(
            MaterializationIssue(
                "warning" if issue["severity"] == "warning" else "blocking",
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
        decode_sampling(obj["sampling_execution"]),
        tuple(versions),
        _materialization(obj["dataset_materialization_contract"], ids),
        decode_receipt(obj["storage_receipt"]),
        tuple(parts),
        quality,
        tuple(issues),
        None if obj["comparison_basis"] is None else comparison_basis_text(obj["comparison_basis"]),
        decode_comparison_inputs(obj["comparison_inputs"]),
        decode_delta_evidence(obj["delta_evidence"]),
        decode_attribution_evidence(obj["attribution_evidence"]),
        _attribution_fold_pair(obj["attribution_fold_authority"]),
    )
    if result.comparison_basis is not None:
        from marivo.analysis.operators.contracts import decode_comparison_basis

        decode_comparison_basis(result.comparison_basis)
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
    if (
        isinstance(row_set.ordering, d._OrderedOrdering)
        and tuple(term.field_id for term in row_set.ordering.terms) != row.key_field_ids
        and ("dataset.final_row_key_unique", 0)
        not in result.population_authority.validation_results
    ):
        raise invalid("ordered output lacks independent final row-key validation")
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
        finding_extractor(row, registration.producer_id),
        1,
        (registration.validation_id,),
        required_retained_contracts(
            row, registration.retained_contract_ids, sampled=result.sampling_execution is not None
        ),
        finding_policy(row, registration.producer_id),
    )
    if materialization_payload(contract) != materialization_payload(expected_contract):
        raise invalid("unregistered materialization contract")
    if row.shape_id.family_id == "delta":
        from marivo.analysis.operators.contracts import DeltaSemantics

        if len(result.comparison_inputs) != 2 or result.delta_evidence is None:
            raise invalid("missing complete comparison authority or Evidence")
        evidence = result.delta_evidence
        semantics = row.family_semantics
        if (
            not isinstance(semantics, DeltaSemantics)
            or evidence.numeric_promotion_id != "lossless_signed:" + semantics.numeric_type + "@v1"
        ):
            raise invalid("Delta Evidence numeric promotion mismatch")
        expected_approximation = approximation_class(
            sampled=any(item.sampling_execution is not None for item in result.comparison_inputs),
            semantic=semantic_approximation(
                (semantics.current_fold_authority, semantics.baseline_fold_authority)
            ),
        )
        if evidence.approximate != (expected_approximation != "exact"):
            raise invalid("Delta Evidence approximation binding mismatch")
        if semantics.approximation_class != expected_approximation:
            raise invalid("Delta row interpretation differs from retained approximation authority")
        operand_sampling = {
            digest(sampling_payload((replace(receipt, ordinal=0),)))
            for operand in result.comparison_inputs
            for receipt in operand.sampling_execution or ()
        }
        retained_sampling = {
            digest(sampling_payload((replace(receipt, ordinal=0),)))
            for receipt in result.sampling_execution or ()
        }
        if operand_sampling != retained_sampling:
            raise invalid("comparison operand realizations differ from retained sampling authority")
        if (
            sum(count for _, count in evidence.calculation_status_counts)
            != result.storage_receipt.realized_row_count
        ):
            raise invalid("Delta Evidence row count mismatch")
        if row.shape_id.local_shape_id == "entity" and evidence.eligible_finding_count:
            raise invalid("identity-bearing Delta cannot emit Findings")
    elif row.shape_id.family_id == "attribution":
        from marivo.analysis.materialization.attribution_publication import validate_descriptor

        validate_descriptor(result)
    elif result.comparison_inputs or result.delta_evidence is not None:
        raise invalid("comparison authority outside Delta family")
    if row.shape_id.family_id != "attribution" and (
        result.attribution_evidence is not None or result.attribution_fold_authority is not None
    ):
        raise invalid("Attribution Evidence outside Attribution family")
    if len({item.role for item in parts}) != len(parts):
        raise invalid("duplicate retained role")
    registered_parts = tuple(
        name
        for name in contract.retained_private_state_contract_ids
        if name != "attribution.reconciliation"
    )
    if any(item.contract_id not in registered_parts for item in parts) or any(
        not any(item.contract_id == expected for item in parts) for expected in registered_parts
    ):
        raise invalid("retained contract set mismatch")
    sampling_parts = tuple(
        item for item in parts if item.contract_id == "population_sampling_state"
    )
    if result.sampling_execution is None:
        if sampling_parts or contract.producer_id == "population.sample":
            raise invalid("missing required sampling execution")
    else:
        if (
            len(sampling_parts) != 1
            or sampling_parts[0].role != "population_sampling_state"
            or sampling_parts[0].contract_version != 1
            or sampling_parts[0].storage_receipt.realized_row_count != 1
            or ("population.sample", 1) not in result.operator_implementation_versions
        ):
            raise invalid("inconsistent retained sampling state")
        if row.shape_id.family_id == "population" and (
            len(result.sampling_execution) != 1
            or result.sampling_execution[0].population_definition_fingerprint
            != result.definition_fingerprint
            or result.sampling_execution[0].realized_entity_count
            != result.storage_receipt.realized_row_count
        ):
            raise invalid("sampled Population receipt mismatch")
    if row.shape_id.family_id == "metric":
        from marivo.analysis.observation.contracts import (
            EntityPresentMetricSemantics,
            EntityReducedMetricSemantics,
        )

        semantics = row.family_semantics
        if not isinstance(semantics, (EntityPresentMetricSemantics, EntityReducedMetricSemantics)):
            raise invalid("missing Metric row semantics")
        retained_fields = {binding[0] for binding in semantics.metric_bindings if binding[3]}
        expected_roles = {
            "metric_components." + d._canonical_digest(column.identity.identity_id[7:])[:20]
            for column in row.schema.columns
            if column.role_id == "metric"
            and column.field_id in retained_fields
            and isinstance(column.identity, d._CatalogFieldIdentity)
            and column.identity.identity_id.startswith("metric:")
        }
        component_parts = tuple(
            item for item in parts if item.contract_id == "metric.sufficient_components"
        )
        if {item.role for item in component_parts} != expected_roles:
            raise invalid("retained Metric component roles mismatch")
        if any(
            item.contract_id != "metric.sufficient_components"
            or item.contract_version != 1
            or item.storage_receipt.realized_row_count != result.storage_receipt.realized_row_count
            for item in component_parts
        ):
            raise invalid("retained Metric component contract or count mismatch")
    if row.shape_id.family_id == "delta":
        from marivo.analysis.operators.attribution_contracts import delta_part_authorities

        expected_roles = {role for role, _ in delta_part_authorities(row)}
        component_parts = tuple(
            item for item in parts if item.contract_id == "delta.sufficient_components"
        )
        if {item.role for item in component_parts} != expected_roles or any(
            item.contract_id != "delta.sufficient_components"
            or item.contract_version != 1
            or item.storage_receipt.realized_row_count != result.storage_receipt.realized_row_count
            for item in component_parts
        ):
            raise invalid("retained Delta component role, contract or count mismatch")
    from marivo.analysis.observation.distinct_contracts import (
        DISTINCT_MEMBERSHIP_CONTRACT_IDS,
        membership_part_authorities,
    )
    from marivo.analysis.observation.distribution_contracts import (
        DISTRIBUTION_CONTRACT_IDS,
        distribution_part_authorities,
    )

    distribution_roles = {role for role, _ in distribution_part_authorities(row)}
    distribution_parts = tuple(
        item for item in parts if item.contract_id in DISTRIBUTION_CONTRACT_IDS
    )
    if {item.role for item in distribution_parts} != distribution_roles:
        raise invalid("retained distribution roles mismatch")
    if distribution_parts:
        receipt = result.storage_receipt
        if not isinstance(receipt, EngineReceipt) or any(
            item.contract_id != f"{row.shape_id.family_id}.distribution"
            or item.contract_version != 1
            or not isinstance(item.storage_receipt, EngineReceipt)
            or item.storage_receipt.datasource_ref != receipt.datasource_ref
            or item.storage_receipt.execution_domain_id != receipt.execution_domain_id
            for item in distribution_parts
        ):
            raise invalid("private distribution requires the exact primary engine sink")
    membership_roles = {role for role, _ in membership_part_authorities(row)}
    membership_parts = tuple(
        item for item in parts if item.contract_id in DISTINCT_MEMBERSHIP_CONTRACT_IDS
    )
    if {item.role for item in membership_parts} != membership_roles:
        raise invalid("retained distinct membership roles mismatch")
    if membership_parts:
        primary_receipt = result.storage_receipt
        if not isinstance(primary_receipt, EngineReceipt) or any(
            item.contract_id != f"{row.shape_id.family_id}.distinct_membership"
            or item.contract_version != 1
            or not isinstance(item.storage_receipt, EngineReceipt)
            or item.storage_receipt.datasource_ref != primary_receipt.datasource_ref
            or item.storage_receipt.execution_domain_id != primary_receipt.execution_domain_id
            for item in membership_parts
        ):
            raise invalid("private membership requires the exact primary engine sink")
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


JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
RunFailurePhase: TypeAlias = Literal[
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


@dataclass(frozen=True, slots=True, repr=False)
class RunDatasetInput(RenderableResult):
    definition_fingerprint: str
    shape_id: d.DatasetShapeId
    row_contract_fingerprint: str
    row_set_contract_fingerprint: str
    bounded_operator_ids: tuple[str, ...]
    bounded_semantic_dependency_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        for value in (
            self.definition_fingerprint,
            self.row_contract_fingerprint,
            self.row_set_contract_fingerprint,
        ):
            _text(value)
        if type(self.shape_id) is not d.DatasetShapeId:
            raise invalid("Run shape must be an exact registered DatasetShapeId")
        for values in (self.bounded_operator_ids, self.bounded_semantic_dependency_refs):
            if type(values) is not tuple or len(values) > 64 or len(set(values)) != len(values):
                raise invalid("invalid bounded Run input provenance")
            for value in values:
                _text(value)

    def _repr_identity(self) -> str:
        return f"RunDatasetInput shape={self.shape_id} definition={self.definition_fingerprint}"[
            :256
        ]

    def _card(self) -> Card:
        return (
            Card(identity=self._repr_identity(), available=(".show()",))
            .listing("operators", self.bounded_operator_ids)
            .listing("semantic_dependencies", self.bounded_semantic_dependency_refs)
        )


def run_input_payload(value: RunDatasetInput) -> dict[str, object]:
    return {
        "definition_fingerprint": value.definition_fingerprint,
        "shape_id": _shape_payload(value.shape_id),
        "row_contract_fingerprint": value.row_contract_fingerprint,
        "row_set_contract_fingerprint": value.row_set_contract_fingerprint,
        "bounded_operator_ids": value.bounded_operator_ids,
        "bounded_semantic_dependency_refs": value.bounded_semantic_dependency_refs,
    }


def decode_run_input(text: str) -> RunDatasetInput:
    from marivo.analysis.observation.contracts import make_ids

    obj = _obj(
        parse_json(text),
        "definition_fingerprint shape_id row_contract_fingerprint row_set_contract_fingerprint bounded_operator_ids bounded_semantic_dependency_refs",
    )
    try:
        shape = _shape(obj["shape_id"], make_ids(()))
    except DatasetConstructionError:
        raise invalid("unregistered persisted Run shape") from None
    result = RunDatasetInput(
        _text(obj["definition_fingerprint"]),
        shape,
        _text(obj["row_contract_fingerprint"]),
        _text(obj["row_set_contract_fingerprint"]),
        _texts(obj["bounded_operator_ids"]),
        _texts(obj["bounded_semantic_dependency_refs"]),
    )
    if len(result.bounded_operator_ids) > 64 or len(result.bounded_semantic_dependency_refs) > 64:
        raise invalid("Run input projection exceeded its bound")
    return result


@dataclass(frozen=True, slots=True, repr=False, init=False)
class RunFailure(RenderableResult):
    phase: RunFailurePhase
    kind: str
    safe_message: str
    safe_location: str | None
    _expected_json: str
    _received_json: str
    repair: AnalysisRepair | None
    backend_class: str | None
    retry_disposition: Literal["retryable", "not_retryable"]

    def __init__(
        self,
        phase: RunFailurePhase,
        kind: str,
        safe_message: str,
        safe_location: str | None,
        expected: JsonValue,
        received: JsonValue,
        repair: AnalysisRepair | None,
        backend_class: str | None = None,
        retry_disposition: Literal["retryable", "not_retryable"] = "retryable",
    ) -> None:
        object.__setattr__(self, "phase", phase)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "safe_message", safe_message)
        object.__setattr__(self, "safe_location", safe_location)
        object.__setattr__(self, "_expected_json", canonical_json(_failure_json(expected)))
        object.__setattr__(self, "_received_json", canonical_json(_failure_json(received)))
        object.__setattr__(self, "repair", repair)
        object.__setattr__(self, "backend_class", backend_class)
        object.__setattr__(self, "retry_disposition", retry_disposition)
        self.__post_init__()

    @property
    def expected(self) -> JsonValue:
        """Return an isolated JSON projection of the immutable expected facts."""
        return _failure_json(parse_json(self._expected_json))

    @property
    def received(self) -> JsonValue:
        """Return an isolated JSON projection of the immutable received facts."""
        return _failure_json(parse_json(self._received_json))

    def __post_init__(self) -> None:
        if self.phase not in _PHASES or self.retry_disposition not in (
            "retryable",
            "not_retryable",
        ):
            raise invalid("unsupported Run failure variant")
        if self.kind not in ("execution_failed", "process_lost") or self.backend_class is not None:
            raise invalid("unregistered Run failure kind or backend class")
        for value in (self.kind, self.safe_message):
            _text(value)
        for optional_value in (self.safe_location, self.backend_class):
            if optional_value is not None:
                _text(optional_value)
        _failure_json(self.expected)
        _failure_json(self.received)
        if self.repair is not None and type(self.repair) is not AnalysisRepair:
            raise invalid("Run repair must be an exact AnalysisRepair")
        repair_payload = None if self.repair is None else self.repair.model_dump(mode="json")
        if len(canonical_json((self.expected, self.received, repair_payload)).encode()) > 8192:
            raise invalid("Run failure diagnostic byte bound exceeded")

    def _repr_identity(self) -> str:
        return f"RunFailure phase={self.phase} kind={self.kind}"[:256]

    def _card(self) -> Card:
        card = (
            Card(identity=self._repr_identity(), available=(".show()",))
            .field("message", self.safe_message)
            .field("retry_disposition", self.retry_disposition)
        )
        if self.repair is not None:
            card.field("repair", self.repair.action)
        return card


_PHASES: frozenset[str] = frozenset(get_args(RunFailurePhase))


def run_failure_phase(stage: str, owning_phase: str) -> RunFailurePhase:
    """Project a reader/action error stage onto the closed persisted Run phases."""
    if owning_phase not in _PHASES:
        raise invalid("unsupported owning Run phase")
    # Both alternatives have been checked against the closed phase registry.
    return cast("RunFailurePhase", stage if stage in _PHASES else owning_phase)


def _failure_json(value: object, *, depth: int = 0) -> JsonValue:
    if depth > 6:
        raise invalid("Run failure JSON depth exceeded")
    if value is None or type(value) in (bool, int):
        return cast("bool | int | None", value)
    if isinstance(value, str):
        return _text(value, empty=True)
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, list) and len(value) <= 64:
        return [_failure_json(item, depth=depth + 1) for item in value]
    if isinstance(value, dict) and len(value) <= 64:
        return {_text(key): _failure_json(item, depth=depth + 1) for key, item in value.items()}
    raise invalid("unsupported bounded Run failure JSON")


def failure_payload(value: RunFailure) -> dict[str, object]:
    return {
        "phase": value.phase,
        "kind": value.kind,
        "safe_message": value.safe_message,
        "safe_location": value.safe_location,
        "expected": value.expected,
        "received": value.received,
        "repair": None if value.repair is None else value.repair.model_dump(mode="json"),
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
    repair = None
    if obj["repair"] is not None:
        repair_obj = _obj(obj["repair"], "kind action help_target snippet candidates")
        _failure_json(repair_obj)
        try:
            repair = AnalysisRepair.model_validate(repair_obj)
        except ValueError:
            raise invalid("invalid structured Run repair") from None
    if len(canonical_json((obj["expected"], obj["received"], obj["repair"])).encode()) > 8192:
        raise invalid("Run failure diagnostic byte bound exceeded")
    return RunFailure(
        run_failure_phase(phase, phase),
        _text(obj["kind"]),
        _text(obj["safe_message"]),
        None if obj["safe_location"] is None else _text(obj["safe_location"]),
        _failure_json(obj["expected"]),
        _failure_json(obj["received"]),
        repair,
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
    from marivo.analysis.materialization.attribution_codec import attribution_evidence_payload
    from marivo.analysis.materialization.comparison_codec import delta_evidence_payload

    contract = descriptor.dataset_materialization_contract
    quality = digest(descriptor.quality_summary.model_dump(mode="json"))
    issues = digest([issue_payload(item) for item in descriptor.typed_issues])
    versions = (
        f"{contract.evidence_extractor_id}@v{contract.evidence_extractor_version}",
        f"{contract.finding_extractor_id}@v{contract.finding_extractor_version}",
    )
    summary = descriptor.delta_evidence or descriptor.attribution_evidence
    count = 0 if summary is None else summary.emitted_finding_count
    empty = digest([]) if summary is None else summary.finding_set_digest
    value = {
        "schema": "marivo.dataset_evidence/v1",
        "quality_summary_digest": quality,
        "typed_issue_digest": issues,
        "finding_count": count,
        "finding_set_digest": empty,
        "extractor_contract_versions": versions,
    }
    if descriptor.delta_evidence is not None:
        value["delta_evidence"] = delta_evidence_payload(descriptor.delta_evidence)
        value["comparison_sampling"] = [
            sampling_payload(item.sampling_execution) for item in descriptor.comparison_inputs
        ]
    if descriptor.attribution_evidence is not None:
        value["attribution_evidence"] = attribution_evidence_payload(
            descriptor.attribution_evidence
        )
    return EvidenceRecord(digest(value), count, empty, versions, quality, issues)


@dataclass(frozen=True, slots=True)
class ArtifactMetadata:
    """Selected Artifact and producer authority, independent of its Evidence row."""

    artifact_ref: str
    session_ref: str
    execution_key_digest: str
    descriptor: ArtifactDescriptor
    committed_at: str
    producing_run_ref: str


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    artifact_ref: str
    session_ref: str
    execution_key_digest: str
    descriptor: ArtifactDescriptor
    committed_at: str
    producing_run_ref: str
    evidence: EvidenceRecord
