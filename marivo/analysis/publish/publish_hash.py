"""Deterministic content hashing for report packages."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path

_DEFAULT_EXCLUDE: frozenset[str] = frozenset({"manifest.json"})


def _package_files(package_root: Path, exclude: frozenset[str]) -> list[Path]:
    return sorted(
        path
        for path in package_root.rglob("*")
        if path.is_file() and path.relative_to(package_root).as_posix() not in exclude
    )


def compute_package_hash(
    package_dir: str | Path,
    *,
    exclude: Iterable[str] = _DEFAULT_EXCLUDE,
) -> str:
    """Return a deterministic ``sha256:`` hash over package file paths and bytes.

    Paths are sorted, separators normalized to ``/``, and each entry contributes
    its relative path, byte length, and raw bytes so concatenation is
    unambiguous. ``manifest.json`` is excluded by default because it embeds the
    hash itself and publish-time timestamps.
    """
    package_root = Path(package_dir)
    exclude_set = frozenset(exclude)
    digest = hashlib.sha256()
    for path in _package_files(package_root, exclude_set):
        rel = path.relative_to(package_root).as_posix()
        data = path.read_bytes()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return f"sha256:{digest.hexdigest()}"
