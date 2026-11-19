"""Integrity and reuse of immutable Granite snapshots."""

from __future__ import annotations

import os
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pytest

from marivo.server.granite_cache import GraniteCache, fingerprint_model


def _row_key(text: str) -> bytes:
    return sha256(text.encode("utf-8")).digest()


def test_snapshot_reopens_bit_identically_in_another_process(tmp_path: Path) -> None:
    cache = GraniteCache(tmp_path / "opt-in", tmp_path / "project")
    key = cache.key("model-fingerprint", "recipe")
    first, second = "id: entity:first\nPrivate first definition", "id: entity:second"
    row_keys = (_row_key(first), _row_key(second))
    matrix = np.zeros((2, 384), dtype=np.float32)
    matrix[0, :4] = (0.125, -0.25, 0.5, -0.0)
    matrix[1, :4] = (1.0, 0.25, -0.75, -1.0)
    assert cache.load(key) is None
    assert not cache.directory.exists()
    cache.store(key, row_keys, matrix)
    archive = (cache.directory / f"{key}.npz").read_bytes()
    assert first.encode() not in archive and second.encode() not in archive

    read = subprocess.check_output(
        [
            sys.executable,
            "-c",
            """
import sys
from pathlib import Path

from marivo.server.granite_cache import GraniteCache

cache = GraniteCache(Path(sys.argv[1]), Path(sys.argv[2]))
vectors = cache.load(cache.key("model-fingerprint", "recipe"))
assert vectors is not None
assert not vectors.matrix.flags.writeable and vectors.matrix.flags.c_contiguous
sys.stdout.buffer.write(b"".join(vectors.row_keys) + vectors.matrix.tobytes())
""",
            str(cache.directory),
            str(cache.project_root),
        ]
    )
    assert read == b"".join(row_keys) + matrix.tobytes()


def test_cache_identity_tracks_project_both_assets_and_recipe(tmp_path: Path) -> None:
    model = tmp_path / "model.ort"
    tokenizer = tmp_path / "tokenizer.json"
    model.write_bytes(b"model-A")
    tokenizer.write_bytes(b"tokenizer-A")
    cache = GraniteCache(tmp_path / "opt-in", tmp_path / "first-project")
    other = GraniteCache(cache.directory, tmp_path / "second-project")
    fingerprint = fingerprint_model(model)
    key = cache.key(fingerprint, "recipe-A")
    cache.store(key, (_row_key("document"),), np.zeros((1, 384), dtype=np.float32))

    assert other.load(other.key(fingerprint, "recipe-A")) is None
    assert cache.load(cache.key(fingerprint, "recipe-B")) is None
    model.write_bytes(b"model-B")
    assert cache.load(cache.key(fingerprint_model(model), "recipe-A")) is None
    model.write_bytes(b"model-A")
    tokenizer.write_bytes(b"tokenizer-B")
    assert cache.load(cache.key(fingerprint_model(model), "recipe-A")) is None
    assert cache.load(key) is not None


def test_legacy_matrix_only_archive_is_a_cache_miss(tmp_path: Path) -> None:
    cache = GraniteCache(tmp_path, tmp_path / "project")
    key = cache.key("model-fingerprint", "recipe")
    np.savez(tmp_path / f"{key}.npz", matrix=np.zeros((1, 384), dtype=np.float32))
    assert cache.load(key) is None


@pytest.mark.parametrize(
    ("packed", "rows", "dtype"),
    [
        (b"x" * 31, 1, np.float32),
        (_row_key("duplicate") * 2, 2, np.float32),
        (_row_key("first") + _row_key("second"), 1, np.float32),
        (_row_key("wrong dtype"), 1, np.float64),
    ],
    ids=["short-fingerprint", "duplicate-fingerprint", "wrong-row-count", "wrong-dtype"],
)
def test_malformed_archive_pair_is_a_cache_miss(
    tmp_path: Path, packed: bytes, rows: int, dtype: type[np.float32] | type[np.float64]
) -> None:
    cache = GraniteCache(tmp_path, tmp_path / "project")
    key = cache.key("model-fingerprint", "recipe")
    with ZipFile(tmp_path / f"{key}.npz", "w") as archive:
        archive.writestr("row-keys.sha256", packed)
        with archive.open("matrix.npy", "w") as stream:
            np.save(stream, np.zeros((rows, 384), dtype=dtype), allow_pickle=False)
    assert cache.load(key) is None


@pytest.mark.parametrize("member", ["row-keys", "matrix"])
def test_corrupt_member_is_a_cache_miss_then_can_be_replaced(tmp_path: Path, member: str) -> None:
    cache = GraniteCache(tmp_path, tmp_path / "project")
    key = cache.key("model-fingerprint", "recipe")
    path = tmp_path / f"{key}.npz"
    row_keys = (_row_key("document"),)
    matrix = np.arange(384, dtype=np.float32).reshape(1, 384)
    cache.store(key, row_keys, matrix)
    archive = bytearray(path.read_bytes())
    member_bytes = row_keys[0] if member == "row-keys" else matrix.tobytes()
    offset = archive.index(member_bytes)
    archive[offset + 20] ^= 1
    path.write_bytes(archive)
    assert cache.load(key) is None
    cache.store(key, row_keys, matrix)
    loaded = cache.load(key)
    assert loaded is not None
    assert loaded.row_keys == row_keys
    assert loaded.matrix.tobytes() == matrix.tobytes()


def test_failed_atomic_replacement_leaves_old_pair_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = GraniteCache(tmp_path, tmp_path / "project")
    key = cache.key("model-fingerprint", "recipe")
    original_keys, updated_keys = (_row_key("original"),), (_row_key("update"),)
    original = np.zeros((1, 384), dtype=np.float32)
    updated = np.ones((1, 384), dtype=np.float32)
    cache.store(key, original_keys, original)

    def fail_replace(_temporary: str, _target: Path) -> None:
        raise OSError("simulated replacement failure")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_replace)
        with pytest.raises(OSError, match="simulated replacement failure"):
            cache.store(key, updated_keys, updated)
    loaded = cache.load(key)
    assert loaded is not None
    assert loaded.row_keys == original_keys and loaded.matrix.tobytes() == original.tobytes()

    cache.store(key, updated_keys, updated)
    loaded = cache.load(key)
    assert loaded is not None
    assert loaded.row_keys == updated_keys and loaded.matrix.tobytes() == updated.tobytes()
    assert list(tmp_path.iterdir()) == [tmp_path / f"{key}.npz"]
