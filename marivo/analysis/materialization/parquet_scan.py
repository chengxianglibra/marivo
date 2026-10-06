"""Immutable local Parquet receipt validation without a query backend."""

from __future__ import annotations

import hashlib
from pathlib import Path

from marivo.analysis.materialization.contracts import LocalReceipt
from marivo.analysis.materialization.errors import StorageAccessError
from marivo.analysis.materialization.storage import (
    _hash_file,
    _integrity,
    _open_payload,
)


def checked_local_path(root: Path, receipt: LocalReceipt, *, verify_schema: bool = False) -> Path:
    """Check manifest, size, schema, count and complete immutable bytes."""
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
