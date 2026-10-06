"""Bounded immutable Parquet writes and source-free PyArrow primary reads."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal, TypeAlias

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
    _DeferredPhysicalType,
    _EntityFieldIdentity,
    _make_schema,
    _OrderedOrdering,
    _ResolvedPhysicalType,
    _StaticRowBound,
)
from marivo.analysis.materialization.contracts import (
    FileEntry,
    LocalReceipt,
    manifest_digest,
)
from marivo.analysis.materialization.errors import (
    IntegrityError,
    MaterializationError,
    StorageAccessError,
)

_Value: TypeAlias = (
    None | bool | int | float | str | date | datetime | Decimal | tuple["_Value", ...]
)


@dataclass(frozen=True, slots=True)
class StoragePolicy:
    """Parquet row-group tuning; no storage or allocation limits."""

    row_group_rows: int = 1024


@dataclass(frozen=True, slots=True)
class ReadPolicy:
    """Preview display length only; complete reads have no resource caps."""

    preview_rows: int = 20


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
    if logical == "unknown":
        return True
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


def _observed_type_id(actual: pa.DataType) -> str:
    value = _normal_type(actual)
    if pa.types.is_boolean(value):
        return "boolean"
    if pa.types.is_integer(value):
        return str(value)
    if pa.types.is_float64(value):
        return "float64"
    if pa.types.is_float32(value):
        return "float32"
    if pa.types.is_decimal(value):
        return "decimal"
    if pa.types.is_string(value) or pa.types.is_large_string(value):
        return "string"
    if pa.types.is_date(value):
        return "date"
    if pa.types.is_timestamp(value):
        return "timestamp"
    if pa.types.is_struct(value):
        return "identity_tuple"
    return "unknown"


def _realized_schema(row: DatasetRowContract, actual: pa.Schema) -> DatasetSchema:
    logical = row.schema
    if actual.names != [column.name for column in logical.columns]:
        _fail("the exact ordered primary column names", "primary columns differ")
    columns: list[DatasetField] = []
    for expected, field in zip(logical.columns, actual, strict=True):
        fragment = False
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
            else (
                _observed_type_id(field.type)
                if isinstance(physical, _DeferredPhysicalType)
                and physical.admitted_type_class_id == "unknown"
                else expected.logical_type_id
            )
        )
        logical_id = expected.logical_type_id
        if (
            isinstance(physical, _DeferredPhysicalType)
            and physical.admitted_type_class_id == "unknown"
        ):
            logical_id = physical_id
            if logical_id == "unknown":
                _fail(
                    "a supported observed source type",
                    str(field.type),
                    stage="state.realized_schema",
                )
        if isinstance(identity, _EntityFieldIdentity) and any(
            kind == "unknown" for _, kind in identity.identity_signature
        ):
            identity = replace(
                identity,
                _token=_CORE_TOKEN,
                identity_signature=tuple(
                    (child.name, _observed_type_id(child.type)) for child in field.type
                ),
            )
            if any(kind == "unknown" for _, kind in identity.identity_signature):
                _fail(
                    "supported observed Entity identity types",
                    str(field.type),
                    stage="state.realized_schema",
                )
        columns.append(
            replace(
                expected,
                _token=_CORE_TOKEN,
                identity=identity,
                logical_type_id=logical_id,
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

        self.contract = contract
        self.rows = rows
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
            ordered = tuple(
                _value(batch.column(batch.schema.get_field_index(name))[offset])
                for name, _, _ in self.terms
            )
            if self.previous is not None:
                comparison = 0
                for left, right, (_name, direction, nulls) in zip(
                    self.previous,
                    ordered,
                    self.terms,
                    strict=True,
                ):
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
