"""Private execution scope shared by authored model loaders."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from traceback import TracebackException
from typing import Literal
from uuid import uuid4

_SOURCE_LOADING_FILE: ContextVar[Path | None] = ContextVar("_SOURCE_LOADING_FILE", default=None)


def _new_module_prefix(kind: Literal["datasource", "semantic"]) -> str:
    """Own an independent import namespace, retained until process exit."""
    return f"_marivo_{kind}_{uuid4().hex}"


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
