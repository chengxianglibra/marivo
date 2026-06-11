"""File-system layout and byte I/O for analysis sessions.

Separates path/directory structure from metadata persistence (which now
belongs to the SQLite store in ``_store.py``).  This module owns:

- ``PersistenceLayout`` — directory paths for a session.
- ``_atomic_write_text`` — safe text writes via temp + rename.
- ``write_job_record`` / ``read_job_record`` / ``list_job_ids`` — job JSON I/O.
- ``write_frame_to_disk`` / ``read_frame_from_disk`` — frame parquet + meta.json I/O.
- ``report_dir`` — safe report directory resolution.

This module does **not** expose ``read_session_meta`` or
``write_session_meta``; session metadata lives in the SQLite store.
"""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pandas as pd

from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta
from marivo.analysis.refs import ArtifactRef


@dataclass(frozen=True)
class PersistenceLayout:
    """Directory layout for a single analysis session.

    Args:
        project_root: The project root directory (contains ``.marivo/``).
        session_id: The session identifier (``sess_...``).

    Example:
        >>> layout = PersistenceLayout(project_root=Path("/my/project"), session_id="sess_abc")
        >>> layout.session_dir
        PosixPath('/my/project/.marivo/analysis/sessions/sess_abc')
    """

    project_root: Path
    session_id: str

    @property
    def analysis_dir(self) -> Path:
        return Path(self.project_root) / ".marivo" / "analysis"

    @property
    def sessions_dir(self) -> Path:
        return self.analysis_dir / "sessions"

    @property
    def session_dir(self) -> Path:
        return self.sessions_dir / self.session_id

    @property
    def jobs_dir(self) -> Path:
        return self.session_dir / "jobs"

    @property
    def frames_dir(self) -> Path:
        return self.session_dir / "frames"

    @property
    def scripts_dir(self) -> Path:
        return self.session_dir / "scripts"

    @property
    def reports_dir(self) -> Path:
        return self.session_dir / "reports"

    @property
    def store_db(self) -> Path:
        """Path to the SQLite session store database."""
        return self.analysis_dir / "session_store.db"

    def relative_path(self, absolute_path: Path) -> str:
        """Convert an absolute path under project_root to a project-relative string.

        The store records paths relative to ``project_root`` so that the
        database remains valid if the project directory is moved.

        Args:
            absolute_path: An absolute path that is a descendant of
                ``project_root``.

        Returns:
            A POSIX-style relative path string (using ``/`` as separator).

        Raises:
            ValueError: If *absolute_path* is not under ``project_root``.
        """
        try:
            return str(absolute_path.relative_to(self.project_root))
        except ValueError:
            raise ValueError(
                f"path {absolute_path!s} is not under project root {self.project_root!s}"
            ) from None


def _atomic_write_text(path: Path, data: str) -> None:
    """Write *data* to *path* atomically via temp file + rename.

    Creates parent directories as needed.  On failure the temp file is
    cleaned up and the original file (if any) is left untouched.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_", suffix=path.name)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def write_job_record(layout: PersistenceLayout, record: dict[str, Any]) -> None:
    """Persist a job record as a JSON file in the session jobs directory.

    Args:
        layout: Session layout providing directory paths.
        record: Job record dict; must contain an ``"id"`` key.
    """
    _atomic_write_text(
        layout.jobs_dir / f"{record['id']}.json",
        json.dumps(record, indent=2, sort_keys=True),
    )


def read_job_record(layout: PersistenceLayout, job_id: str) -> dict[str, Any]:
    """Read a job record JSON file from the session jobs directory.

    Args:
        layout: Session layout providing directory paths.
        job_id: The job identifier (used as the filename stem).

    Returns:
        The parsed job record dict.
    """
    return cast("dict[str, Any]", json.loads((layout.jobs_dir / f"{job_id}.json").read_text()))


def list_job_ids(layout: PersistenceLayout) -> list[str]:
    """Return sorted job ids for the session.

    Args:
        layout: Session layout providing directory paths.

    Returns:
        Sorted list of job id strings, or an empty list if the jobs
        directory does not exist.
    """
    if not layout.jobs_dir.is_dir():
        return []
    return sorted(path.stem for path in layout.jobs_dir.glob("*.json"))


def write_frame_to_disk(layout: PersistenceLayout, frame: BaseFrame) -> BaseFrameMeta:
    """Write a frame's data and metadata to the session frames directory.

    Writes a ``data.parquet`` file and a ``meta.json`` sidecar.  The
    parquet write uses a temp + rename to avoid partial writes.

    Args:
        layout: Session layout providing directory paths.
        frame: The frame to persist.

    Returns:
        Updated ``BaseFrameMeta`` with the on-disk ``byte_size`` populated.
    """
    frame_dir = layout.frames_dir / frame.meta.ref
    frame_dir.mkdir(parents=True, exist_ok=True)

    data_path = frame_dir / "data.parquet"
    tmp_parquet = frame_dir / f".tmp_data_{frame.meta.ref}.parquet"
    frame._df.to_parquet(tmp_parquet, engine="pyarrow", compression="snappy", index=False)
    os.replace(tmp_parquet, data_path)

    updated = frame.meta.model_copy(update={"byte_size": data_path.stat().st_size})
    _atomic_write_text(frame_dir / "meta.json", updated.model_dump_json(indent=2))
    return updated


def read_frame_from_disk(
    layout: PersistenceLayout,
    frame_ref: str | ArtifactRef,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Read a frame's data and metadata from the session frames directory.

    Args:
        layout: Session layout providing directory paths.
        frame_ref: Frame identifier (string or ``ArtifactRef``).

    Returns:
        A ``(DataFrame, meta_dict)`` tuple.
    """
    if isinstance(frame_ref, ArtifactRef):
        frame_ref = frame_ref.id
    frame_dir = layout.frames_dir / frame_ref
    df = pd.read_parquet(frame_dir / "data.parquet", engine="pyarrow", to_pandas_kwargs={})
    meta = json.loads((frame_dir / "meta.json").read_text())
    return df, meta


def report_dir(layout: PersistenceLayout, report_id: str) -> Path:
    """Return the directory path for a session report.

    Validates that *report_id* is safe to use as a directory name: it
    must be non-empty, not ``"."`` or ``".."``, and contain no path
    separators.

    Args:
        layout: Session layout providing directory paths.
        report_id: Report identifier to use as the directory name.

    Returns:
        The absolute path to the report directory.

    Raises:
        ValueError: If *report_id* is not safe for use as a directory name.
    """
    if not report_id or report_id in {".", ".."} or "/" in report_id or "\\" in report_id:
        raise ValueError(f"report_id is not safe for a session report directory: {report_id!r}")
    return layout.reports_dir / report_id
