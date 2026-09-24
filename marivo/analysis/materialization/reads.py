"""Bounded reads of immutable local Parquet receipts."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Generator, Iterator
from pathlib import Path
from typing import Literal

import pandas as pd
import pyarrow as pa

from marivo.analysis.datasets.descriptors import DatasetRowContract, DatasetRowSetContract
from marivo.analysis.materialization import storage
from marivo.analysis.materialization.contracts import (
    RetainedPart,
    StorageReceipt,
    schema_fingerprint,
)
from marivo.analysis.materialization.errors import MaterializationError, StorageAccessError
from marivo.analysis.materialization.storage import ReadPolicy, _integrity

_DEFAULT_READ_POLICY = ReadPolicy()


class _ReceiptBatchStream:
    """Adapt a governed local receipt to the existing schema-first BatchStream."""

    def __init__(
        self,
        project_root: Path,
        receipt: StorageReceipt,
        row: DatasetRowContract,
        *,
        policy: ReadPolicy,
    ) -> None:
        self._reader = payload_batches(project_root, receipt, policy=policy)
        self._started = False
        self._closed = False
        try:
            header = next(self._reader)
            if (
                header.num_rows
                or schema_fingerprint(storage._realized_schema(row, header.schema))
                != (receipt.schema_fingerprint)
                or any(
                    expected.nullable != actual.nullable
                    for expected, actual in zip(row.schema.columns, header.schema, strict=True)
                )
            ):
                _integrity("the exact receipt-bound exchange schema", "selected schema differs")
            self._header = header
        except BaseException:
            self._reader.close()
            raise

    @property
    def schema(self) -> pa.Schema:
        return self._header.schema

    def __iter__(self) -> Iterator[pa.RecordBatch]:
        if self._started or self._closed:
            _integrity("one open selected receipt stream", "receipt stream already used")
        self._started = True
        try:
            yield self._header
            yield from self._reader
        finally:
            self.close()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._reader.close()


def open_receipt_batch_stream(
    project_root: Path,
    receipt: StorageReceipt,
    row: DatasetRowContract,
    rows: DatasetRowSetContract,
    *,
    policy: ReadPolicy = _DEFAULT_READ_POLICY,
) -> _ReceiptBatchStream:
    """Open one selected primary payload at the authorized execution boundary."""
    if rows.cardinality.kind == "singleton" and receipt.realized_row_count != 1:
        _integrity("one committed singleton row", "selected row count differs")
    return _ReceiptBatchStream(project_root, receipt, row, policy=policy)


def _payload_batches(
    project_root: Path,
    receipt: StorageReceipt,
    *,
    policy: ReadPolicy,
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
) -> pa.Schema:
    """Read selected storage schema and bind it to its immutable receipt.

    Family consumers independently check the returned fields against their
    registered key/state contracts. Reading a header is not content validation;
    a consuming action must exhaust the subsequent guarded part read.
    """
    from marivo.analysis.materialization.retained import guard_part_transfer

    guard_part_transfer(part)
    stream = payload_batches(project_root, part.storage_receipt, policy=policy)
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
    preview: bool = False,
) -> pa.Table:
    return storage._read(
        project_root=project_root,
        receipt=receipt,
        row_contract=row_contract,
        row_set_contract=row_set_contract,
        policy=policy,
        preview=preview,
    )


def read_preview(
    *,
    project_root: Path,
    receipt: StorageReceipt,
    row_contract: DatasetRowContract,
    row_set_contract: DatasetRowSetContract,
    policy: ReadPolicy,
) -> pa.Table:
    return read_table(
        project_root=project_root,
        receipt=receipt,
        row_contract=row_contract,
        row_set_contract=row_set_contract,
        policy=policy,
        preview=True,
    )


def read_primary(
    *,
    project_root: Path,
    receipt: StorageReceipt,
    row_contract: DatasetRowContract,
    row_set_contract: DatasetRowSetContract,
    policy: ReadPolicy,
) -> pd.DataFrame:
    return storage.read_primary(
        project_root=project_root,
        receipt=receipt,
        row_contract=row_contract,
        row_set_contract=row_set_contract,
        policy=policy,
    )


def read_part_batches(
    project_root: Path,
    part: RetainedPart,
    *,
    expected_schema: pa.Schema,
    policy: ReadPolicy = _DEFAULT_READ_POLICY,
) -> Generator[pa.RecordBatch, None, None]:
    from marivo.analysis.materialization.retained import guard_part_transfer

    guard_part_transfer(part)
    return _read_part_batches(project_root, part, expected_schema=expected_schema, policy=policy)


def _read_part_batches(
    project_root: Path,
    part: RetainedPart,
    *,
    expected_schema: pa.Schema,
    policy: ReadPolicy,
) -> Generator[pa.RecordBatch, None, None]:
    receipt = part.storage_receipt
    if (
        receipt.schema_fingerprint
        != hashlib.sha256(expected_schema.serialize().to_pybytes()).hexdigest()
    ):
        _integrity("the exact registered part schema fingerprint", "required part schema differs")
    for batch in payload_batches(project_root, receipt, policy=policy):
        if not batch.schema.equals(expected_schema, check_metadata=False):
            _integrity("the exact registered part schema", "required part schema differs")
        for field in expected_schema:
            if not field.nullable and batch.column(field.name).null_count:
                _integrity("non-null required part fields", "null retained field")
        yield batch
