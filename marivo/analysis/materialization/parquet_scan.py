"""Fixed native scans of immutable Parquet, with no durable database output."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

import ibis.expr.types as ir
import pyarrow as pa

from marivo.analysis.datasets.descriptors import DatasetRowContract
from marivo.analysis.materialization import contracts as codec
from marivo.analysis.materialization.contracts import LocalReceipt, ObjectReceipt, StorageReceipt
from marivo.analysis.materialization.errors import StorageAccessError
from marivo.analysis.materialization.execution import ExecutionAdapter
from marivo.analysis.materialization.storage import (
    ReadPolicy,
    _hash_file,
    _integrity,
    _open_payload,
    _realized_schema,
)
from marivo.analysis.materialization.targets import ObjectBinding


def checked_local_path(root: Path, receipt: LocalReceipt, *, verify_schema: bool = False) -> Path:
    """Check manifest, size, schema, count and complete bytes before a native scan."""
    parquet, path = _open_payload(root, receipt)
    try:
        if (
            _hash_file(path) != receipt.bytes_hash
            or receipt.file_manifest[0].sha256 != receipt.bytes_hash
        ):
            raise StorageAccessError("mutated")
        if (
            verify_schema
            and hashlib.sha256(parquet.schema_arrow.serialize().to_pybytes()).hexdigest()
            != receipt.schema_fingerprint
        ):
            _integrity("the exact immutable part schema", "Parquet part schema differs")
    finally:
        parquet.close()
    return path


def attach_parquet_scan(
    backend: ExecutionAdapter,
    root: Path,
    receipt: StorageReceipt,
    *,
    bindings: tuple[ObjectBinding, ...] = (),
    verify_schema: bool = False,
) -> ir.Table:
    """Use the preselected native adapter; object streams are frozen once in memory."""
    name = "mv_parquet_" + uuid4().hex
    if isinstance(receipt, LocalReceipt):
        path = checked_local_path(root, receipt, verify_schema=verify_schema)
        return backend.read_parquet(str(path), table_name=name)
    if not isinstance(receipt, ObjectReceipt):
        _integrity("an immutable Parquet receipt", "unsupported native scan storage")
    from marivo.analysis.materialization.reads import _guarded_payload_batches

    # This native stream never enters a pandas complete-input path.
    stream = _guarded_payload_batches(
        root, receipt, policy=ReadPolicy(), bindings=bindings, audit=True
    )
    try:
        header = next(stream)
        if (
            verify_schema
            and hashlib.sha256(header.schema.serialize().to_pybytes()).hexdigest()
            != receipt.schema_fingerprint
        ):
            _integrity("the exact immutable part schema", "Parquet part schema differs")
        with pa.RecordBatchReader.from_batches(header.schema, stream) as reader:
            backend.freeze_reader(name, reader)
    finally:
        stream.close()
    return backend.table(name)


def validate_parquet_relation(
    backend: ExecutionAdapter,
    table: ir.Table,
    receipt: StorageReceipt,
    row: DatasetRowContract,
    record: Callable[[str, str], None],
) -> None:
    count_sql = backend.compile(table.count())
    record("parquet_check.input_count", count_sql)
    if (
        backend.read_scalar(backend.prepare(table.count(), role="parquet_check.input_count"))
        != receipt.realized_row_count
    ):
        _integrity("the exact Parquet receipt row count", "native scan count differs")
    record("parquet_check.input_schema", backend.compile(table.limit(0)))
    realized = _realized_schema(
        row,
        backend.read_table(
            backend.prepare(table.limit(0), role="parquet_check.input_schema")
        ).schema,
    )
    if codec.schema_fingerprint(realized) != receipt.schema_fingerprint:
        _integrity("the exact Parquet receipt schema", "native scan schema differs")
