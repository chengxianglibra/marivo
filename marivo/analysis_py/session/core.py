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
    judgment_store: Any = None
    judgment_store_unavailable: bool = False

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
        if self.judgment_store is not None:
            self.judgment_store.close()
            self.judgment_store = None
        if self.backend_cache is not None:
            self.backend_cache.close_all()

    def evidence_store(self) -> Any:
        """Return the lazily-opened JudgmentStore, or None if unavailable."""
        if self.judgment_store is not None:
            return self.judgment_store
        if self.judgment_store_unavailable:
            return None
        from marivo.analysis_py.errors import EvidenceStoreUnavailableError
        from marivo.analysis_py.evidence.store import open_judgment_store, run_startup_gc

        db_path = self.layout.session_dir / "judgment.db"
        try:
            store = open_judgment_store(db_path)
        except EvidenceStoreUnavailableError:
            self.judgment_store_unavailable = True
            return None
        run_startup_gc(store, self.layout.frames_dir)
        self.judgment_store = store
        return store

    def knowledge(self) -> Any:
        """Return a SessionKnowledge projection for this session."""
        from marivo.analysis_py.evidence.knowledge import build_session_knowledge

        db_path = self.layout.session_dir / "judgment.db"
        if not db_path.exists():
            from datetime import UTC
            from datetime import datetime as _dt

            from marivo.analysis_py.evidence.knowledge import SessionKnowledge

            now = _dt.now(UTC)
            return SessionKnowledge(
                session_id=self.id,
                snapshot_id=f"snap_{self.id}_{int(now.timestamp() * 1_000_000)}",
                snapshot_at=now,
                evidence_completeness="unavailable",
            )
        return build_session_knowledge(db_path=db_path, session_id=self.id)

    def run_followup(self, action: Any) -> Any:
        """Dispatch a FollowupAction to the appropriate operator."""
        from marivo.analysis_py.followups import FollowupAction

        if not isinstance(action, FollowupAction):
            raise TypeError(
                f"run_followup expected FollowupAction, got {type(action).__name__}"
            )

        if action.operator == "assess_quality":
            from marivo.analysis_py.intents.assess_quality import assess_quality
            from marivo.analysis_py.session._load import load_frame

            source_ref = action.input_refs[0]
            source_frame = load_frame(source_ref, session=self)
            try:
                result = assess_quality(source_frame, session=self)
            finally:
                self._mark_followup_executed(
                    action_id=action.action_id,
                    executed_step_id="step_assess_quality",
                )
            return result

        if action.operator == "compare":
            raise NotImplementedError(
                "run_followup(compare) requires another MetricFrame; agent must dispatch"
            )
        if action.operator == "observe":
            raise NotImplementedError(
                "run_followup(observe) requires explicit MetricRef; agent must dispatch"
            )

        raise NotImplementedError(
            f"run_followup is not wired for operator={action.operator!r} in this slice"
        )

    def _mark_followup_executed(
        self, *, action_id: str, executed_step_id: str
    ) -> None:
        """Mark a followup action as executed in the judgment store."""
        store = self.evidence_store()
        if store is None:
            return
        with store.transaction() as tx:
            tx.execute(
                "UPDATE followups SET executed_step_id=? WHERE followup_id=?",
                (executed_step_id, action_id),
            )


def ensure_session_writable(session: Session) -> None:
    from marivo.analysis_py.errors import SessionStateError

    state = session.state
    if session.layout.meta_file.is_file():
        state = read_session_meta(session.layout).get("state", state)
    if state == "archived":
        session.state = "archived"
        raise SessionStateError(message=f"session '{session.name}' is archived")
