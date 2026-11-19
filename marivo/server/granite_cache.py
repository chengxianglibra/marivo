"""Explicitly configured cache for Granite document vectors."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from zipfile import ZIP_STORED, BadZipFile, ZipFile

import numpy as np
import numpy.typing as npt

if TYPE_CHECKING:
    from collections.abc import Callable

_DIMENSIONS = 384
_DTYPE = np.dtype(np.float32)
_MATRIX_MEMBER = "matrix.npy"
_ROW_KEYS_MEMBER = "row-keys.sha256"
_ROW_KEY_BYTES = hashlib.sha256().digest_size


def fingerprint_model(model_path: Path, *, check_budget: Callable[[], None] | None = None) -> str:
    """Fingerprint both local model assets without loading them into memory."""
    assets = hashlib.sha256()
    for path in (model_path, model_path.with_name("tokenizer.json")):
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                if check_budget is not None:
                    check_budget()
                digest.update(block)
        assets.update(digest.digest())
    return assets.hexdigest()


@dataclass(frozen=True)
class CachedVectors:
    """Ordered document fingerprints and their read-only vectors."""

    row_keys: tuple[bytes, ...]
    matrix: npt.NDArray[np.float32]


@dataclass(frozen=True)
class GraniteCache:
    """Store the latest complete set of document vectors for one project."""

    directory: Path
    project_root: Path

    def key(self, model_fingerprint: str, recipe: str) -> str:
        """Identify the project and model configuration that owns the document vectors."""
        digest = hashlib.sha256(b"granite-project-vectors-v2\0")
        for encoded in (
            os.fsencode(self.project_root.resolve()),
            model_fingerprint.encode("utf-8"),
            recipe.encode("utf-8"),
        ):
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
        return digest.hexdigest()

    def load(self, key: str) -> CachedVectors | None:
        """Read a complete fingerprint and matrix pair with valid CRCs."""
        try:
            with (self.directory / f"{key}.npz").open("rb") as file, ZipFile(file) as cache:
                row_keys_member = cache.getinfo(_ROW_KEYS_MEMBER)
                matrix_member = cache.getinfo(_MATRIX_MEMBER)
                members = (row_keys_member, matrix_member)
                if len(cache.infolist()) != len(members) or any(
                    member.compress_type != ZIP_STORED or member.compress_size != member.file_size
                    for member in members
                ):
                    return None
                if (
                    row_keys_member.file_size % _ROW_KEY_BYTES
                    or sum(member.file_size for member in members) > os.fstat(file.fileno()).st_size
                ):
                    return None
                rows = row_keys_member.file_size // _ROW_KEY_BYTES
                expected_bytes = rows * _DIMENSIONS * _DTYPE.itemsize
                with cache.open(matrix_member) as stream:
                    if np.lib.format.read_magic(stream) != (1, 0):
                        return None
                    shape, fortran_order, dtype = np.lib.format.read_array_header_1_0(stream)
                    if (
                        shape != (rows, _DIMENSIONS)
                        or fortran_order
                        or dtype != _DTYPE
                        or matrix_member.file_size - stream.tell() != expected_bytes
                    ):
                        return None
                    # A final read to EOF checks the member CRC before accepting its bytes.
                    data = stream.read(expected_bytes + 1)
                packed = cache.read(row_keys_member)
        except (OSError, EOFError, KeyError, RuntimeError, TypeError, ValueError, BadZipFile):
            return None
        if len(data) != expected_bytes or len(packed) != rows * _ROW_KEY_BYTES:
            return None
        row_keys = tuple(
            packed[offset : offset + _ROW_KEY_BYTES]
            for offset in range(0, len(packed), _ROW_KEY_BYTES)
        )
        if len(set(row_keys)) != rows:
            return None
        return CachedVectors(
            row_keys, np.frombuffer(data, dtype=np.float32).reshape(rows, _DIMENSIONS)
        )

    def store(self, key: str, row_keys: tuple[bytes, ...], matrix: npt.NDArray[np.float32]) -> None:
        """Publish a complete fingerprint and matrix pair with atomic replacement."""
        if any(
            not isinstance(row_key, bytes) or len(row_key) != _ROW_KEY_BYTES for row_key in row_keys
        ) or len(set(row_keys)) != len(row_keys):
            raise ValueError("Expected a unique 32-byte fingerprint for each Granite vector.")
        if (
            matrix.shape != (len(row_keys), _DIMENSIONS)
            or matrix.dtype != _DTYPE
            or not matrix.flags.c_contiguous
        ):
            raise ValueError(
                "Expected one contiguous float32 Granite row per fingerprint with 384 columns."
            )
        self.directory.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".granite-", dir=self.directory)
        try:
            with os.fdopen(descriptor, "wb") as file:
                with ZipFile(file, "w") as cache:
                    cache.writestr(_ROW_KEYS_MEMBER, b"".join(row_keys))
                    with cache.open(_MATRIX_MEMBER, "w") as stream:
                        np.lib.format.write_array(
                            stream, matrix, version=(1, 0), allow_pickle=False
                        )
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.directory / f"{key}.npz")
        finally:
            Path(temporary).unlink(missing_ok=True)
