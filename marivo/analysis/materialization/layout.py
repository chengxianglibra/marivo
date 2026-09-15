"""Generation-scoped paths; files contain data, never metadata authority."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath


@dataclass(frozen=True, slots=True)
class MaterializationLayout:
    project_root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_root", self.project_root.resolve())

    @property
    def generation_dir(self) -> Path:
        return self.project_root / ".marivo" / "analysis" / "generations" / "v5"

    @property
    def store_db(self) -> Path:
        return self.generation_dir / "session_store.db"

    def session_dir(self, session_ref: str) -> Path:
        return self.generation_dir / "sessions" / _component(session_ref)

    def lock_path(self, session_ref: str) -> Path:
        return self.session_dir(session_ref) / "session.lock"

    def run_dir(self, session_ref: str, run_ref: str) -> Path:
        return self.session_dir(session_ref) / "runs" / _component(run_ref)

    def artifact_dir(self, session_ref: str, artifact_ref: str) -> Path:
        return self.session_dir(session_ref) / "artifacts" / _component(artifact_ref)

    def relative_path(self, path: Path) -> str:
        return path.resolve().relative_to(self.project_root).as_posix()


def _component(value: str) -> str:
    if not value or PurePosixPath(value).parts != (value,) or value in (".", ".."):
        raise ValueError("expected one non-empty path component")
    return value
