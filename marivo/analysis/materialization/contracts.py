"""Closed immutable v3 metadata and explicit, non-executable value codecs."""

from __future__ import annotations

import hashlib
import json
import math
import re
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Literal, TypeAlias, cast, get_args

import pyarrow as pa

from marivo.analysis.core.time_authority import TemporalExecution
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.datasets.handles import BoundedLineage, CanonicalValue
from marivo.analysis.errors import AnalysisRepair
from marivo.analysis.materialization.errors import IntegrityError
from marivo.render import Card, RenderableResult

if TYPE_CHECKING:
    from marivo.analysis.materialization.association_codec import AssociationEvidenceSummary
    from marivo.analysis.materialization.candidate_codec import CandidateEvidenceSummary
    from marivo.analysis.materialization.forecast_codec import ForecastEvidenceSummary
    from marivo.analysis.materialization.quality import QualitySummary
    from marivo.analysis.observation.contracts import ContractEvidence
    from marivo.analysis.operators.registry import MethodContract

_HASH = re.compile(r"[0-9a-f]{64}\Z")
FINDING_CAP = 1000
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
    text: str | None = None
    with suppress(TypeError, ValueError, RecursionError):
        text = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
    if text is None:
        raise invalid("non-canonical metadata")
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
    try:
        value: object = json.loads(text, object_pairs_hook=_pairs)
        if canonical_json(value) != text:
            raise invalid("non-canonical JSON encoding")
        return value
    except (TypeError, ValueError, RecursionError):
        pass
    raise invalid("invalid JSON metadata")


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
    if type(value) is not list:
        raise invalid("invalid array")
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


StorageReceipt: TypeAlias = LocalReceipt


def receipt_payload(value: StorageReceipt) -> dict[str, object]:
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


def _exchange_invalid(received: str) -> IntegrityError:
    return IntegrityError(
        expected="one exact versioned Analysis exchange binding",
        received=received,
        repair="Use the owning row, method, input binding and selected receipts together.",
        stage="exchange",
    )


def _arrow_fingerprint(schema: pa.Schema) -> str:
    return hashlib.sha256(schema.serialize().to_pybytes()).hexdigest()


@dataclass(frozen=True, slots=True)
class ExchangePart:
    """One first-round private state role and its keyed physical schema."""

    role: str
    contract_id: str
    contract_version: int
    schema: pa.Schema
    keys: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not d._is_stable_identifier(self.role)
            or not d._is_stable_identifier(self.contract_id)
            or type(self.contract_version) is not int
            or self.contract_version < 1
            or not isinstance(self.schema, pa.Schema)
            or type(self.keys) is not tuple
        ):
            raise _exchange_invalid("invalid retained role contract")


@dataclass(frozen=True, slots=True)
class ExchangeRecord:
    """Closed v1 codec value; publication of this inactive slice belongs to S1."""

    row_fingerprint: str
    row_set_fingerprint: str
    domain_fingerprint: str
    quantity_fingerprint: str
    method_id: str
    method_version: int
    method_fingerprint: str
    cell_reasons: tuple[tuple[str, tuple[str, ...]], ...]
    input_binding: str
    fields: tuple[tuple[str, str, bool], ...]
    schema_fingerprint: str
    parts: tuple[tuple[str, str, int, str, str | None], ...]
    declarations: tuple[str, ...]
    deductions: tuple[str, ...]
    completed_checks: tuple[str, ...]
    pending_checks: tuple[str, ...]
    receipt_identity: str | None


