"""Selected receipt dispatch with bounded engine and version-pinned PyArrow reads."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Generator, Iterator
from pathlib import Path
from typing import Literal

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from marivo.analysis.datasets.descriptors import DatasetRowContract, DatasetRowSetContract
from marivo.analysis.materialization import contracts as codec
from marivo.analysis.materialization import storage
from marivo.analysis.materialization.contracts import (
    LocalReceipt,
    ObjectReceipt,
    RetainedPart,
    StorageReceipt,
)
from marivo.analysis.materialization.errors import MaterializationError, StorageAccessError
from marivo.analysis.materialization.storage import ReadPolicy, _integrity
from marivo.analysis.materialization.targets import (
    ObjectBinding,
    S3Access,
    object_access,
)

_DEFAULT_READ_POLICY = ReadPolicy()


def _object_read_access(bindings: tuple[ObjectBinding, ...], reference: str) -> S3Access:
    """Classify unavailable reader authority without changing target selection errors."""
    try:
        return object_access(bindings, reference)
    except MaterializationError as error:
        if type(error) is not MaterializationError or error.stage != "storage_selection":
            raise
    raise StorageAccessError("unauthorized")


def _payload_batches(
    project_root: Path,
    receipt: StorageReceipt,
    *,
    policy: ReadPolicy,
    bindings: tuple[ObjectBinding, ...] = (),
    preview: bool = False,
    row: DatasetRowContract | None = None,
    rows: DatasetRowSetContract | None = None,
    audit: bool = False,
) -> Iterator[pa.RecordBatch]:
    """Read only this payload. Complete exhaustion includes content verification."""
    time.monotonic()
    count = decoded = 0

    def checked(incoming: Iterator[pa.RecordBatch]) -> Iterator[pa.RecordBatch]:
        nonlocal count, decoded
        seen = False
        for batch in incoming:
            seen = True
            batch = storage._normalize_batch(batch)
            if preview:
                batch = batch.slice(0, max(0, policy.preview_rows - count))
            count += batch.num_rows
            decoded += batch.nbytes
            yield batch
            if preview and count >= policy.preview_rows:
                return
        if not seen and receipt.realized_row_count:
            _integrity("all selected payload rows", "missing payload stream")

    if isinstance(receipt, ObjectReceipt):
        from marivo.analysis.materialization.object_storage import (
            ObjectRangeFile,
            client,
            open_manifest,
        )

        access = _object_read_access(bindings, receipt.object_store_ref)
        with client(access) as s3:
            file = open_manifest(s3, access, receipt)
            with (
                ObjectRangeFile(s3, access, file) as stream,
                pq.ParquetFile(stream, page_checksum_verification=True) as parquet,
            ):
                if parquet.metadata.num_rows != receipt.realized_row_count:
                    _integrity("the committed object row count", "object row count differs")
                yield pa.RecordBatch.from_arrays(
                    [pa.array([], type=f.type) for f in parquet.schema_arrow],
                    schema=parquet.schema_arrow,
                )
                yield from checked(
                    storage._parquet_batches(parquet, policy, preview=preview, use_threads=False)
                )
            if not preview:
                file.verify(s3, access)
    else:
        parquet, path = storage._open_payload(project_root, receipt)
        try:
            if audit and storage._hash_file(path) != receipt.bytes_hash:
                raise StorageAccessError("mutated")
            yield pa.RecordBatch.from_arrays(
                [pa.array([], type=f.type) for f in parquet.schema_arrow],
                schema=parquet.schema_arrow,
            )
            yield from checked(storage._parquet_batches(parquet, policy, preview=preview))
            if not preview and (
                storage._hash_file(path) != receipt.bytes_hash
                or receipt.file_manifest[0].sha256 != receipt.bytes_hash
            ):
                _integrity("the immutable selected local payload", "local content changed")
        finally:
            parquet.close()
    if not preview and count != receipt.realized_row_count:
        _integrity("the complete exact payload count", "incomplete selected payload")


def payload_batches(
    project_root: Path,
    receipt: StorageReceipt,
    *,
    policy: ReadPolicy,
    bindings: tuple[ObjectBinding, ...] = (),
    preview: bool = False,
    row: DatasetRowContract | None = None,
    rows: DatasetRowSetContract | None = None,
    audit: bool = False,
) -> Generator[pa.RecordBatch, None, None]:
    """Reject private membership before allocating any generic reader or iterator."""
    from marivo.analysis.materialization.retained import guard_receipt_transfer

    guard_receipt_transfer(receipt)
    return _guarded_payload_batches(
        project_root,
        receipt,
        policy=policy,
        bindings=bindings,
        preview=preview,
        row=row,
        rows=rows,
        audit=audit,
    )


def _guarded_payload_batches(
    project_root: Path,
    receipt: StorageReceipt,
    *,
    policy: ReadPolicy,
    bindings: tuple[ObjectBinding, ...] = (),
    preview: bool = False,
    row: DatasetRowContract | None = None,
    rows: DatasetRowSetContract | None = None,
    audit: bool = False,
) -> Generator[pa.RecordBatch, None, None]:
    """Keep native reader diagnostics and raw locators outside error chains."""
    failure: Literal["missing", "unauthorized", "mutated", "unknown"] = "unknown"
    try:
        yield from _payload_batches(
            project_root,
            receipt,
            policy=policy,
            bindings=bindings,
            preview=preview,
            row=row,
            rows=rows,
            audit=audit,
        )
        return
    except MaterializationError:
        raise
    except FileNotFoundError:
        failure = "missing"
    except PermissionError:
        failure = "unauthorized"
    except pa.ArrowException:
        failure = "mutated"
    except Exception:
        pass
    raise StorageAccessError(failure)


def part_schema(
    project_root: Path,
    part: RetainedPart,
    *,
    policy: ReadPolicy = _DEFAULT_READ_POLICY,
    bindings: tuple[ObjectBinding, ...] = (),
) -> pa.Schema:
    """Read selected storage schema and bind it to its immutable receipt.

    Family consumers independently check the returned fields against their
    registered key/state contracts. Reading a header is not content validation;
    a consuming action must exhaust the subsequent guarded part read.
    """
    from marivo.analysis.materialization.retained import guard_part_transfer

    guard_part_transfer(part)
    stream = payload_batches(project_root, part.storage_receipt, policy=policy, bindings=bindings)
    try:
        batch = next(stream, None)
        if batch is None or batch.num_rows:
            _integrity("a bounded selected part schema header", "missing part schema header")
        schema = batch.schema
        if hashlib.sha256(schema.serialize().to_pybytes()).hexdigest() != (
            part.storage_receipt.schema_fingerprint
        ):
            _integrity("the receipt-bound exact part schema", "part schema fingerprint differs")
        return schema
    finally:
        stream.close()


def read_table(
    *,
    project_root: Path,
    receipt: StorageReceipt,
    row_contract: DatasetRowContract,
    row_set_contract: DatasetRowSetContract,
    policy: ReadPolicy,
    bindings: tuple[ObjectBinding, ...] = (),
    preview: bool = False,
) -> pa.Table:
    if isinstance(receipt, LocalReceipt):
        return storage._read(
            project_root=project_root,
            receipt=receipt,
            row_contract=row_contract,
            row_set_contract=row_set_contract,
            policy=policy,
            preview=preview,
        )
    validator = storage._RowValidator(row_contract, row_set_contract, source_key_validation=True)
    retained: list[pa.RecordBatch] = []
    schema: pa.Schema | None = None
    for batch in payload_batches(
        project_root,
        receipt,
        policy=policy,
        bindings=bindings,
        preview=preview,
        row=row_contract,
        rows=row_set_contract,
    ):
        realized = storage._realized_schema(row_contract, batch.schema)
        if codec.schema_fingerprint(realized) != receipt.schema_fingerprint:
            _integrity("the exact selected realized schema", "receipt schema differs")
        if schema is not None and not schema.equals(batch.schema, check_metadata=False):
            _integrity("one complete selected schema", "changing payload schema")
        schema = batch.schema
        validator.accept(batch)
        retained.append(batch)
    if schema is None:
        _integrity("the selected payload schema", "missing payload schema")
    if not preview:
        validator.finish()
    return pa.Table.from_batches(retained, schema=schema)


def read_preview(
    *,
    project_root: Path,
    receipt: StorageReceipt,
    row_contract: DatasetRowContract,
    row_set_contract: DatasetRowSetContract,
    policy: ReadPolicy,
    bindings: tuple[ObjectBinding, ...] = (),
) -> pa.Table:
    return read_table(
        project_root=project_root,
        receipt=receipt,
        row_contract=row_contract,
        row_set_contract=row_set_contract,
        policy=policy,
        bindings=bindings,
        preview=True,
    )


def read_primary(
    *,
    project_root: Path,
    receipt: StorageReceipt,
    row_contract: DatasetRowContract,
    row_set_contract: DatasetRowSetContract,
    policy: ReadPolicy,
    bindings: tuple[ObjectBinding, ...] = (),
) -> pd.DataFrame:
    if isinstance(receipt, LocalReceipt):
        return storage.read_primary(
            project_root=project_root,
            receipt=receipt,
            row_contract=row_contract,
            row_set_contract=row_set_contract,
            policy=policy,
        )
    table = read_table(
        project_root=project_root,
        receipt=receipt,
        row_contract=row_contract,
        row_set_contract=row_set_contract,
        preview=False,
        policy=policy,
        bindings=bindings,
    )
    return storage._to_dataframe(table, row_contract)


def read_part_batches(
    project_root: Path,
    part: RetainedPart,
    *,
    expected_schema: pa.Schema,
    policy: ReadPolicy = _DEFAULT_READ_POLICY,
    bindings: tuple[ObjectBinding, ...] = (),
) -> Iterator[pa.RecordBatch]:
    from marivo.analysis.materialization.retained import guard_part_transfer

    guard_part_transfer(part)
    return _read_part_batches(
        project_root, part, expected_schema=expected_schema, policy=policy, bindings=bindings
    )


def _read_part_batches(
    project_root: Path,
    part: RetainedPart,
    *,
    expected_schema: pa.Schema,
    policy: ReadPolicy,
    bindings: tuple[ObjectBinding, ...],
) -> Iterator[pa.RecordBatch]:
    receipt = part.storage_receipt
    if (
        receipt.schema_fingerprint
        != hashlib.sha256(expected_schema.serialize().to_pybytes()).hexdigest()
    ):
        _integrity("the exact registered part schema fingerprint", "required part schema differs")
    for batch in payload_batches(project_root, receipt, policy=policy, bindings=bindings):
        if not batch.schema.equals(expected_schema, check_metadata=False):
            _integrity("the exact registered part schema", "required part schema differs")
        for field in expected_schema:
            if not field.nullable and batch.column(field.name).null_count:
                _integrity("non-null required part fields", "null retained field")
        yield batch
