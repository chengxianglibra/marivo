"""Publish targets for report packages.

``PublishTarget`` is the seam a later phase fills with an S3 target. The path
traversal guard mirrors the package-id safety check in ``report_package.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class PublishTarget(Protocol):
    """A write-only, content-addressed-by-relative-path publish destination."""

    def uri(self, rel_path: str) -> str: ...

    def exists(self, rel_path: str) -> bool: ...

    def put_file(self, rel_path: str, data: bytes) -> None: ...


class LocalFilesystemTarget:
    """A publish target that writes package files under a local base directory."""

    def __init__(self, base_dir: str | Path) -> None:
        self._base = Path(base_dir)

    def _resolve(self, rel_path: str) -> Path:
        path = (self._base / rel_path).resolve()
        base = self._base.resolve()
        if base != path and base not in path.parents:
            raise ValueError(f"publish path escapes target base: {rel_path!r}")
        return path

    def uri(self, rel_path: str) -> str:
        return self._resolve(rel_path).as_uri()

    def exists(self, rel_path: str) -> bool:
        return self._resolve(rel_path).is_file()

    def put_file(self, rel_path: str, data: bytes) -> None:
        path = self._resolve(rel_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
