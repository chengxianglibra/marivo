"""Bounded reads of immutable local Parquet receipts."""

from __future__ import annotations

from collections.abc import Generator, Iterator
from pathlib import Path
from typing import Literal

import pyarrow as pa

from marivo.analysis.datasets.descriptors import DatasetRowContract, DatasetRowSetContract
from marivo.analysis.materialization import storage
from marivo.analysis.materialization.contracts import (
    StorageReceipt,
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
            if header.num_rows or any(
                expected.nullable != actual.nullable
                for expected, actual in zip(row.schema.columns, header.schema, strict=True)
            ):
                _integrity("the exact receipt-bound exchange schema", "selected schema differs")
            storage._realized_schema(row, header.schema)
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
    count = 0

    def checked(incoming: Iterator[pa.RecordBatch]) -> Iterator[pa.RecordBatch]:
        nonlocal count
        seen = False
        for batch in incoming:
            seen = True
            batch = storage._normalize_batch(batch)
            if preview:
                batch = batch.slice(0, max(0, policy.preview_rows - count))
            count += batch.num_rows
            yield batch
            if preview and count >= policy.preview_rows:
                return
        if not seen and receipt.realized_row_count:
            _integrity("all selected payload rows", "missing payload stream")

    parquet, _path = storage._open_payload(project_root, receipt)
    try:
        yield pa.RecordBatch.from_arrays(
            [pa.array([], type=f.type) for f in parquet.schema_arrow],
            schema=parquet.schema_arrow,
        )
        yield from checked(storage._parquet_batches(parquet, policy, preview=preview))
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
