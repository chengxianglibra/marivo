"""Private execution scope shared by authored model loaders."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from traceback import TracebackException

_SOURCE_LOADING_FILE: ContextVar[Path | None] = ContextVar("_SOURCE_LOADING_FILE", default=None)


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
        action = "Fix the syntax at the reported file and line, then restart Python and reload."
    else:
        frame = exc.__traceback__
        while frame is not None:
            file, line = frame.tb_frame.f_code.co_filename, frame.tb_lineno
            frame = frame.tb_next
        if isinstance(exc, RecursionError):
            action = (
                "Read the original traceback to locate the recursive calls or import chain, "
                "fix that cause, then restart Python and reload."
            )
        else:
            action = (
                "Read the original exception and traceback, fix the reported failure, "
                "then restart Python and reload."
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
