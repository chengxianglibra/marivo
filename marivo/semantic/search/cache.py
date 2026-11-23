"""Deterministic identities and atomic cache publication for search."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _atomic_json(path: Path, value: object, *, replace_existing: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".building-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        if replace_existing:
            os.replace(temporary, path)
        else:
            with suppress(FileExistsError):
                # A complete result is immutable once published. Concurrent
                # first queries reuse the winner rather than replacing cursors.
                os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _read_json(path: Path) -> object:
    with path.open(encoding="utf-8") as stream:
        value: object = json.load(stream)
    return value
