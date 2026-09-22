"""Bounded immutable Parquet writes and source-free PyArrow primary reads."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import BinaryIO, Generic, Literal, TypeAlias, TypeVar

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from marivo._compat import Never
from marivo.analysis.datasets.descriptors import (
    _CORE_TOKEN,
    DatasetField,
    DatasetRowContract,
    DatasetRowSetContract,
    DatasetSchema,
    _bool_tuple_arity,
    _bool_tuple_value,
    _EntityFieldIdentity,
    _make_schema,
    _OrderedOrdering,
    _ResolvedPhysicalType,
    _StaticRowBound,
)
from marivo.analysis.domains.lifecycle_reducers import is_fragment_duration
from marivo.analysis.materialization.contracts import (
    FileEntry,
    LocalReceipt,
    RetainedPart,
    StorageReceipt,
    manifest_digest,
    schema_fingerprint,
)
from marivo.analysis.materialization.errors import (
    IntegrityError,
    MaterializationError,
    StorageAccessError,
)

_Value: TypeAlias = (
    None | bool | int | float | str | date | datetime | Decimal | tuple["_Value", ...]
)
_ROLE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@+-]{0,159}\Z")


@dataclass(frozen=True, slots=True)
class StoragePolicy:
    """Parquet row-group tuning; no storage or allocation limits."""

    row_group_rows: int = 1024


@dataclass(frozen=True, slots=True)
class ReadPolicy:
    """Preview display length only; complete reads have no resource caps."""

    preview_rows: int = 20


_STORAGE_POLICY = StoragePolicy()
_READ_POLICY = ReadPolicy()


@dataclass(frozen=True, slots=True)
class PartWriteSpec:
    role: str
    contract_id: str
    contract_version: int
    column_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IndependentPartWrite:
    """An independently counted owner-declared part in the primary transaction."""

    role: str
    batches: Iterable[pa.RecordBatch]


_ReceiptT = TypeVar("_ReceiptT", bound=StorageReceipt, covariant=True)


@dataclass(frozen=True, slots=True)
class DatasetWriteResult(Generic[_ReceiptT]):
    primary_receipt: _ReceiptT
    retained_parts: tuple[RetainedPart, ...]
    realized_schema: DatasetSchema
    realized_row_count: int


def _fail(expected: str, received: str, *, stage: str = "output_validation") -> Never:
    raise MaterializationError(
        expected=expected,
        received=received,
        repair="Use the exact registered schema, ordered row keys and immutable local storage route.",
        stage=stage,
    )


def _integrity(expected: str, received: str) -> Never:
    raise IntegrityError(
        expected=expected,
        received=received,
        repair="Restore the exact committed backing or explicitly author a new execution.",
        stage="storage_read",
    )


def _checked_path(project_root: Path, path: Path) -> Path:
    root = project_root.absolute()
    candidate = path if path.is_absolute() else root / path
    if ".." in candidate.parts or root.resolve() != root:
        _integrity("canonical project-local paths", "non-canonical path")
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        _integrity("a path within this project", "foreign path")
    if not relative.parts:
        _integrity("a dedicated immutable payload path", "project root")
    current = root
    for component in relative.parts:
        current /= component
        if current.is_symlink():
            _integrity("symlink-free immutable storage", "symlink in selected path")
    return candidate


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _create_directory(path: Path) -> None:
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(exist_ok=True)
        _fsync_directory(directory.parent)


def _normal_type(value: pa.DataType) -> pa.DataType:
    if pa.types.is_timestamp(value) and value.unit == "s":
        return pa.timestamp("ms", tz=value.tz)
    if pa.types.is_dictionary(value):
        return _normal_type(value.value_type)
    if pa.types.is_struct(value):
        return pa.struct(
            [pa.field(item.name, _normal_type(item.type), item.nullable) for item in value]
        )
    return value


def _normalize_batch(batch: pa.RecordBatch) -> pa.RecordBatch:
    fields = [pa.field(item.name, _normal_type(item.type), item.nullable) for item in batch.schema]
    target = pa.schema(fields)
    if not batch.schema.equals(target, check_metadata=False):
        batch = batch.cast(target)
    return batch


def _matches_type(logical: str, actual: pa.DataType) -> bool:
    decimal = re.fullmatch(r"decimal\((\d+),\s*(\d+)\)", logical)
    if decimal is not None:
        return bool(
            pa.types.is_decimal(actual)
            and actual.precision == int(decimal.group(1))
            and actual.scale == int(decimal.group(2))
        )
    timestamp = re.fullmatch(r"timestamp\(([0-6])\)", logical)
    if timestamp is not None:
        scale = int(timestamp[1])
        return bool(actual == pa.timestamp("ms" if scale <= 3 else "us"))
    if logical == "duration":
        return bool(pa.types.is_int64(actual))
    if logical == "candidate_reasons":
        return bool(pa.types.is_list(actual) and pa.types.is_string(actual.value_type))
    if logical == "identity_tuple":
        return bool(pa.types.is_struct(actual))
    arity = _bool_tuple_arity(logical)
    if arity is not None:
        return bool(
            (pa.types.is_list(actual) or pa.types.is_fixed_size_list(actual))
            and pa.types.is_boolean(actual.value_type)
            and (not pa.types.is_fixed_size_list(actual) or actual.list_size == arity)
        )
    checks: dict[str, Callable[[pa.DataType], bool]] = {
        "bool": pa.types.is_boolean,
        "boolean": pa.types.is_boolean,
        "int8": pa.types.is_int8,
        "int16": pa.types.is_int16,
        "int32": pa.types.is_int32,
        "int64": pa.types.is_int64,
        "uint8": pa.types.is_uint8,
        "uint16": pa.types.is_uint16,
        "uint32": pa.types.is_uint32,
        "uint64": pa.types.is_uint64,
        "integer": pa.types.is_integer,
        "float32": pa.types.is_float32,
        "float64": pa.types.is_float64,
        "floating": pa.types.is_floating,
        "numeric": lambda value: bool(pa.types.is_integer(value) or pa.types.is_floating(value)),
        "string": pa.types.is_string,
        "date": pa.types.is_date,
        "timestamp": pa.types.is_timestamp,
        "datetime": pa.types.is_timestamp,
        "decimal": pa.types.is_decimal,
    }
    check = checks.get(logical)
    return check is not None and bool(check(actual))


def _realized_schema(row: DatasetRowContract, actual: pa.Schema) -> DatasetSchema:
    logical = row.schema
    if actual.names != [column.name for column in logical.columns]:
        _fail("the exact ordered primary column names", "primary columns differ")
    columns: list[DatasetField] = []
    for expected, field in zip(logical.columns, actual, strict=True):
        fragment = is_fragment_duration(row, expected)
        if not (
            pa.types.is_float64(field.type)
            if fragment
            else _matches_type(expected.logical_type_id, field.type)
        ):
            _fail("an Arrow type admitted by each logical field", "logical type mismatch")
        identity = expected.identity
        if isinstance(identity, _EntityFieldIdentity):
            signature = identity.identity_signature
            if len(field.type) != len(signature) or any(
                child.name != name or not _matches_type(kind, child.type)
                for child, (name, kind) in zip(field.type, signature, strict=True)
            ):
                _fail("the exact ordered typed identity struct", "identity schema mismatch")
        physical = expected.physical_type_state
        if isinstance(physical, _ResolvedPhysicalType) and not (
            physical.physical_type_id == "duration" and pa.types.is_float64(field.type)
            if fragment
            else _matches_type(physical.physical_type_id, field.type)
        ):
            _fail("the exact already resolved physical field type", "physical type mismatch")
        physical_id = (
            physical.physical_type_id
            if isinstance(physical, _ResolvedPhysicalType)
            else expected.logical_type_id
        )
        columns.append(
            replace(
                expected,
                _token=_CORE_TOKEN,
                physical_type_state=_ResolvedPhysicalType(
                    _token=_CORE_TOKEN, physical_type_id=physical_id
                ),
            )
        )
    return _make_schema(tuple(columns))


def _reason_tuple(value: object) -> tuple[str, ...] | None:
    """Normalize the bounded structural tuple; the objective owns its vocabulary."""
    if (
        isinstance(value, (tuple, list))
        and len(value) == 1
        and type(value[0]) is str
        and 0 < len(value[0]) <= 64
    ):
        return (value[0],)
    return None


def _value(scalar: pa.Scalar) -> _Value:
    if not scalar.is_valid:
        return None
    if pa.types.is_struct(scalar.type):
        return tuple(_value(scalar[index]) for index in range(len(scalar.type)))
    if pa.types.is_list(scalar.type) or pa.types.is_fixed_size_list(scalar.type):
        values: object = scalar.as_py()
        if pa.types.is_string(scalar.type.value_type):
            reasons = _reason_tuple(values)
            if reasons is None:
                _fail("one bounded non-null reason code", "invalid reason tuple")
            return reasons
        mask = _bool_tuple_value(values) if isinstance(values, list) else None
        if mask is not None:
            return mask
        _fail("non-null boolean mask members", "invalid partition mask")
    value: object = scalar.as_py()
    if isinstance(value, (bool, int, float, str, date, datetime, Decimal)):
        if isinstance(value, float) and not math.isfinite(value):
            _fail("finite totally ordered key values", "non-finite row key")
        if isinstance(value, Decimal) and not value.is_finite():
            _fail("finite totally ordered key values", "non-finite decimal row key")
        return value
    _fail("registered scalar or identity key values", "unsupported key value")


def _compare(left: _Value, right: _Value, *, nulls: str = "last") -> int:
    if left is None or right is None:
        if left is right:
            return 0
        return (-1 if nulls == "first" else 1) if left is None else (1 if nulls == "first" else -1)
    if isinstance(left, tuple) and isinstance(right, tuple):
        for first, second in zip(left, right, strict=True):
            result = _compare(first, second, nulls=nulls)
            if result:
                return result
        return 0
    if type(left) is not type(right):
        _fail("one exact physical type for an ordered key", "mixed key types")
    if isinstance(left, (bool, int, float, Decimal)) and isinstance(
        right, (bool, int, float, Decimal)
    ):
        return (left > right) - (left < right)
    if isinstance(left, str) and isinstance(right, str):
        return (left > right) - (left < right)
    if isinstance(left, datetime) and isinstance(right, datetime):
        return (left > right) - (left < right)
    if isinstance(left, date) and isinstance(right, date):
        return (left > right) - (left < right)
    _fail("comparable exact key values", "unsupported ordering")


class _RowValidator:
    def __init__(
        self,
        contract: DatasetRowContract,
        rows: DatasetRowSetContract,
        *,
        source_key_validation: bool = False,
    ) -> None:
        by_id = {field.field_id: field.name for field in contract.schema.columns}
        self.keys = tuple(by_id[key] for key in contract.key_field_ids)
        self.terms = tuple((name, "ascending", "last") for name in self.keys)
        if isinstance(rows.ordering, _OrderedOrdering):
            ordered_ids = tuple(term.field_id for term in rows.ordering.terms)
            if not set(contract.key_field_ids).issubset(ordered_ids):
                _fail(
                    "a total stream order containing the complete row key",
                    "missing unique ordering tie-breaker",
                    stage="storage_selection",
                )
            if ordered_ids != contract.key_field_ids and not source_key_validation:
                _fail(
                    "separate final source row-key uniqueness validation",
                    "ordering alone does not prove row-key uniqueness",
                    stage="storage_selection",
                )
            self.terms = tuple(
                (by_id[term.field_id], term.direction, term.nulls) for term in rows.ordering.terms
            )
        from marivo.analysis.operators.association_contracts import association_orders

        self.authored_orders = association_orders(contract, rows)
        from marivo.analysis.domains.lifecycle_reducers import (
            REDUCER_TYPES,
            TransitionsSemantics,
            history_semantics,
        )

        self.lifecycle_pairs: tuple[tuple[str, str], ...] | None = None
        self.previous_lifecycle_pair: int | None = None
        if isinstance(contract.family_semantics, REDUCER_TYPES):
            lifecycle = history_semantics(contract.family_semantics)
            if isinstance(contract.family_semantics, TransitionsSemantics):
                self.lifecycle_pairs = lifecycle.transition_pairs
            elif "model_state" in by_id.values():
                self.authored_orders["model_state"] = lifecycle.states
        from marivo.analysis.domains.event_comparison import FunnelDeltaSemantics

        if isinstance(contract.family_semantics, FunnelDeltaSemantics):
            self.authored_orders["step_key"] = tuple(
                step.key for step in contract.family_semantics.current.journey.pattern.steps
            )
        self.contract = contract
        self.rows = rows
        self.attribution_masks: tuple[tuple[bool, ...], ...] | None = None
        self.attribution_axes: tuple[str, ...] = ()
        if contract.shape_id.family_id == "attribution":
            from marivo.analysis.operators.attribution_contracts import AttributionSemantics

            semantics = contract.family_semantics
            from marivo.analysis.domains.event_attribution import FunnelAttributionSemantics

            if not isinstance(semantics, (AttributionSemantics, FunnelAttributionSemantics)):
                _fail("exact Attribution row semantics", "missing Attribution authority")
            self.attribution_masks = tuple(
                tuple(index < len(prefix) for index in range(len(semantics.axis_field_ids)))
                for prefix in semantics.resolution_prefixes
            )
            self.attribution_axes = tuple(by_id[field_id] for field_id in semantics.axis_field_ids)
        self.previous: tuple[_Value, ...] | None = None
        self.count = 0
        from marivo.analysis.domains.contracts import (
            EventFunnelSemantics,
            EventJourneySemantics,
            EventTimeToEventSemantics,
        )
        from marivo.analysis.materialization.event_publication import EventRowValidator
        from marivo.analysis.materialization.event_reducer_publication import (
            EventReducerRowValidator,
        )

        self.event_validator: EventRowValidator | EventReducerRowValidator | None = (
            EventRowValidator(contract.family_semantics)
            if isinstance(contract.family_semantics, EventJourneySemantics)
            else EventReducerRowValidator(contract.family_semantics)
            if isinstance(
                contract.family_semantics, (EventFunnelSemantics, EventTimeToEventSemantics)
            )
            else None
        )

    def accept(self, batch: pa.RecordBatch) -> None:
        for field in self.contract.schema.columns:
            column = batch.column(batch.schema.get_field_index(field.name))
            if not field.nullable and column.null_count:
                _fail("non-null values for required fields", "unexpected nulls")
            if isinstance(field.identity, _EntityFieldIdentity) and (
                column.null_count
                or any(column.field(index).null_count for index in range(column.type.num_fields))
            ):
                _fail("complete non-null identity tuples", "null identity component")
            if field.logical_type_id == "candidate_reasons" and (
                not _matches_type(field.logical_type_id, column.type)
                or any(_reason_tuple(value) is None for value in column.to_pylist())
            ):
                _fail("one bounded non-null reason code", "invalid reason tuple")
            arity = _bool_tuple_arity(field.logical_type_id)
            if arity is not None and (
                not _matches_type(field.logical_type_id, column.type)
                or any(
                    not isinstance(value, list) or _bool_tuple_value(value, arity=arity) is None
                    for value in column.to_pylist()
                )
            ):
                _fail("the exact fixed-length boolean partition mask", "invalid mask values")
        if self.event_validator is not None:
            self.event_validator.accept(batch)
            self.count += batch.num_rows
            return
        for offset in range(batch.num_rows):
            if self.attribution_masks is not None:
                active = _value(batch.column("active_axis_mask")[offset])
                other = _value(batch.column("other_mask")[offset])
                if (
                    not isinstance(active, tuple)
                    or not isinstance(other, tuple)
                    or active not in self.attribution_masks
                    or len(other) != len(active)
                    or any(
                        mapped and not selected
                        for mapped, selected in zip(other, active, strict=True)
                    )
                ):
                    _fail(
                        "an exact registered Attribution resolution and Other mask",
                        "invalid partition mask",
                    )
                if any(
                    (not selected or mapped) and batch.column(name)[offset].is_valid
                    for name, selected, mapped in zip(
                        self.attribution_axes, active, other, strict=True
                    )
                ):
                    _fail(
                        "null typed inactive or Other axis values", "invalid Attribution axis value"
                    )
            ordered = tuple(
                _value(batch.column(batch.schema.get_field_index(name))[offset])
                for name, _, _ in self.terms
            )
            if self.lifecycle_pairs is not None:
                pair = (
                    batch["from_model_state"][offset].as_py(),
                    batch["to_model_state"][offset].as_py(),
                )
                if pair not in self.lifecycle_pairs:
                    _fail("declared Lifecycle transition pair", "unknown pair")
                ordinal = self.lifecycle_pairs.index(pair)
                if (
                    self.previous_lifecycle_pair is not None
                    and ordinal <= self.previous_lifecycle_pair
                ):
                    _fail(
                        "strictly increasing declared transition pairs",
                        "duplicate or unordered pair",
                    )
                self.previous_lifecycle_pair = ordinal
            elif self.previous is not None:
                comparison = 0
                for left, right, (name, direction, nulls) in zip(
                    self.previous,
                    ordered,
                    self.terms,
                    strict=True,
                ):
                    if name in self.authored_orders:
                        values = self.authored_orders[name]
                        if left not in values or right not in values:
                            _fail("authored Association coordinate", "unknown ordering value")
                        comparison = (values.index(left) > values.index(right)) - (
                            values.index(left) < values.index(right)
                        )
                    else:
                        comparison = _compare(left, right, nulls=nulls)
                    if direction == "descending" and left is not None and right is not None:
                        comparison = -comparison
                    if comparison:
                        break
                if comparison >= 0:
                    _fail(
                        "strictly increasing governed total stream order",
                        "duplicate or unordered ordering tuple",
                    )
            self.previous = ordered
            self.count += 1
        if self.rows.cardinality.kind == "singleton" and self.count > 1:
            _fail("one singleton row", "multiple singleton rows")
        bound = getattr(self.rows.cardinality, "row_bound", None)
        if isinstance(bound, _StaticRowBound) and self.count > bound.max_rows:
            _fail("rows within the declared bound", "static row bound exceeded")

    def finish(self) -> None:
        if self.event_validator is not None:
            self.event_validator.finish()
        if self.rows.cardinality.kind == "singleton" and self.count != 1:
            _fail("exactly one singleton row", "empty singleton")


def _manifest_bytes(entries: tuple[FileEntry, ...]) -> bytes:
    return json.dumps(
        [
            {
                "relative_path": entry.relative_path,
                "size_bytes": entry.size_bytes,
                "sha256": entry.sha256,
            }
            for entry in entries
        ],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def write_local_dataset(
    *,
    project_root: Path,
    staging_path: Path,
    final_path: Path,
    batches: Iterable[pa.RecordBatch],
    row_contract: DatasetRowContract,
    row_set_contract: DatasetRowSetContract,
    parts: tuple[PartWriteSpec, ...] = (),
    independent_parts: tuple[IndependentPartWrite, ...] = (),
    source_key_validation: bool = False,
    event: Callable[[str], None],
    policy: StoragePolicy = _STORAGE_POLICY,
) -> DatasetWriteResult[LocalReceipt]:
    """Write one pre-reserved ordered stream; metadata publication belongs to Runtime."""
    from marivo.analysis.materialization.retained import (
        reject_source_private_transfer,
        source_private_role,
    )
    from marivo.analysis.observation.distinct_contracts import DISTINCT_MEMBERSHIP_CONTRACT_IDS
    from marivo.analysis.observation.distribution_contracts import DISTRIBUTION_CONTRACT_IDS

    if any(
        source_private_role(part.role)
        or part.contract_id in (*DISTINCT_MEMBERSHIP_CONTRACT_IDS, *DISTRIBUTION_CONTRACT_IDS)
        for part in parts
    ):
        reject_source_private_transfer()
    from marivo.analysis.materialization.private_parquet import independent_contracts

    independent_specs = independent_contracts(row_contract)
    if tuple(part.role for part in independent_parts) != tuple(independent_specs):
        _fail("all exact owner-declared independent roles", "invalid independent retained parts")
    if set(independent_specs) & {part.role for part in parts}:
        _fail("one writer for each retained role", "duplicate independent role")
    staging = _checked_path(project_root, staging_path)
    final = _checked_path(project_root, final_path)
    if staging.exists() or final.exists() or staging == final:
        _fail(
            "unused exact staging and final locations",
            "existing or identical output path",
            stage="storage_selection",
        )
    if len({part.role for part in parts}) != len(parts) or any(
        not _ROLE.fullmatch(part.role) for part in parts
    ):
        _fail("unique bounded retained role names", "invalid retained role")
    validator = _RowValidator(
        row_contract, row_set_contract, source_key_validation=source_key_validation
    )
    for part in parts:
        if (
            not part.column_names
            or len(set(part.column_names)) != len(part.column_names)
            or any(name not in part.column_names for name in validator.keys)
        ):
            _fail(
                "retained columns containing the complete shared row key",
                "invalid retained column projection",
            )
    _create_directory(staging)
    writers: list[pq.ParquetWriter] = []
    sinks: list[BinaryIO] = []
    schemas: list[pa.Schema] = []
    realized: DatasetSchema | None = None
    full_schema: pa.Schema | None = None
    projections = (
        tuple(field.name for field in row_contract.schema.columns),
        *(part.column_names for part in parts),
    )
    directories = ("primary", *(f"parts/{part.role}" for part in parts))
    try:
        for incoming in batches:
            batch = _normalize_batch(incoming)
            if len(set(batch.schema.names)) != len(batch.schema.names):
                _fail("unique stream column names", "duplicate stream columns")
            if full_schema is None:
                expected_names = {name for projection in projections for name in projection}
                if set(batch.schema.names) != expected_names:
                    _fail(
                        "the exact primary and retained stream columns",
                        "missing or unexpected stream columns",
                    )
                full_schema = batch.schema
                for projection, directory in zip(projections, directories, strict=True):
                    target = staging / directory
                    _create_directory(target)
                    selected = batch.select(projection)
                    schema = selected.schema
                    if not schemas:
                        realized = _realized_schema(row_contract, schema)
                        schema = pa.schema(
                            [
                                pa.field(field.name, schema.field(field.name).type, field.nullable)
                                for field in row_contract.schema.columns
                            ]
                        )
                    schemas.append(schema)
                    sink: BinaryIO = (target / "data.parquet").open("wb")
                    sinks.append(sink)
                    event("parquet_payload_create")
                    writers.append(
                        pq.ParquetWriter(
                            sink,
                            schema,
                            compression="zstd",
                            use_dictionary=False,
                            write_page_checksum=True,
                        )
                    )
            elif not batch.schema.equals(full_schema, check_metadata=False):
                _fail("one exact Arrow stream schema", "schema changed between batches")
            primary = batch.select(projections[0])
            validator.accept(primary)
            for writer, projection, schema in zip(writers, projections, schemas, strict=True):
                selected = batch.select(projection).cast(schema)
                writer.write_batch(selected, row_group_size=policy.row_group_rows)
        validator.finish()
        if realized is None:
            _fail(
                "a stream carrying its schema, including an empty batch",
                "stream contained no batch",
            )
        for writer in writers:
            writer.close()
        writers.clear()
        for sink in sinks:
            sink.close()
        sinks.clear()
        row_counts = (validator.count,) * len(directories)
        retained_specs = tuple(
            (part.role, part.contract_id, part.contract_version) for part in parts
        )
        for independent in independent_parts:
            from marivo.analysis.materialization.retained import (
                checked_component_batches,
                component_schema,
            )

            event("retained_part_write")
            event(f"retained_part_write.{independent.role}")
            if row_contract.shape_id.family_id == "lifecycle":
                event("lifecycle_part_write")
                event(f"lifecycle_part_write.{independent.role}")
            directory = f"parts/{independent.role}"
            target = staging / directory
            _create_directory(target)
            part_count = 0
            part_schema_value: pa.Schema | None = None
            part_writer: pq.ParquetWriter | None = None
            part_sink = (target / "data.parquet").open("wb")
            sinks.append(part_sink)
            event("parquet_payload_create")
            for incoming in checked_component_batches(
                independent.batches, row_contract, independent.role
            ):
                batch = _normalize_batch(incoming)
                if part_schema_value is None:
                    part_schema_value = batch.schema
                    component_schema(row_contract, independent.role, part_schema_value)
                    part_writer = pq.ParquetWriter(
                        part_sink,
                        part_schema_value,
                        compression="zstd",
                        use_dictionary=False,
                        write_page_checksum=True,
                    )
                    writers.append(part_writer)
                if not batch.schema.equals(part_schema_value, check_metadata=False):
                    _fail("one canonical part schema", "changing independent part schema")
                assert part_writer is not None
                part_writer.write_batch(batch, row_group_size=policy.row_group_rows)
                part_count += batch.num_rows
            if part_writer is not None:
                part_writer.close()
                writers.clear()
            part_sink.close()
            sinks.clear()
            if part_schema_value is None:
                _fail(
                    "an empty or populated schema-carrying part", "missing independent part stream"
                )
            schemas.append(part_schema_value)
            directories += (directory,)
            row_counts += (part_count,)
            retained_specs += ((independent.role, independent_specs[independent.role], 1),)
        receipts: list[LocalReceipt] = []
        for index, (directory, schema, row_count) in enumerate(
            zip(directories, schemas, row_counts, strict=True)
        ):
            target = staging / directory
            file = target / "data.parquet"
            with pq.ParquetFile(file) as parquet:
                persisted_schema = parquet.schema_arrow
                if parquet.metadata.num_rows != row_count or not persisted_schema.equals(
                    schema, check_metadata=False
                ):
                    _fail(
                        "an exact schema and row-count Parquet round trip",
                        "written Parquet differs",
                    )
            entry = FileEntry("data.parquet", file.stat().st_size, _hash_file(file))
            entries = (entry,)
            manifest = _manifest_bytes(entries)
            with (target / "manifest.json").open("wb") as stream:
                stream.write(manifest)
            _fsync_directory(target)
            receipts.append(
                LocalReceipt(
                    project_relative_path=(final / directory)
                    .relative_to(project_root.absolute())
                    .as_posix(),
                    file_manifest=entries,
                    manifest_hash=manifest_digest(entries),
                    bytes_hash=entry.sha256,
                    schema_fingerprint=schema_fingerprint(realized)
                    if index == 0
                    else hashlib.sha256(persisted_schema.serialize().to_pybytes()).hexdigest(),
                    realized_row_count=row_count,
                    realized_byte_count=entry.size_bytes + len(manifest),
                )
            )
        if retained_specs:
            _fsync_directory(staging / "parts")
        _fsync_directory(staging)
        _create_directory(final.parent)
        _checked_path(project_root, final)
        event("before_rename")
        os.rename(staging, final)
        _fsync_directory(final.parent)
        _fsync_directory(staging.parent)
        event("after_rename")
        return DatasetWriteResult(
            receipts[0],
            tuple(
                RetainedPart(role, contract_id, version, receipt)
                for (role, contract_id, version), receipt in zip(
                    retained_specs, receipts[1:], strict=True
                )
            ),
            realized,
            validator.count,
        )
    finally:
        for writer in writers:
            with suppress(BaseException):
                writer.close()
        for sink in sinks:
            with suppress(BaseException):
                sink.close()


def _open_payload(
    project_root: Path,
    receipt: LocalReceipt,
) -> tuple[pq.ParquetFile, Path]:
    path = Path(receipt.project_relative_path)
    if path.is_absolute():
        _integrity("a project-relative receipt", "absolute receipt locator")
    root = _checked_path(project_root, path)
    if receipt.parquet_contract_version != 1 or len(receipt.file_manifest) != 1:
        _integrity("the supported Parquet v1 single-file receipt", "unsupported receipt")
    if manifest_digest(receipt.file_manifest) != receipt.manifest_hash:
        _integrity("the exact manifest digest", "manifest mismatch")
    entry = receipt.file_manifest[0]
    if entry.relative_path != "data.parquet":
        _integrity("the exact registered data filename", "invalid manifest entry")
    manifest = _checked_path(project_root, root / "manifest.json")
    data = _checked_path(project_root, root / entry.relative_path)
    parquet: pq.ParquetFile | None = None
    failure: Literal["missing", "unauthorized", "mutated", "unknown"] = "unknown"
    try:
        expected = _manifest_bytes(receipt.file_manifest)
        if manifest.stat().st_size != len(expected) or data.stat().st_size != entry.size_bytes:
            _integrity("exact committed file sizes", "backing size changed")
        if (
            manifest.read_bytes() != expected
            or receipt.realized_byte_count != entry.size_bytes + len(expected)
        ):
            _integrity("the exact committed manifest", "manifest bytes changed")
        parquet = pq.ParquetFile(data, page_checksum_verification=True)
        if parquet.metadata.num_rows != receipt.realized_row_count:
            _integrity("the exact committed row count", "Parquet row count differs")
        return parquet, data
    except IntegrityError:
        if parquet is not None:
            parquet.close()
        raise
    except MaterializationError:
        if parquet is not None:
            parquet.close()
        _integrity("the exact retained logical and physical schema", "invalid primary schema")
    except (OSError, pa.ArrowException) as error:
        if parquet is not None:
            parquet.close()
        if isinstance(error, FileNotFoundError):
            failure = "missing"
        elif isinstance(error, PermissionError):
            failure = "unauthorized"
        elif isinstance(error, pa.ArrowException):
            failure = "mutated"
    raise StorageAccessError(failure)


def _open_primary(
    project_root: Path,
    receipt: LocalReceipt,
    row: DatasetRowContract,
) -> tuple[pq.ParquetFile, Path]:
    parquet, data = _open_payload(project_root, receipt)
    try:
        realized = _realized_schema(row, parquet.schema_arrow)
        if schema_fingerprint(realized) != receipt.schema_fingerprint:
            _integrity("the exact realized schema fingerprint", "retained schema mismatch")
        for field, actual in zip(row.schema.columns, parquet.schema_arrow, strict=True):
            if field.nullable != actual.nullable:
                _integrity("the committed field nullability", "retained nullability differs")
        return parquet, data
    except BaseException:
        parquet.close()
        raise


def read_part_batches(
    project_root: Path,
    part: RetainedPart,
    *,
    expected_schema: pa.Schema,
    policy: ReadPolicy = _READ_POLICY,
) -> Iterator[pa.RecordBatch]:
    """Read exactly one registered part; closing early never constitutes validation."""
    from marivo.analysis.materialization.retained import guard_part_transfer

    guard_part_transfer(part)
    return _read_part_batches(project_root, part, expected_schema=expected_schema, policy=policy)


def _read_part_batches(
    project_root: Path,
    part: RetainedPart,
    *,
    expected_schema: pa.Schema,
    policy: ReadPolicy,
) -> Iterator[pa.RecordBatch]:
    receipt = part.storage_receipt
    if not isinstance(receipt, LocalReceipt):
        _integrity("the local part reader", "non-local part receipt")
    expected_hash = hashlib.sha256(expected_schema.serialize().to_pybytes()).hexdigest()
    if receipt.schema_fingerprint != expected_hash:
        _integrity("the exact registered part schema", "required part schema fingerprint differs")
    parquet, data = _open_payload(project_root, receipt)
    time.monotonic()
    count = decoded = 0
    try:
        if not parquet.schema_arrow.equals(expected_schema, check_metadata=False):
            _integrity("the exact registered part schema", "required part schema differs")
        for batch in _parquet_batches(parquet, policy, preview=False):
            count += batch.num_rows
            decoded += batch.nbytes
            for field in expected_schema:
                if not field.nullable and batch.column(field.name).null_count:
                    _integrity("required non-null part fields", "null required part field")
            yield batch
        if (
            count != receipt.realized_row_count
            or _hash_file(data) != receipt.bytes_hash
            or receipt.file_manifest[0].sha256 != receipt.bytes_hash
        ):
            _integrity("complete immutable required part backing", "required part content changed")
    except (OSError, pa.ArrowException):
        _integrity("accessible valid required part backing", "required part is corrupt")
    finally:
        parquet.close()


def _to_dataframe(table: pa.Table, row: DatasetRowContract) -> pd.DataFrame:
    result: pd.DataFrame = table.to_pandas(types_mapper=pd.ArrowDtype)
    for field in row.schema.columns:
        if (
            field.logical_type_id == "identity_tuple"
            or _bool_tuple_arity(field.logical_type_id) is not None
            or field.logical_type_id == "candidate_reasons"
        ):
            array = table.column(field.name)
            result[field.name] = pd.Series(
                [_value(array[index]) for index in range(table.num_rows)], dtype=object
            )
        if is_fragment_duration(row, field):
            result[field.name] = pd.to_timedelta(table.column(field.name).to_pandas(), unit="us")
        elif field.logical_type_id == "duration":
            result[field.name] = pd.Series(
                table.column(field.name).cast(pa.duration("us")),
                dtype=pd.ArrowDtype(pa.duration("us")),
            )
    return result.copy(deep=True)


def _parquet_batches(
    parquet: pq.ParquetFile, policy: ReadPolicy, *, preview: bool, use_threads: bool = True
) -> Iterator[pa.RecordBatch]:
    remaining = policy.preview_rows
    for index in range(parquet.metadata.num_row_groups):
        parquet.metadata.row_group(index)
        size = min(1024, remaining) if preview else 1024
        for batch in parquet.iter_batches(
            batch_size=max(1, size), row_groups=[index], use_threads=use_threads
        ):
            yield batch
            remaining -= batch.num_rows
            if preview and remaining <= 0:
                return


def _read(
    *,
    project_root: Path,
    receipt: LocalReceipt,
    row_contract: DatasetRowContract,
    row_set_contract: DatasetRowSetContract,
    preview: bool,
    policy: ReadPolicy,
) -> pa.Table:
    time.monotonic()
    if row_set_contract.cardinality.kind == "singleton" and receipt.realized_row_count != 1:
        _integrity("exactly one committed singleton row", "invalid singleton count")
    bound = getattr(row_set_contract.cardinality, "row_bound", None)
    if isinstance(bound, _StaticRowBound) and receipt.realized_row_count > bound.max_rows:
        _integrity("the declared static row bound", "committed row bound exceeded")
    parquet, data = _open_primary(project_root, receipt, row_contract)
    # The committed descriptor requires the producer's independent key validation.
    validator = _RowValidator(row_contract, row_set_contract, source_key_validation=True)
    retained: list[pa.RecordBatch] = []
    decoded = 0
    remaining = policy.preview_rows if preview else receipt.realized_row_count
    try:
        for batch in _parquet_batches(parquet, policy, preview=preview):
            selected = batch.slice(0, remaining) if preview else batch
            decoded += selected.nbytes
            validator.accept(selected)
            retained.append(selected)
            remaining -= selected.num_rows
            if preview and remaining <= 0:
                break
        if not preview:
            validator.finish()
            if validator.count != receipt.realized_row_count:
                _integrity("all committed primary rows", "incomplete primary read")
            if (
                _hash_file(data) != receipt.bytes_hash
                or receipt.file_manifest[0].sha256 != receipt.bytes_hash
            ):
                _integrity("the exact immutable primary content hash", "primary content changed")
        return pa.Table.from_batches(retained, schema=parquet.schema_arrow)
    except IntegrityError:
        raise
    except MaterializationError:
        _integrity("valid ordered retained rows and nullability", "selected row contract violation")
    except (OSError, pa.ArrowException):
        _integrity("valid selected Parquet pages", "selected backing is corrupt")
    finally:
        parquet.close()


def read_preview(
    *,
    project_root: Path,
    receipt: LocalReceipt,
    row_contract: DatasetRowContract,
    row_set_contract: DatasetRowSetContract,
    policy: ReadPolicy = _READ_POLICY,
) -> pa.Table:
    """Read the fixed bounded ordered primary preview without scanning complete data."""
    return _read(
        project_root=project_root,
        receipt=receipt,
        row_contract=row_contract,
        row_set_contract=row_set_contract,
        preview=True,
        policy=policy,
    )


def _read_primary(
    *,
    project_root: Path,
    receipt: LocalReceipt,
    row_contract: DatasetRowContract,
    row_set_contract: DatasetRowSetContract,
    policy: ReadPolicy = _READ_POLICY,
) -> pd.DataFrame:
    """Collect complete retained primary rows into an isolated terminal DataFrame."""
    time.monotonic()
    table = _read(
        project_root=project_root,
        receipt=receipt,
        row_contract=row_contract,
        row_set_contract=row_set_contract,
        preview=False,
        policy=policy,
    )
    result = _to_dataframe(table, row_contract)
    return result


def read_primary(
    *,
    project_root: Path,
    receipt: LocalReceipt,
    row_contract: DatasetRowContract,
    row_set_contract: DatasetRowSetContract,
    policy: ReadPolicy = _READ_POLICY,
) -> pd.DataFrame:
    """Collect complete terminal rows in the calling process."""
    time.monotonic()
    if row_set_contract.cardinality.kind == "singleton" and receipt.realized_row_count != 1:
        _integrity("exactly one committed singleton row", "invalid singleton count")
    bound = getattr(row_set_contract.cardinality, "row_bound", None)
    if isinstance(bound, _StaticRowBound) and receipt.realized_row_count > bound.max_rows:
        _integrity("the declared static row bound", "committed row bound exceeded")
    return _read_primary(
        project_root=project_root,
        receipt=receipt,
        row_contract=row_contract,
        row_set_contract=row_set_contract,
        policy=policy,
    )
