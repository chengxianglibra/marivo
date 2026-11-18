"""Private execution scope shared by authored model loaders."""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from importlib import util as importlib_util
from importlib.abc import MetaPathFinder
from importlib.machinery import ModuleSpec, PathFinder, SourceFileLoader
from pathlib import Path
from threading import Lock
from traceback import TracebackException
from types import CodeType, ModuleType
from typing import Literal
from uuid import uuid4

_SOURCE_LOADING_FILE: ContextVar[Path | None] = ContextVar("_SOURCE_LOADING_FILE", default=None)
_IMPORT_FINDERS_LOCK = Lock()
_SourceCapture = Callable[[str, bytes], None]


def _new_module_prefix(kind: Literal["datasource", "semantic"]) -> str:
    """Own an independent import namespace, retained until process exit."""
    return f"_marivo_{kind}_{uuid4().hex}"


class _AuthoredSourceLoader(SourceFileLoader):
    def __init__(self, name: str, path: str, capture: _SourceCapture | None) -> None:
        super().__init__(name, path)
        self._capture = capture

    def get_code(self, fullname: str) -> CodeType:
        # Keep the load-local cache out of the loader retained by sys.modules.
        capture, self._capture = self._capture, None
        path = self.get_filename(fullname)
        source = self.get_data(path)
        if capture is not None:
            capture(path, source)
        return self.source_to_code(source, path)


def _authored_file_spec(
    name: str, path: Path, capture: _SourceCapture | None = None
) -> ModuleSpec | None:
    return _use_authored_source(importlib_util.spec_from_file_location(name, path), capture)


def _use_authored_source(
    spec: ModuleSpec | None, capture: _SourceCapture | None
) -> ModuleSpec | None:
    if spec is not None and isinstance(spec.loader, SourceFileLoader):
        spec.loader = _AuthoredSourceLoader(spec.name, spec.loader.path, capture)
    return spec


class _AuthoredSourceFinder(MetaPathFinder):
    def __init__(self, prefixes: Sequence[str], capture: _SourceCapture | None) -> None:
        self._prefixes = tuple(f"{prefix}." for prefix in prefixes)
        self._capture = capture

    def find_spec(
        self, fullname: str, path: Sequence[str] | None, target: ModuleType | None = None
    ) -> ModuleSpec | None:
        if not fullname.startswith(self._prefixes):
            return None
        return _use_authored_source(PathFinder.find_spec(fullname, path, target), self._capture)


@contextmanager
def _authored_imports(
    prefixes: Sequence[str], capture: _SourceCapture | None = None
) -> Iterator[None]:
    finder = _AuthoredSourceFinder(prefixes, capture)
    # Publish a new list so concurrent imports can finish iterating theirs.
    with _IMPORT_FINDERS_LOCK:
        sys.meta_path = [finder, *sys.meta_path]
    try:
        yield
    finally:
        with _IMPORT_FINDERS_LOCK:
            sys.meta_path = [entry for entry in sys.meta_path if entry is not finder]


def _exception_chain_key(exc: BaseException | None) -> tuple[tuple[str, str, str, int], ...]:
    """Identify causes without caller/import frames that vary across load paths."""
    result: list[tuple[str, str, str, int]] = []
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        frame = exc.__traceback__
        file, line = "", 0
        while frame is not None:
            file, line = frame.tb_frame.f_code.co_filename, frame.tb_lineno
            frame = frame.tb_next
        result.append((type(exc).__qualname__, str(exc), file, line))
        exc = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
    return tuple(result)


@dataclass(frozen=True)
class _ExecutionDiagnostic:
    exception_type: str
    traceback: str
    file: str
    line: int
    action: str


def _execution_diagnostic(exc: Exception, filepath: Path) -> _ExecutionDiagnostic:
    file, line = str(filepath), 0
    if isinstance(exc, SyntaxError):
        file, line = exc.filename or file, exc.lineno or 0
        action = "Fix the syntax at the reported file and line, then reload the project."
    else:
        frame = exc.__traceback__
        authored: tuple[str, int] | None = None
        while frame is not None:
            file, line = frame.tb_frame.f_code.co_filename, frame.tb_lineno
            if Path(file).is_absolute() and Path(file).is_relative_to(filepath.parent):
                authored = file, line
            frame = frame.tb_next
        if authored is not None:
            file, line = authored
        if isinstance(exc, RecursionError):
            action = (
                "Read the original traceback to locate the recursive calls or import chain, "
                "fix that cause, then reload the project."
            )
        else:
            action = (
                "Fix the reported failure at the declaration call site, then reload the project."
            )
    return _ExecutionDiagnostic(
        exception_type=type(exc).__name__,
        traceback="".join(TracebackException.from_exception(exc, capture_locals=False).format()),
        file=file,
        line=line,
        action=action,
    )


def _current_source_loading_file() -> Path | None:
    return _SOURCE_LOADING_FILE.get()


@contextmanager
def _source_loading(filepath: Path) -> Iterator[None]:
    token = _SOURCE_LOADING_FILE.set(filepath)
    try:
        yield
    finally:
        _SOURCE_LOADING_FILE.reset(token)
