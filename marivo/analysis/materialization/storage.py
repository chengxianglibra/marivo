"""Shared checks for immutable project-local terminal receipts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Literal

import pyarrow as pa
import pyarrow.parquet as pq

from marivo._compat import Never
from marivo.analysis.materialization.contracts import FileEntry, LocalReceipt
from marivo.analysis.materialization.errors import (
    IntegrityError,
    MaterializationError,
    StorageAccessError,
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


def payload_digest(root: Path, directory: Path, receipt: LocalReceipt) -> str:
    """Bind an immutable part receipt to all its exact persisted file bytes."""

    digest = hashlib.sha256()
    for entry in receipt.file_manifest:
        path = _checked_path(root, directory / entry.relative_path)
        digest.update(entry.relative_path.encode())
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _manifest_bytes(entries: tuple[FileEntry, ...]) -> bytes:
    return json.dumps(
        [
            {
                "relative_path": entry.relative_path,
                "size_bytes": entry.size_bytes,
            }
            for entry in entries
        ],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


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
    entry = receipt.file_manifest[0]
    if entry.relative_path != "data.parquet":
        _integrity("the exact registered data filename", "invalid manifest entry")
    data = _checked_path(project_root, root / entry.relative_path)
    failure: Literal["missing", "unauthorized", "mutated", "unknown"] = "unknown"
    try:
        parquet = pq.ParquetFile(data)
        return parquet, data
    except IntegrityError:
        raise
    except MaterializationError:
        _integrity("the exact retained logical and physical schema", "invalid primary schema")
    except (OSError, pa.ArrowException) as error:
        if isinstance(error, FileNotFoundError):
            failure = "missing"
        elif isinstance(error, PermissionError):
            failure = "unauthorized"
        elif isinstance(error, pa.ArrowException):
            failure = "mutated"
    raise StorageAccessError(failure)
