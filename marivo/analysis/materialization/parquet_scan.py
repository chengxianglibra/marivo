"""Fixed native scans of immutable Parquet, with no durable database output."""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

import ibis.expr.types as ir

from marivo.analysis.datasets.descriptors import DatasetRowContract
from marivo.analysis.materialization import contracts as codec
from marivo.analysis.materialization.contracts import LocalReceipt, StorageReceipt
from marivo.analysis.materialization.errors import StorageAccessError
from marivo.analysis.materialization.execution import ExecutionAdapter
from marivo.analysis.materialization.storage import (
    _hash_file,
    _integrity,
    _open_payload,
    _realized_schema,
)


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
    verify_schema: bool = False,
) -> ir.Table:
    """Use the preselected native adapter for immutable local Parquet."""
    name = "mv_parquet_" + uuid4().hex
    path = checked_local_path(root, receipt, verify_schema=verify_schema)
    return backend.read_parquet(str(path), table_name=name)


def validate_parquet_relation(
    backend: ExecutionAdapter,
    table: ir.Table,
    receipt: StorageReceipt,
    row: DatasetRowContract,
) -> None:
    if (
        backend.read_scalar(backend.prepare(table.count(), role="parquet_check.input_count"))
        != receipt.realized_row_count
    ):
        _integrity("the exact Parquet receipt row count", "native scan count differs")
    realized = _realized_schema(
        row,
        backend.read_table(
            backend.prepare(table.limit(0), role="parquet_check.input_schema")
        ).schema,
    )
    if codec.schema_fingerprint(realized) != receipt.schema_fingerprint:
        _integrity("the exact Parquet receipt schema", "native scan schema differs")