@dataclass(frozen=True, slots=True)
class ExchangeBinding:
    """Run-local view of existing Dataset, method and receipt owners."""

    row: d.DatasetRowContract
    rows: d.DatasetRowSetContract
    domain: d.AnalysisDomain
    quantity: d.QuantityState
    method: MethodContract
    evidence: ContractEvidence
    input_binding: str
    schema: pa.Schema
    parts: tuple[ExchangePart, ...]
    storage_receipt: StorageReceipt | None = None
    retained_parts: tuple[RetainedPart, ...] = ()

    def __post_init__(self) -> None:
        from marivo.analysis.materialization.storage import _matches_type, _realized_schema
        from marivo.analysis.observation.contracts import ContractEvidence
        from marivo.analysis.operators.registry import MethodContract

        if (
            not isinstance(self.row, d.DatasetRowContract)
            or not isinstance(self.rows, d.DatasetRowSetContract)
            or not isinstance(self.domain, d.AnalysisDomain)
            or not isinstance(self.quantity, d.QuantityState)
            or not isinstance(self.method, MethodContract)
            or not isinstance(self.evidence, ContractEvidence)
            or not isinstance(self.schema, pa.Schema)
            or type(self.parts) is not tuple
            or any(not isinstance(part, ExchangePart) for part in self.parts)
            or (
                self.storage_receipt is not None
                and not isinstance(self.storage_receipt, LocalReceipt)
            )
            or type(self.retained_parts) is not tuple
            or any(not isinstance(part, RetainedPart) for part in self.retained_parts)
        ):
            raise _exchange_invalid("invalid exchange owner")

        by_id = {field.field_id: field.name for field in self.row.schema.columns}
        keys = tuple(by_id[field_id] for field_id in self.row.key_field_ids)
        domain_keys = (
            (self.domain.member_identity,)
            if isinstance(self.domain, (d._EntityDomain, d._SelectedEntityDomain))
            else self.domain.group_fields
            if isinstance(self.domain, d._GroupDomain)
            else ()
            if isinstance(self.domain, d._SingletonDomain)
            else None
        )
        if (
            not d._is_stable_identifier(self.input_binding)
            or domain_keys != self.row.key_field_ids
            or (not keys and self.rows.cardinality.kind != "singleton")
            or (bool(keys) and self.rows.cardinality.kind == "singleton")
            or tuple(self.schema.names) != (*keys, "value", "cell_tag", "cell_reason")
            or tuple(field.name for field in self.row.schema.columns) != tuple(self.schema.names)
            or any(
                field.nullable != arrow.nullable
                or not _matches_type(field.logical_type_id, arrow.type)
                for field, arrow in zip(self.row.schema.columns, self.schema, strict=True)
            )
            or any(self.schema.field(key).nullable for key in keys)
            or any(
                not (
                    pa.types.is_int64(self.schema.field(key).type)
                    or pa.types.is_string(self.schema.field(key).type)
                )
                for key in keys
            )
            or not self.schema.field("value").nullable
            or self.schema.field("cell_tag").type != pa.string()
            or self.schema.field("cell_tag").nullable
            or self.schema.field("cell_reason").type != pa.string()
            or not self.schema.field("cell_reason").nullable
        ):
            raise _exchange_invalid("invalid ordered exchange schema or key")
        quantity_domain = (
            self.quantity.domain
            if isinstance(self.quantity, d._ObservedQuantity)
            else self.quantity.output_domain
            if isinstance(self.quantity, d._RowStatisticQuantity)
            else None
        )
        quantity_parts = (
            self.quantity.required_parts
            if isinstance(self.quantity, (d._ObservedQuantity, d._RowStatisticQuantity))
            else None
        )
        if (
            quantity_domain != self.domain
            or self.quantity.kind != self.method.output_kind
            or (
                self.quantity.input_domain.kind
                if isinstance(self.quantity, d._RowStatisticQuantity)
                else self.domain.kind
            )
            not in self.method.input_domains
            or tuple(part.role for part in self.parts) != quantity_parts
            or any(
                part.keys != keys
                or tuple(part.schema.names[: len(keys)]) != keys
                or len(part.schema) <= len(keys)
                or any(part.schema.field(key) != self.schema.field(key) for key in keys)
                for part in self.parts
            )
        ):
            raise _exchange_invalid("quantity, method or retained role binding differs")
        if self.storage_receipt is None:
            if self.retained_parts:
                raise _exchange_invalid("retained parts without primary receipt")
        elif (
            len(self.retained_parts) != len(self.parts)
            or self.storage_receipt.schema_fingerprint
            != schema_fingerprint(_realized_schema(self.row, self.schema))
            or any(
                retained.role != part.role
                or retained.contract_id != part.contract_id
                or retained.contract_version != part.contract_version
                or retained.storage_receipt.schema_fingerprint != _arrow_fingerprint(part.schema)
                for retained, part in zip(self.retained_parts, self.parts, strict=True)
            )
        ):
            raise _exchange_invalid("receipt and retained role binding differs")

    @property
    def record(self) -> ExchangeRecord:
        return ExchangeRecord(
            d._row_contract_fingerprint(self.row),
            d._row_set_contract_fingerprint(self.rows),
            d._canonical_digest(d._descriptor_payload(self.domain)),
            d._canonical_digest(d._descriptor_payload(self.quantity)),
            self.method.method_id,
            self.method.version,
            hashlib.sha256(canonical_json(asdict(self.method)).encode("utf-8")).hexdigest(),
            self.method.cell_reasons,
            self.input_binding,
            tuple((field.name, str(field.type), field.nullable) for field in self.schema),
            _arrow_fingerprint(self.schema),
            tuple(
                (
                    part.role,
                    part.contract_id,
                    part.contract_version,
                    _arrow_fingerprint(part.schema),
                    self.retained_parts[index].storage_receipt.identity_digest
                    if self.retained_parts
                    else None,
                )
                for index, part in enumerate(self.parts)
            ),
            self.evidence.declarations,
            self.evidence.deductions,
            self.evidence.completed_checks,
            self.evidence.pending_checks,
            self.storage_receipt.identity_digest if self.storage_receipt is not None else None,
        )

    def require_record(self, record: ExchangeRecord) -> None:
        if record != self.record:
            raise _exchange_invalid("exchange binding differs")

    def require_method_type(self) -> None:
        value_type = self.schema.field("value").type
        policy = self.method.numeric_policy
        if not (
            policy == "none"
            or (policy == "int64_checked" and pa.types.is_int64(value_type))
            or (
                policy == "int64_or_float64"
                and (pa.types.is_int64(value_type) or pa.types.is_float64(value_type))
            )
            or (policy in ("float64_finite", "pair_ranks") and pa.types.is_float64(value_type))
        ):
            raise _exchange_invalid("value type is not admitted by the selected method")


