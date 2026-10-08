"""Private execution scope shared by authored model loaders."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

_SOURCE_LOADING_FILE: ContextVar[Path | None] = ContextVar("_SOURCE_LOADING_FILE", default=None)


def _current_source_loading_file() -> Path | None:
    return _SOURCE_LOADING_FILE.get()


@contextmanager
def _source_loading(filepath: Path) -> Iterator[None]:
    token = _SOURCE_LOADING_FILE.set(filepath)
    try:
        yield
    finally:
        _SOURCE_LOADING_FILE.reset(token)
