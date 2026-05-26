"""Session class and session-local summaries."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from marivo.analysis_py.session.persistence import (
    PersistenceLayout,
    list_job_ids,
    read_job_record,
    read_session_meta,
)

SessionState = Literal["active", "archived"]
BackendFactory = Callable[[str], Any]


@dataclass(frozen=True)
class JobSummary:
    id: str
    intent: str
    status: str
    started_at: str
    duration_ms: int
    output_frame_ref: str | None


@dataclass(frozen=True)
class FrameRef:
    ref: str
    kind: str


@dataclass
class Session:
    id: str
    name: str
    question: str | None
    cwd: Path
    project_root: Path
    state: SessionState
    created_at: datetime
    updated_at: datetime
    backend_factory: BackendFactory | None
    layout: PersistenceLayout
    semantic_project: Any  # SemanticProject from marivo.semantic_py
    tz: ZoneInfo = field(default_factory=lambda: ZoneInfo("UTC"))
    default_calendar: str | None = None
    known_calendars: set[str] = field(default_factory=set)
    calendars: Any = None
    known_datasources: set[str] = field(default_factory=set)
    backend_cache: Any = None

    def __post_init__(self) -> None:
        if self.backend_cache is None:
            from marivo.analysis_py.executor.backend import BackendCache

            self.backend_cache = BackendCache(self.backend_factory)
        if self.calendars is None:
            from marivo.analysis_py.calendar.loader import CalendarCache

            self.calendars = CalendarCache(self.project_root)

    @property
    def is_read_only(self) -> bool:
        return self.backend_factory is None

    def jobs(self) -> list[JobSummary]:
        summaries: list[JobSummary] = []
        for job_id in list_job_ids(self.layout):
            record = read_job_record(self.layout, job_id)
            summaries.append(
                JobSummary(
                    id=record["id"],
                    intent=record["intent"],
                    status=record["status"],
                    started_at=record["started_at"],
                    duration_ms=record["duration_ms"],
                    output_frame_ref=record.get("output_frame_ref"),
                )
            )
        summaries.sort(key=lambda item: (item.started_at, item.id))
        return summaries

    def job(self, job_id: str) -> dict[str, Any]:
        return read_job_record(self.layout, job_id)

    def frames(self) -> list[FrameRef]:
        if not self.layout.frames_dir.is_dir():
            return []
        refs: list[FrameRef] = []
        for frame_dir in sorted(self.layout.frames_dir.iterdir()):
            meta_file = frame_dir / "meta.json"
            if meta_file.is_file():
                meta = json.loads(meta_file.read_text())
                refs.append(FrameRef(ref=meta["ref"], kind=meta["kind"]))
        return refs

    def close(self) -> None:
        if self.backend_cache is not None:
            self.backend_cache.close_all()


def ensure_session_writable(session: Session) -> None:
    from marivo.analysis_py.errors import SessionStateError

    state = session.state
    if session.layout.meta_file.is_file():
        state = read_session_meta(session.layout).get("state", state)
    if state == "archived":
        session.state = "archived"
        raise SessionStateError(message=f"session '{session.name}' is archived")