def exchange_payload(value: ExchangeBinding) -> dict[str, object]:
    record = value.record
    return {
        "schema": "marivo.analysis_exchange/v1",
        "row_fingerprint": record.row_fingerprint,
        "row_set_fingerprint": record.row_set_fingerprint,
        "domain_fingerprint": record.domain_fingerprint,
        "quantity_fingerprint": record.quantity_fingerprint,
        "method_id": record.method_id,
        "method_version": record.method_version,
        "method_fingerprint": record.method_fingerprint,
        "cell_reasons": [[tag, list(reasons)] for tag, reasons in record.cell_reasons],
        "input_binding": record.input_binding,
        "fields": [[name, kind, nullable] for name, kind, nullable in record.fields],
        "schema_fingerprint": record.schema_fingerprint,
        "parts": [list(part) for part in record.parts],
        "declarations": record.declarations,
        "deductions": record.deductions,
        "completed_checks": record.completed_checks,
        "pending_checks": record.pending_checks,
        "receipt_identity": record.receipt_identity,
    }


def encode_exchange(value: ExchangeBinding) -> str:
    """Encode only the inactive v1 exchange binding, never an Artifact descriptor."""
    return canonical_json(exchange_payload(value))


def decode_exchange(text: str) -> ExchangeRecord:
    """Decode a strict v1 binding without granting publication or evidence authority."""
    obj = _obj(
        parse_json(text),
        "schema row_fingerprint row_set_fingerprint domain_fingerprint quantity_fingerprint method_id method_version method_fingerprint cell_reasons input_binding fields schema_fingerprint parts declarations deductions completed_checks pending_checks receipt_identity",
    )
    if obj["schema"] != "marivo.analysis_exchange/v1":
        raise _exchange_invalid("unsupported exchange schema version")
    hashes = tuple(
        _text(obj[name])
        for name in (
            "row_fingerprint",
            "row_set_fingerprint",
            "domain_fingerprint",
            "quantity_fingerprint",
            "schema_fingerprint",
        )
    )
    for value in hashes:
        _hash(value)
    receipt = obj["receipt_identity"]
    if receipt is not None:
        receipt = _text(receipt)
        _hash(receipt)
    method_fingerprint = _text(obj["method_fingerprint"])
    _hash(method_fingerprint)
    reasons: list[tuple[str, tuple[str, ...]]] = []
    for item in _array(obj["cell_reasons"]):
        pair = _array(item)
        if len(pair) != 2:
            raise _exchange_invalid("invalid Cell reason entry")
        reasons.append((_text(pair[0]), _texts(pair[1])))
    fields: list[tuple[str, str, bool]] = []
    for item in _array(obj["fields"]):
        field = _array(item)
        if len(field) != 3 or type(field[2]) is not bool:
            raise _exchange_invalid("invalid exchange field")
        fields.append((_text(field[0]), _text(field[1]), field[2]))
    parts: list[tuple[str, str, int, str, str | None]] = []
    for item in _array(obj["parts"]):
        part = _array(item)
        if len(part) != 5:
            raise _exchange_invalid("invalid exchange part")
        part_hash = _text(part[3])
        _hash(part_hash)
        part_receipt = part[4]
        if part_receipt is not None:
            part_receipt = _text(part_receipt)
            _hash(part_receipt)
        parts.append(
            (_text(part[0]), _text(part[1]), _int(part[2], minimum=1), part_hash, part_receipt)
        )
    record = ExchangeRecord(
        hashes[0],
        hashes[1],
        hashes[2],
        hashes[3],
        _text(obj["method_id"]),
        _int(obj["method_version"], minimum=1),
        method_fingerprint,
        tuple(reasons),
        _text(obj["input_binding"]),
        tuple(fields),
        hashes[4],
        tuple(parts),
        _texts(obj["declarations"]),
        _texts(obj["deductions"]),
        _texts(obj["completed_checks"]),
        _texts(obj["pending_checks"]),
        receipt,
    )
    if (
        len({tag for tag, _ in record.cell_reasons}) != len(record.cell_reasons)
        or any(tag not in ("null", "undefined", "unknown") for tag, _ in record.cell_reasons)
        or len({name for name, _, _ in record.fields}) != len(record.fields)
        or len({role for role, *_ in record.parts}) != len(record.parts)
        or set(record.completed_checks) & set(record.pending_checks)
    ):
        raise _exchange_invalid("duplicate or conflicting exchange facts")
    return record


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
    raise invalid("Entity sampling is no longer supported")


