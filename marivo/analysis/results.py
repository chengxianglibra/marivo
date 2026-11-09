"""Explicit persistence and source-free reading of plain pandas results."""

import re
from pathlib import Path

import pandas as pd

from marivo.analysis.core.dataframe_result import DataFrameResult
from marivo.analysis.core.result_reference import DataFrameReference, DataFrameReferenceWire
from marivo.analysis.materialization.dataframe_store import publish, read
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


def read_dataframe(
    reference: DataFrameReference | DataFrameReferenceWire,
) -> DataFrameResult:
    """Read a complete immutable pandas snapshot using its exact saved reference.

    Args:
        reference: DataFrameReference or its three-field to_dict() wire dictionary,
            including schema marivo.dataframe_ref/v2, the captured absolute
            project_root and a saved result_id.
    Returns:
        A detached DataFrameResult with reference, UTC commit time, logical row
        count and ordered Arrow schema. Call to_pandas() for isolated fixed values.
    Example:
        >>> import marivo.analysis as mv
        >>> reference = mv.persist_dataframe(table)  # doctest: +SKIP
        >>> result = mv.read_dataframe(reference.to_dict())  # doctest: +SKIP
        >>> copy = result.to_pandas()  # doctest: +SKIP
    Constraints:
        Reads the complete saved result or raises AnalysisError. No Session,
        Catalog, source reads, HTTP server, state creation or producer replay.
        Uses only the captured local project path; current directory and project
        discovery do not affect reads. The saved location must be accessible on
        this filesystem. Bare IDs, paths, JSON strings, older reference schemas
        and AnalysisFrame references fail. No location override or producer replay.
        No pagination or HTTP response limits; memory grows with the full result.
    """
    result_id: object = None
    project_root: object = None
    received = f"reference type {type(reference).__name__}"
    if isinstance(reference, DataFrameReference):
        result_id = reference.result_id
        project_root = reference.project_root
        received = f"reference.result_id={result_id!r}"
    elif isinstance(reference, dict):
        if set(reference) != {"schema", "project_root", "result_id"}:
            received = f"reference fields {tuple(reference)!r}"
        elif reference.get("schema") != "marivo.dataframe_ref/v2":
            received = f"reference.schema={reference.get('schema')!r}"
        else:
            result_id = reference.get("result_id")
            project_root = reference.get("project_root")
            received = f"reference.result_id={result_id!r}"
    if not isinstance(result_id, str) or re.fullmatch(r"df_[0-9a-f]{32}", result_id) is None:
        raise ResultStorageError(
            "invalid_result_reference",
            "reference",
            received[:256],
            "Pass the saved DataFrameReference or its original three-field to_dict() dictionary.",
            target="read_dataframe",
            expected="a marivo.dataframe_ref/v2 reference with project_root and a df_ followed by 32 lowercase hex digits",
        )
    if (
        not isinstance(project_root, (str, Path))
        or (isinstance(reference, dict) and not isinstance(project_root, str))
        or not str(project_root)
        or "\x00" in str(project_root)
        or not Path(project_root).is_absolute()
        or ".." in Path(project_root).parts
    ):
        raise ResultStorageError(
            "invalid_result_reference",
            "reference.project_root",
            repr(project_root)[:256],
            "Use the original reference with the absolute project directory captured when saving.",
            target="read_dataframe",
            expected="an absolute local project path without parent traversal",
        )
    try:
        root = Path(project_root)
        if not root.is_dir():
            raise ResultStorageError(
                "result_not_found",
                "reference.project_root",
                f"{str(root)!r} is not an existing directory",
                "Restore the saved project at its captured location, or use the correct complete reference.",
                target="read_dataframe",
                expected="an existing project directory",
            )
    except (OSError, RuntimeError, ValueError) as error:
        raise ResultStorageError(
            "result_read_failed",
            "reference.project_root",
            f"captured project access failed ({type(error).__name__})",
            "Check the captured project path and read permissions, then explicitly read again.",
            target="read_dataframe",
        ) from None
    return read(root, DataFrameReference._create(root, result_id))
