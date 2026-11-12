"""Project-bound immutable result files and atomic publication."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, TypeAlias

from marivo.analysis.errors import AnalysisError, AnalysisRepair, RepairKind
from marivo.introspection.live.model import LiveHelpTarget

ResultErrorCode: TypeAlias = Literal[
    "invalid_parameter",
    "unencodable_value",
    "result_not_found",
    "invalid_result_reference",
    "corrupt_dataframe",
    "result_publication_failed",
    "result_read_failed",
]


class ResultStorageError(AnalysisError):
    """A fixed result cannot be encoded, published or read."""

    def __init__(
        self,
        code: ResultErrorCode,
        location: str,
        received: str,
        repair: str,
        *,
        target: Literal["persist_dataframe", "read_dataframe"] = "persist_dataframe",
        expected: str = "a supported complete result in the bound project",
        repair_kind: RepairKind | None = None,
    ) -> None:
        self.code = code
        super().__init__(
            message=f"Persisted result: {received}.",
            expected=expected,
            received=received,
            location=location,
            repair=AnalysisRepair(
                kind=repair_kind
                or (
                    "retry"
                    if code
                    in ("invalid_parameter", "unencodable_value", "invalid_result_reference")
                    else "environment"
                ),
                action=repair,
                help_target=LiveHelpTarget(surface="analysis", canonical_id=target),
            ),
        )


def state_path(
    root: Path,
    relative: str,
    *,
    target: Literal["persist_dataframe", "read_dataframe"] = "persist_dataframe",
) -> Path:
    path = root / ".marivo" / "server" / relative
    if any(item.is_symlink() for item in (path, *path.parents) if item != root.parent):
        raise ResultStorageError(
            "invalid_result_reference",
            "state",
            "indirect filesystem path",
            "Keep result state in the bound project's local directory.",
            target=target,
            repair_kind="environment",
        )
    return path


@contextmanager
def publication_file(path: Path) -> Iterator[Path]:
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, name = tempfile.mkstemp(prefix=".publishing-", dir=path.parent)
        os.close(descriptor)
        temporary = Path(name)
        yield temporary
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        raise ResultStorageError(
            "result_publication_failed",
            "state",
            "publication failed",
            "Check storage permissions and capacity before explicitly saving again.",
        ) from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