def decode_sampling(value: object) -> tuple[SamplingRealization, ...] | None:
    if value is None:
        return None
    raise invalid("Entity sampling is no longer supported")


def required_retained_contracts(
    row: d.DatasetRowContract,
    registered: tuple[str, ...],
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
    return result


def finding_extractor(row: d.DatasetRowContract, producer_id: str) -> str:
    if row.shape_id.family_id == "forecast":
        return "forecast_point_finding"
    if row.shape_id.family_id == "association":
        return "association_finding"
    return "none"


def finding_policy(row: d.DatasetRowContract, producer_id: str) -> str:
    if row.shape_id.family_id == "forecast":
        return "forecast_point_findings@v1"
    if row.shape_id.family_id == "association":
        return "association_findings@v1"
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
    association_evidence: AssociationEvidenceSummary | None = None
    forecast_evidence: ForecastEvidenceSummary | None = None
    candidate_evidence: CandidateEvidenceSummary | None = None

    temporal_execution: tuple[TemporalExecution, ...] = ()

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

    from marivo.analysis.operators.association_contracts import AssociationSemantics
    from marivo.analysis.operators.candidate_contracts import CandidateSemantics
    from marivo.analysis.operators.forecast_contracts import ForecastSemantics

    if isinstance(value, CandidateSemantics):
        from marivo.analysis.materialization.candidate_codec import (
            semantics_payload as candidate_payload,
        )

        return candidate_payload(value)
    if isinstance(value, ForecastSemantics):
        from marivo.analysis.materialization.forecast_codec import (
            semantics_payload as forecast_payload,
        )

        return forecast_payload(value)
    if isinstance(value, AssociationSemantics):
        from marivo.analysis.materialization.association_codec import semantics_payload

        return semantics_payload(value)

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

    if type(value) is not str or not value:
        raise invalid("invalid retained fold authority")
    try:
        decode_fold_authority(value)
    except (ValueError, TypeError, RecursionError) as exc:
        raise invalid("invalid retained fold authority") from exc
    return value


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
    if kind == "candidate/discovery@v1":
        from marivo.analysis.materialization.candidate_codec import (
            decode_semantics as decode_candidate,
        )

        return decode_candidate(value)
    if kind == "forecast/metric@v1":
        from marivo.analysis.materialization.forecast_codec import (
            decode_semantics as decode_forecast,
        )

        return decode_forecast(value)
    if kind == "association/metric@v1":
        from marivo.analysis.materialization.association_codec import decode_semantics

        return decode_semantics(value)
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
    from marivo.analysis.materialization.association_codec import (
        evidence_payload as association_evidence_payload,
    )
    from marivo.analysis.materialization.candidate_codec import (
        evidence_payload as candidate_evidence_payload,
    )
    from marivo.analysis.materialization.forecast_codec import (
        evidence_payload as forecast_evidence_payload,
    )
    from marivo.analysis.materialization.input_bindings_codec import (
        comparison_inputs_payload,
    )

    payload: dict[str, object] = {
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
        "temporal_execution": [item.model_dump(mode="json") for item in value.temporal_execution],
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
        "association_evidence": association_evidence_payload(value.association_evidence),
        "forecast_evidence": forecast_evidence_payload(value.forecast_evidence),
        "candidate_evidence": candidate_evidence_payload(value.candidate_evidence),
    }
    return payload


def encode_descriptor(value: ArtifactDescriptor) -> str:
    return canonical_json(descriptor_payload(value))


def decode_descriptor(text: str) -> ArtifactDescriptor:
    from marivo.analysis.materialization.association_codec import (
        decode_evidence as decode_association_evidence,
    )
    from marivo.analysis.materialization.candidate_codec import (
        decode_evidence as decode_candidate_evidence,
    )
    from marivo.analysis.materialization.forecast_codec import (
        decode_evidence as decode_forecast_evidence,
    )
    from marivo.analysis.materialization.input_bindings_codec import (
        comparison_basis_text,
        decode_comparison_inputs,
    )
    from marivo.analysis.materialization.quality import QualitySummary
    from marivo.analysis.observation.contracts import (
        make_family_registry,
        make_ids,
        producer_contract,
    )

    ids = make_ids(())
    raw = parse_json(text)
    if not isinstance(raw, dict):
        raise invalid("invalid Artifact descriptor")
    names = "schema definition_fingerprint row_contract row_contract_fingerprint row_set_contract row_set_contract_fingerprint realized_schema realized_schema_fingerprint bounded_lineage semantic_dependency_digest population_authority sampling_execution operator_implementation_versions dataset_materialization_contract storage_receipt retained_parts quality_summary typed_issues comparison_basis comparison_inputs association_evidence forecast_evidence candidate_evidence temporal_execution"
    obj = _obj(raw, names)
    if obj["schema"] not in ("marivo.dataset_artifact_descriptor/v1",):
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
        decode_association_evidence(obj["association_evidence"]),
        decode_forecast_evidence(obj["forecast_evidence"]),
        decode_candidate_evidence(obj["candidate_evidence"]),
        _decode_temporal(obj["temporal_execution"]),
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
        required_retained_contracts(row, registration.retained_contract_ids),
        finding_policy(row, registration.producer_id),
    )
    if materialization_payload(contract) != materialization_payload(expected_contract):
        raise invalid("unregistered materialization contract")
    if row.shape_id.family_id == "candidate":
        from marivo.analysis.materialization.candidate_publication import (
            validate_descriptor as validate_candidate_descriptor,
        )

        validate_candidate_descriptor(result)
    elif result.candidate_evidence is not None:
        raise invalid("Candidate Evidence outside its family")
    if row.shape_id.family_id == "forecast":
        from marivo.analysis.materialization.forecast_publication import (
            validate_descriptor as validate_forecast_descriptor,
        )

        validate_forecast_descriptor(result)
    elif result.forecast_evidence is not None:
        raise invalid("Forecast Evidence outside its family")
    if row.shape_id.family_id == "association":
        if (
            result.association_evidence is None
            or result.association_evidence.row_count != result.storage_receipt.realized_row_count
        ):
            raise invalid("missing or inconsistent Association Evidence")
        from marivo.analysis.operators.association_contracts import (
            AssociationSemantics,
            pair_approximation_bindings,
            pair_count,
        )

        semantics = row.family_semantics
        summary = result.association_evidence
        if (
            not isinstance(semantics, AssociationSemantics)
            or summary.searched_pair_count != pair_count(len(semantics.metric_keys))
            or summary.searched_lag_count != len(semantics.lag_offsets)
            or summary.pair_approximation_bindings != pair_approximation_bindings(semantics)
        ):
            raise invalid("Association Evidence search scope differs from its row authority")
    elif result.association_evidence is not None:
        raise invalid("Association Evidence outside its family")
    if result.comparison_inputs:
        raise invalid("retired private comparison authority")
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
    if result.sampling_execution is not None or any(
        item.contract_id == "population_sampling_state" for item in parts
    ):
        raise invalid("Entity sampling is no longer supported")
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
            "metric_components."
            + d._canonical_digest(
                column.identity.identity_id[7:]
                if isinstance(column.identity, d._CatalogFieldIdentity)
                else column.identity.identity_id
            )[:20]
            for column in row.schema.columns
            if column.role_id == "metric"
            and column.field_id in retained_fields
            and isinstance(
                column.identity, (d._CatalogFieldIdentity, d._RuntimeMetricFieldIdentity)
            )
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
    if any(
        item.contract_id != f"{row.shape_id.family_id}.distribution" or item.contract_version != 1
        for item in distribution_parts
    ):
        raise invalid("private distribution contract or version mismatch")
    membership_roles = {role for role, _ in membership_part_authorities(row)}
    membership_parts = tuple(
        item for item in parts if item.contract_id in DISTINCT_MEMBERSHIP_CONTRACT_IDS
    )
    if {item.role for item in membership_parts} != membership_roles:
        raise invalid("retained distinct membership roles mismatch")
    if any(
        item.contract_id != f"{row.shape_id.family_id}.distinct_membership"
        or item.contract_version != 1
        for item in membership_parts
    ):
        raise invalid("private membership contract or version mismatch")
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
    from marivo.analysis.materialization.association_codec import (
        evidence_payload as association_evidence_payload,
    )
    from marivo.analysis.materialization.candidate_codec import (
        evidence_payload as candidate_evidence_payload,
    )
    from marivo.analysis.materialization.forecast_codec import (
        evidence_payload as forecast_evidence_payload,
    )

    contract = descriptor.dataset_materialization_contract
    quality = digest(descriptor.quality_summary.model_dump(mode="json"))
    issues = digest([issue_payload(item) for item in descriptor.typed_issues])
    versions = (
        f"{contract.evidence_extractor_id}@v{contract.evidence_extractor_version}",
        f"{contract.finding_extractor_id}@v{contract.finding_extractor_version}",
    )
    summary = (
        descriptor.association_evidence
        or descriptor.forecast_evidence
        or descriptor.candidate_evidence
    )
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
    if descriptor.candidate_evidence is not None:
        value["candidate_evidence"] = candidate_evidence_payload(descriptor.candidate_evidence)
        value["candidate_semantics"] = _semantics_payload(descriptor.row_contract.family_semantics)
    if descriptor.forecast_evidence is not None:
        value["forecast_evidence"] = forecast_evidence_payload(descriptor.forecast_evidence)
        value["forecast_semantics"] = _semantics_payload(descriptor.row_contract.family_semantics)
    if descriptor.association_evidence is not None:
        value["association_evidence"] = association_evidence_payload(
            descriptor.association_evidence
        )
        value["association_semantics"] = _semantics_payload(
            descriptor.row_contract.family_semantics
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


def _decode_temporal(value: object) -> tuple[TemporalExecution, ...]:
    import json

    try:
        result = tuple(
            TemporalExecution.model_validate_json(json.dumps(item)) for item in _array(value)
        )
    except (ValueError, TypeError) as exc:
        raise invalid("invalid temporal execution authority") from exc
    if len({item.model_dump_json() for item in result}) != len(result):
        raise invalid("duplicate temporal execution authority")
    return result
