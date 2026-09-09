"""Bounded immutable Parquet writes and source-free PyArrow primary reads."""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from multiprocessing import Pipe
from multiprocessing.connection import Connection
from pathlib import Path
from threading import Event, Thread
from typing import BinaryIO, Generic, Literal, TypeAlias, TypeVar

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from typing_extensions import Buffer

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
from marivo.analysis.materialization import contracts as codec
from marivo.analysis.materialization.contracts import (
    FileEntry,
    LocalReceipt,
    RetainedPart,
    StorageReceipt,
    manifest_digest,
    schema_fingerprint,
)
from marivo.analysis.materialization.errors import (
    CollectionLimitError,
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
    max_stored_bytes: int = 67_108_864
    max_batch_bytes: int = 8_388_608
    row_group_rows: int = 1024


@dataclass(frozen=True, slots=True)
class ReadPolicy:
    preview_rows: int = 20
    max_rows: int = 100_000
    max_decoded_bytes: int = 67_108_864
    deadline_seconds: float = 60.0
    max_batch_bytes: int = 8_388_608


_STORAGE_POLICY = StoragePolicy()
_READ_POLICY = ReadPolicy()


@dataclass(frozen=True, slots=True)
class PartWriteSpec:
    role: str
    contract_id: str
    contract_version: int
    column_names: tuple[str, ...]


_ReceiptT = TypeVar("_ReceiptT", bound=StorageReceipt, covariant=True)


@dataclass(frozen=True, slots=True)
class DatasetWriteResult(Generic[_ReceiptT]):
    primary_receipt: _ReceiptT
    retained_parts: tuple[RetainedPart, ...]
    realized_schema: DatasetSchema
    realized_row_count: int


@dataclass(frozen=True, slots=True)
class SamplingStateRead:
    sampling: tuple[codec.SamplingRealization, ...]
    receipt: StorageReceipt


def sampling_state_read(descriptor: codec.ArtifactDescriptor) -> SamplingStateRead | None:
    """Select exact committed read authority without touching any Artifact backing."""
    if descriptor.sampling_execution is None:
        return None
    selected = tuple(
        part for part in descriptor.retained_parts if part.role == "population_sampling_state"
    )
    if len(selected) != 1:
        _integrity("one retained sampling receipt binding", "missing sampling state")
    return SamplingStateRead(descriptor.sampling_execution, selected[0].storage_receipt)


def _fail(expected: str, received: str, *, stage: str = "output_validation") -> Never:
    raise MaterializationError(
        expected=expected,
        received=received,
        repair="Use the exact registered schema, ordered row keys and bounded local storage route.",
        stage=stage,
    )


def _integrity(expected: str, received: str) -> Never:
    raise IntegrityError(
        expected=expected,
        received=received,
        repair="Restore the exact committed backing or explicitly author a new execution.",
        stage="storage_read",
    )


def _limited(received: str) -> Never:
    raise CollectionLimitError(
        expected="a complete retained result within the configured row, byte and deadline limits",
        received=received,
        repair="Narrow the logical Dataset before execution and collect its committed result.",
        stage="collection",
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


@dataclass(slots=True)
class _DiskBudget:
    limit: int
    used: int = 0


class _BudgetFile(io.BufferedIOBase):
    """The Arrow sink refuses bytes before they exceed the shared Artifact budget."""

    def __init__(self, path: Path, budget: _DiskBudget) -> None:
        self._stream: BinaryIO = path.open("xb")
        self._budget = budget

    def writable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._stream.tell()

    def write(self, data: Buffer) -> int:
        view = memoryview(data)
        size = view.nbytes
        if self._budget.used + size > self._budget.limit:
            _fail(
                "stored bytes within the local Artifact budget",
                "disk budget exceeded",
                stage="transfer_guard",
            )
        written = self._stream.write(view)
        self._budget.used += written
        return written

    def flush(self) -> None:
        if not self._stream.closed:
            self._stream.flush()

    def close(self) -> None:
        if not self.closed:
            try:
                self.flush()
                os.fsync(self._stream.fileno())
            finally:
                try:
                    super().close()
                finally:
                    self._stream.close()


def _normal_type(value: pa.DataType) -> pa.DataType:
    if pa.types.is_dictionary(value):
        return _normal_type(value.value_type)
    if pa.types.is_struct(value):
        return pa.struct(
            [pa.field(item.name, _normal_type(item.type), item.nullable) for item in value]
        )
    return value


def _decoded_bound(column: pa.Array, limit: int) -> int:
    if pa.types.is_struct(column.type):
        estimate = (len(column) + 7) // 8
        for index in range(column.type.num_fields):
            estimate += _decoded_bound(column.field(index), limit - estimate)
            if estimate > limit:
                _fail(
                    "bounded struct decoding",
                    "decoded batch budget exceeded",
                    stage="transfer_guard",
                )
        return estimate
    if not pa.types.is_dictionary(column.type):
        return int(column.nbytes)
    estimate = 0
    for scalar in column:
        value: object = scalar.as_py()
        estimate += len(value.encode("utf-8")) + 8 if isinstance(value, str) else 16
        if estimate > limit:
            _fail(
                "bounded dictionary decoding",
                "decoded batch budget exceeded",
                stage="transfer_guard",
            )
    return estimate


def _normalize_batch(batch: pa.RecordBatch, limit: int) -> pa.RecordBatch:
    if batch.nbytes > limit:
        _fail("a bounded decoded Arrow batch", "batch byte budget exceeded", stage="transfer_guard")
    fields = [pa.field(item.name, _normal_type(item.type), item.nullable) for item in batch.schema]
    target = pa.schema(fields)
    if not batch.schema.equals(target, check_metadata=False):
        # Check dictionary expansion one scalar at a time before allocating decoded arrays.
        estimate = 0
        for column in batch.columns:
            estimate += _decoded_bound(column, limit - estimate)
        if estimate > limit:
            _fail(
                "a bounded normalized batch",
                "decoded batch budget exceeded",
                stage="transfer_guard",
            )
        batch = batch.cast(target)
    if batch.nbytes > limit:
        _fail("a bounded normalized batch", "decoded batch budget exceeded", stage="transfer_guard")
    return batch


def _matches_type(logical: str, actual: pa.DataType) -> bool:
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


def _realized_schema(logical: DatasetSchema, actual: pa.Schema) -> DatasetSchema:
    if actual.names != [column.name for column in logical.columns]:
        _fail("the exact ordered primary column names", "primary columns differ")
    columns: list[DatasetField] = []
    for expected, field in zip(logical.columns, actual, strict=True):
        if not _matches_type(expected.logical_type_id, field.type):
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
        if isinstance(physical, _ResolvedPhysicalType) and not _matches_type(
            physical.physical_type_id, field.type
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


def _value(scalar: pa.Scalar) -> _Value:
    if not scalar.is_valid:
        return None
    if pa.types.is_struct(scalar.type):
        return tuple(_value(scalar[index]) for index in range(len(scalar.type)))
    if pa.types.is_list(scalar.type) or pa.types.is_fixed_size_list(scalar.type):
        values: object = scalar.as_py()
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
        self.contract = contract
        self.rows = rows
        self.attribution_masks: tuple[tuple[bool, ...], ...] | None = None
        self.attribution_axes: tuple[str, ...] = ()
        if contract.shape_id.family_id == "attribution":
            from marivo.analysis.operators.attribution_contracts import AttributionSemantics

            semantics = contract.family_semantics
            if not isinstance(semantics, AttributionSemantics):
                _fail("exact Attribution row semantics", "missing Attribution authority")
            self.attribution_masks = tuple(
                tuple(index < len(prefix) for index in range(len(semantics.axis_field_ids)))
                for prefix in semantics.resolution_prefixes
            )
            self.attribution_axes = tuple(by_id[field_id] for field_id in semantics.axis_field_ids)
        self.previous: tuple[_Value, ...] | None = None
        self.count = 0

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
            arity = _bool_tuple_arity(field.logical_type_id)
            if arity is not None and (
                not _matches_type(field.logical_type_id, column.type)
                or any(
                    not isinstance(value, list) or _bool_tuple_value(value, arity=arity) is None
                    for value in column.to_pylist()
                )
            ):
                _fail("the exact fixed-length boolean partition mask", "invalid mask values")
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
            if self.previous is not None:
                comparison = 0
                for left, right, (name, direction, nulls) in zip(
                    self.previous, ordered, self.terms, strict=True
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
    sampling: tuple[codec.SamplingRealization, ...] = (),
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
    if sampling and any(part.role == "population_sampling_state" for part in parts):
        _fail("one owned sampling-state role", "duplicate sampling state")
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
    budget = _DiskBudget(policy.max_stored_bytes)
    _create_directory(staging)
    writers: list[pq.ParquetWriter] = []
    sinks: list[_BudgetFile] = []
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
            batch = _normalize_batch(incoming, policy.max_batch_bytes)
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
                        realized = _realized_schema(row_contract.schema, schema)
                        schema = pa.schema(
                            [
                                pa.field(field.name, schema.field(field.name).type, field.nullable)
                                for field in row_contract.schema.columns
                            ]
                        )
                    schemas.append(schema)
                    sink = _BudgetFile(target / "data.parquet", budget)
                    sinks.append(sink)
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
        if sampling:
            directory = "parts/population_sampling_state"
            target = staging / directory
            _create_directory(target)
            schema = pa.schema([pa.field("sampling_execution_digest", pa.string(), nullable=False)])
            batch = pa.RecordBatch.from_arrays(
                [pa.array([codec.digest(codec.sampling_payload(sampling))], type=pa.string())],
                schema=schema,
            )
            with (
                _BudgetFile(target / "data.parquet", budget) as sink,
                pq.ParquetWriter(
                    sink, schema, compression="zstd", write_page_checksum=True
                ) as writer,
            ):
                writer.write_batch(batch)
            schemas.append(schema)
            directories += (directory,)
            row_counts += (1,)
            retained_specs += (("population_sampling_state", "population_sampling_state", 1),)
        receipts: list[LocalReceipt] = []
        for index, (directory, schema, row_count) in enumerate(
            zip(directories, schemas, row_counts, strict=True)
        ):
            target = staging / directory
            file = target / "data.parquet"
            with pq.ParquetFile(file) as parquet:
                if parquet.metadata.num_rows != row_count or not parquet.schema_arrow.equals(
                    schema, check_metadata=False
                ):
                    _fail(
                        "an exact schema and row-count Parquet round trip",
                        "written Parquet differs",
                    )
            entry = FileEntry("data.parquet", file.stat().st_size, _hash_file(file))
            entries = (entry,)
            manifest = _manifest_bytes(entries)
            with _BudgetFile(target / "manifest.json", budget) as stream:
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
                    else hashlib.sha256(schema.serialize().to_pybytes()).hexdigest(),
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
            with suppress(OSError, MaterializationError, pa.ArrowException):
                writer.close()
        for sink in sinks:
            with suppress(OSError, MaterializationError):
                sink.close()


def validate_sampling_state(project_root: Path, state: SamplingStateRead | None) -> None:
    """Verify the bounded retained receipt binding without source or membership reads."""
    if state is None:
        return
    sampling = state.sampling
    receipt = state.receipt
    if not isinstance(receipt, LocalReceipt):
        _integrity("the local sampling reader", "non-local sampling receipt")
    root = _checked_path(project_root, Path(receipt.project_relative_path))
    if len(receipt.file_manifest) != 1 or receipt.realized_row_count != 1:
        _integrity("one bounded sampling state row", "invalid sampling state receipt")
    entry = receipt.file_manifest[0]
    if entry.relative_path != "data.parquet" or entry.size_bytes > 65_536:
        _integrity("the bounded sampling state Parquet file", "invalid sampling backing")
    data = _checked_path(project_root, root / "data.parquet")
    manifest = _checked_path(project_root, root / "manifest.json")
    schema = pa.schema([pa.field("sampling_execution_digest", pa.string(), nullable=False)])
    try:
        expected_manifest = _manifest_bytes(receipt.file_manifest)
        if (
            data.stat().st_size != entry.size_bytes
            or manifest.stat().st_size != len(expected_manifest)
            or manifest.read_bytes() != expected_manifest
            or _hash_file(data) != receipt.bytes_hash
            or entry.sha256 != receipt.bytes_hash
            or receipt.schema_fingerprint
            != hashlib.sha256(schema.serialize().to_pybytes()).hexdigest()
            or receipt.realized_byte_count != entry.size_bytes + len(expected_manifest)
        ):
            _integrity(
                "the exact retained sampling state receipt", "sampling state backing changed"
            )
        with pq.ParquetFile(data, page_checksum_verification=True) as parquet:
            if (
                parquet.metadata.num_rows != 1
                or parquet.metadata.num_row_groups != 1
                or parquet.metadata.row_group(0).total_byte_size > 65_536
                or not parquet.schema_arrow.equals(schema)
            ):
                _integrity("the bounded sampling state schema", "invalid sampling state rows")
            value: object = parquet.read()["sampling_execution_digest"][0].as_py()
            if value != codec.digest(codec.sampling_payload(sampling)):
                _integrity(
                    "sampling state bound to the exact execution receipt",
                    "sampling receipt differs",
                )
    except (OSError, pa.ArrowException):
        _integrity(
            "accessible valid retained sampling state",
            "sampling state backing is absent or corrupt",
        )


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
        realized = _realized_schema(row.schema, parquet.schema_arrow)
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
    if receipt.realized_row_count > policy.max_rows:
        _limited("required part row count exceeds the collection limit")
    expected_hash = hashlib.sha256(expected_schema.serialize().to_pybytes()).hexdigest()
    if receipt.schema_fingerprint != expected_hash:
        _integrity("the exact registered part schema", "required part schema fingerprint differs")
    parquet, data = _open_payload(project_root, receipt)
    started = time.monotonic()
    count = decoded = 0
    try:
        if not parquet.schema_arrow.equals(expected_schema, check_metadata=False):
            _integrity("the exact registered part schema", "required part schema differs")
        for batch in _bounded_batches(parquet, policy, preview=False):
            count += batch.num_rows
            decoded += batch.nbytes
            if count > policy.max_rows or decoded > policy.max_decoded_bytes:
                _limited("required part exceeds the collection budget")
            if time.monotonic() - started > policy.deadline_seconds:
                _limited("required part read deadline exceeded")
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
            isinstance(field.identity, _EntityFieldIdentity)
            or _bool_tuple_arity(field.logical_type_id) is not None
        ):
            array = table.column(field.name)
            result[field.name] = pd.Series(
                [_value(array[index]) for index in range(table.num_rows)], dtype=object
            )
    return result.copy(deep=True)


def _bounded_batches(
    parquet: pq.ParquetFile, policy: ReadPolicy, *, preview: bool, use_threads: bool = True
) -> Iterator[pa.RecordBatch]:
    remaining = policy.preview_rows
    for index in range(parquet.metadata.num_row_groups):
        group = parquet.metadata.row_group(index)
        if group.total_byte_size > policy.max_batch_bytes:
            _limited("declared uncompressed row group exceeds the decoded batch limit")
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
    started = time.monotonic()
    if row_set_contract.cardinality.kind == "singleton" and receipt.realized_row_count != 1:
        _integrity("exactly one committed singleton row", "invalid singleton count")
    bound = getattr(row_set_contract.cardinality, "row_bound", None)
    if isinstance(bound, _StaticRowBound) and receipt.realized_row_count > bound.max_rows:
        _integrity("the declared static row bound", "committed row bound exceeded")
    if not preview and receipt.realized_row_count > policy.max_rows:
        _limited("committed row count exceeds the collection limit")
    parquet, data = _open_primary(project_root, receipt, row_contract)
    # The committed descriptor requires the producer's independent key validation.
    validator = _RowValidator(row_contract, row_set_contract, source_key_validation=True)
    retained: list[pa.RecordBatch] = []
    decoded = 0
    remaining = policy.preview_rows if preview else receipt.realized_row_count
    try:
        for batch in _bounded_batches(parquet, policy, preview=preview):
            if time.monotonic() - started > policy.deadline_seconds:
                _limited("retained read deadline exceeded")
            if batch.nbytes > policy.max_batch_bytes:
                _limited("decoded batch limit exceeded")
            selected = batch.slice(0, remaining) if preview else batch
            decoded += selected.nbytes
            if decoded > policy.max_decoded_bytes:
                _limited("decoded byte limit exceeded")
            validator.accept(selected)
            retained.append(selected)
            remaining -= selected.num_rows
            if preview and remaining <= 0:
                break
            if not preview and validator.count > policy.max_rows:
                _limited("realized row count exceeds the collection limit")
        if not preview:
            validator.finish()
            if validator.count != receipt.realized_row_count:
                _integrity("all committed primary rows", "incomplete primary read")
            if (
                _hash_file(data) != receipt.bytes_hash
                or receipt.file_manifest[0].sha256 != receipt.bytes_hash
            ):
                _integrity("the exact immutable primary content hash", "primary content changed")
        if time.monotonic() - started > policy.deadline_seconds:
            _limited("retained read deadline exceeded")
        return pa.Table.from_batches(retained, schema=parquet.schema_arrow)
    except (IntegrityError, CollectionLimitError):
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
    started = time.monotonic()
    table = _read(
        project_root=project_root,
        receipt=receipt,
        row_contract=row_contract,
        row_set_contract=row_set_contract,
        preview=False,
        policy=policy,
    )
    result = _to_dataframe(table, row_contract)
    if int(result.memory_usage(index=True, deep=True).sum()) > policy.max_decoded_bytes:
        _limited("complete DataFrame byte limit exceeded")
    if time.monotonic() - started > policy.deadline_seconds:
        _limited("complete collection deadline exceeded")
    return result


_WORKER_CODE = (
    "from marivo.analysis.materialization.storage import _read_worker_entry; _read_worker_entry()"
)


def _read_request(
    project_root: Path,
    receipt: StorageReceipt,
    row: DatasetRowContract,
    rows: DatasetRowSetContract,
    policy: ReadPolicy,
) -> str:
    return codec.canonical_json(
        {
            "schema": "marivo.primary_read/v1",
            "project_root": str(project_root.absolute()),
            "receipt": codec.receipt_payload(receipt),
            "row": codec.row_payload(row),
            "row_set": codec.row_set_payload(rows),
            "policy": {
                "preview_rows": policy.preview_rows,
                "max_rows": policy.max_rows,
                "max_decoded_bytes": policy.max_decoded_bytes,
                "deadline_seconds": policy.deadline_seconds,
                "max_batch_bytes": policy.max_batch_bytes,
            },
        }
    )


def _read_request_value(text: str) -> pd.DataFrame:
    from marivo.analysis.observation.contracts import make_ids

    raw = codec.parse_json(text)
    obj = codec._obj(raw, "schema project_root receipt row row_set policy")
    if obj["schema"] != "marivo.primary_read/v1":
        _integrity("the supported read request version", "unsupported read request")
    policy_obj = codec._obj(
        obj["policy"], "preview_rows max_rows max_decoded_bytes deadline_seconds max_batch_bytes"
    )
    seconds = policy_obj["deadline_seconds"]
    if (
        type(seconds) not in (int, float)
        or not isinstance(seconds, (int, float))
        or not math.isfinite(seconds)
        or seconds <= 0
    ):
        _limited("invalid collection deadline")
    policy = ReadPolicy(
        preview_rows=codec._int(policy_obj["preview_rows"], minimum=1),
        max_rows=codec._int(policy_obj["max_rows"]),
        max_decoded_bytes=codec._int(policy_obj["max_decoded_bytes"], minimum=1),
        deadline_seconds=float(seconds),
        max_batch_bytes=codec._int(policy_obj["max_batch_bytes"], minimum=1),
    )
    ids = make_ids(())
    receipt = codec.decode_receipt(obj["receipt"])
    if not isinstance(receipt, LocalReceipt):
        _integrity("the local read worker", "non-local receipt")
    return _read_primary(
        project_root=Path(codec._text(obj["project_root"])),
        receipt=receipt,
        row_contract=codec.decode_row(obj["row"], ids),
        row_set_contract=codec.decode_row_set(obj["row_set"], ids),
        policy=policy,
    )


def _read_worker_entry(
    read_value: Callable[[str], pd.DataFrame] = _read_request_value,
) -> None:
    """Run the selected reader behind the shared bounded IPC/error envelope."""
    connection = Connection(int(sys.argv[1]), readable=False, writable=True)
    try:
        payload = sys.stdin.buffer.read(1_048_577)
        if len(payload) > 1_048_576:
            _integrity("a bounded canonical read request", "read request exceeded its limit")
        result = read_value(payload.decode("utf-8"))
        connection.send(("dataframe", result))
    except MaterializationError as error:
        kind = (
            "integrity"
            if isinstance(error, IntegrityError)
            else "limit"
            if isinstance(error, CollectionLimitError)
            else "materialization"
        )
        connection.send(
            (
                "failure",
                codec.canonical_json(
                    {
                        "kind": kind,
                        "expected": error.expected,
                        "received": error.received,
                        "repair": error.hint,
                        "stage": error.stage,
                    }
                ),
            )
        )
    except Exception:
        connection.send(
            (
                "failure",
                codec.canonical_json(
                    {
                        "kind": "materialization",
                        "expected": "a complete supported retained primary read",
                        "received": "the isolated read worker could not complete",
                        "repair": "Inspect the exact selected backing and retry the retained read.",
                        "stage": "storage_read",
                    }
                ),
            )
        )
    finally:
        connection.close()


def _response(value: object) -> pd.DataFrame:
    if not isinstance(value, tuple) or len(value) != 2:
        _integrity("one complete terminal read response", "invalid read worker response")
    kind, body = value
    if kind == "dataframe" and isinstance(body, pd.DataFrame):
        return body
    if kind == "failure" and isinstance(body, str):
        failure = codec._obj(codec.parse_json(body), "kind expected received repair stage")
        error_type = {
            "integrity": IntegrityError,
            "limit": CollectionLimitError,
            "materialization": MaterializationError,
        }.get(codec._text(failure["kind"]))
        if error_type is not None:
            raise error_type(
                expected=codec._text(failure["expected"]),
                received=codec._text(failure["received"]),
                repair=codec._text(failure["repair"]),
                stage=codec._text(failure["stage"]),
            )
    _integrity("a supported terminal read response", "invalid read worker outcome")


def _supervise_read(
    payload: str, seconds: float, *, worker_code: str = _WORKER_CODE
) -> pd.DataFrame:
    if seconds <= 0 or not math.isfinite(seconds):
        _limited("collection deadline exceeded before the worker started")
    if os.name != "posix":
        _fail(
            "a supported terminable local read worker",
            "this platform lacks the registered file-descriptor handoff",
            stage="collection",
        )
    deadline = time.monotonic() + seconds
    receive, send = Pipe(duplex=False)
    completed = Event()
    response: list[object] = []
    process: subprocess.Popen[bytes] | None = None
    collector: Thread | None = None
    try:
        environment = os.environ.copy()
        environment["MARIVO_TELEMETRY"] = "off"
        process = subprocess.Popen(
            [sys.executable, "-B", "-c", worker_code, str(send.fileno())],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            pass_fds=(send.fileno(),),
            env=environment,
        )
        send.close()
        input_stream = process.stdin
        if input_stream is None:
            _integrity("the isolated worker input pipe", "missing read worker pipe")

        def collect() -> None:
            try:
                input_stream.write(payload.encode("utf-8"))
                input_stream.close()
                received: object = receive.recv()
                response.append(received)
            except Exception:
                # An incomplete or undecodable IPC value is never a terminal result.
                pass
            finally:
                completed.set()

        collector = Thread(target=collect, name="marivo-primary-read", daemon=True)
        collector.start()
        if not completed.wait(max(0, deadline - time.monotonic())):
            _limited("the isolated collection worker exceeded its deadline")
        try:
            process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            _limited("the isolated collection worker did not terminate before its deadline")
        if process.returncode != 0 or not response:
            _integrity(
                "a successful complete read worker response",
                "read worker exited without a complete response",
            )
        if time.monotonic() > deadline:
            _limited("complete collection deadline exceeded")
        return _response(response[0])
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
        receive.close()
        send.close()
        if collector is not None:
            collector.join(timeout=1)
        if process is not None and process.stdin is not None:
            process.stdin.close()


def read_primary(
    *,
    project_root: Path,
    receipt: LocalReceipt,
    row_contract: DatasetRowContract,
    row_set_contract: DatasetRowSetContract,
    policy: ReadPolicy = _READ_POLICY,
) -> pd.DataFrame:
    """Collect isolated terminal rows under a terminable fresh-process deadline."""
    started = time.monotonic()
    if receipt.realized_row_count > policy.max_rows:
        _limited("committed row count exceeds the collection limit")
    if row_set_contract.cardinality.kind == "singleton" and receipt.realized_row_count != 1:
        _integrity("exactly one committed singleton row", "invalid singleton count")
    bound = getattr(row_set_contract.cardinality, "row_bound", None)
    if isinstance(bound, _StaticRowBound) and receipt.realized_row_count > bound.max_rows:
        _integrity("the declared static row bound", "committed row bound exceeded")
    payload = _read_request(project_root, receipt, row_contract, row_set_contract, policy)
    return _supervise_read(payload, policy.deadline_seconds - (time.monotonic() - started))
