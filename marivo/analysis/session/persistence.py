"""Disk I/O for analysis sessions, jobs, and frames."""

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
    def meta_file(self) -> Path:
        return self.session_dir / "meta.json"


def _atomic_write_text(path: Path, data: str) -> None:
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


def write_session_meta(layout: PersistenceLayout, meta: dict[str, Any]) -> None:
    _atomic_write_text(layout.meta_file, json.dumps(meta, indent=2, sort_keys=True))


def read_session_meta(layout: PersistenceLayout) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(layout.meta_file.read_text()))


def write_job_record(layout: PersistenceLayout, record: dict[str, Any]) -> None:
    _atomic_write_text(
        layout.jobs_dir / f"{record['id']}.json",
        json.dumps(record, indent=2, sort_keys=True),
    )


def read_job_record(layout: PersistenceLayout, job_id: str) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads((layout.jobs_dir / f"{job_id}.json").read_text()))


def list_job_ids(layout: PersistenceLayout) -> list[str]:
    if not layout.jobs_dir.is_dir():
        return []
    return sorted(path.stem for path in layout.jobs_dir.glob("*.json"))


def write_frame_to_disk(layout: PersistenceLayout, frame: BaseFrame) -> BaseFrameMeta:
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
    if isinstance(frame_ref, ArtifactRef):
        frame_ref = frame_ref.id
    frame_dir = layout.frames_dir / frame_ref
    df = pd.read_parquet(frame_dir / "data.parquet", engine="pyarrow", to_pandas_kwargs={})
    meta = json.loads((frame_dir / "meta.json").read_text())
    return df, meta
