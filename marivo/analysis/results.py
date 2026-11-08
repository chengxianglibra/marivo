"""Explicit persistence of plain pandas results."""

from pathlib import Path

import pandas as pd

from marivo.analysis.core.result_reference import DataFrameReference
from marivo.analysis.materialization.dataframe_store import publish
from marivo.analysis.materialization.result_files import ResultStorageError
from marivo.project import resolve_project_root


def persist_dataframe(
    frame: pd.DataFrame, *, project_root: str | Path | None = None
) -> DataFrameReference:
    """Save one immutable plain-table snapshot in the bound project.

    Args:
        frame: A pandas table with unique nonempty string columns, a default
            unnamed RangeIndex and supported scalar values.
        project_root: Exact existing destination directory; omission uses the
            project environment/current-directory discovery rules.
    Returns:
        A fixed DataFrameReference for the saved result.
    Example:
        >>> import marivo.analysis as mv
        >>> reference = mv.persist_dataframe(table)  # doctest: +SKIP
        >>> reference.to_dict()  # doctest: +SKIP
    Constraints:
        Explicit I/O; creates no Session, Analysis Store or source connection.
        Each call saves a new version. Does not save attrs or infer semantics.
        No HTTP server or optional API dependency is required.
    """
    root = resolve_project_root() if project_root is None else Path(project_root).resolve()
    if not root.is_dir():
        raise ResultStorageError(
            "invalid_parameter",
            "project_root",
            "directory does not exist",
            "Pass an existing project directory.",
        )
    if not isinstance(frame, pd.DataFrame):
        raise ResultStorageError(
            "invalid_parameter", "frame", type(frame).__name__, "Pass a pandas DataFrame."
        )
    return publish(root, frame)
