"""Exact retained-file receipts and canonical identity payloads."""

from __future__ import annotations

import hashlib
import json
from contextlib import suppress
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

from marivo.analysis.model.execution_errors import IntegrityError


def invalid(received: str) -> IntegrityError:
    return IntegrityError(
        expected="one supported complete canonical v3 metadata value",
        received=received,
        repair="Preserve the selected Artifact and inspect its exact metadata; do not replay its origin.",
        stage="publication",
    )


def canonical_json(value: object) -> str:
    text: str | None = None
    with suppress(TypeError, ValueError, RecursionError):
        text = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
    if text is None:
        raise invalid("non-canonical metadata")
    return text


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _int(value: object, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise invalid("invalid integer")
    return value


def _relative(value: str) -> None:
    path = PurePosixPath(value)
    if (
        not value
        or value == "."
        or path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != value
        or "\\" in value
    ):
        raise invalid("unsafe relative storage path")


@dataclass(frozen=True, slots=True)
class FileEntry:
    relative_path: str
    size_bytes: int

    def __post_init__(self) -> None:
        _relative(self.relative_path)
        _int(self.size_bytes)


def manifest_payload(entries: tuple[FileEntry, ...]) -> list[dict[str, object]]:
    return [
        {"relative_path": item.relative_path, "size_bytes": item.size_bytes} for item in entries
    ]


@dataclass(frozen=True, slots=True)
class LocalReceipt:
    project_relative_path: str
    file_manifest: tuple[FileEntry, ...]
    realized_row_count: int
    realized_byte_count: int
    parquet_contract_version: int = 1

    def __post_init__(self) -> None:
        _relative(self.project_relative_path)
        if type(self.file_manifest) is not tuple or not self.file_manifest:
            raise invalid("empty or mutable file manifest")
        paths = tuple(item.relative_path for item in self.file_manifest)
        if paths != tuple(sorted(set(paths))):
            raise invalid("non-canonical file manifest order")
        _int(self.realized_row_count)
        _int(self.realized_byte_count)
        if self.realized_byte_count < sum(item.size_bytes for item in self.file_manifest):
            raise invalid("receipt bytes smaller than manifest")
        if type(self.parquet_contract_version) is not int or self.parquet_contract_version != 1:
            raise invalid("unsupported Parquet contract")

    @property
    def kind(self) -> Literal["local"]:
        return "local"

    @property
    def format(self) -> Literal["parquet"]:
        return "parquet"

    @property
    def identity_digest(self) -> str:
        return digest(receipt_payload(self))


def receipt_payload(value: LocalReceipt) -> dict[str, object]:
    return {
        "kind": "local",
        "project_relative_path": value.project_relative_path,
        "format": "parquet",
        "parquet_contract_version": value.parquet_contract_version,
        "file_manifest": manifest_payload(value.file_manifest),
        "realized_row_count": value.realized_row_count,
        "realized_byte_count": {"kind": "exact", "byte_count": value.realized_byte_count},
    }
