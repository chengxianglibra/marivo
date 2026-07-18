"""Bounded, side-effect-free reads over historical analysis sessions."""

from __future__ import annotations

from dataclasses import dataclass

from marivo.analysis._pages import _BoundedPage, decode_keyset_cursor, encode_keyset_cursor
from marivo.analysis.errors import AnalysisRepair, SessionNotFoundError
from marivo.analysis.session._layout import PersistenceLayout
from marivo.analysis.session._store import SessionStore, SessionSummary
from marivo.analysis.session.core import (
    FrameSummaryPage,
    JobSummary,
    _read_frame_summary_page,
    _read_job_summaries,
)
from marivo.introspection.live.model import LiveHelpTarget
from marivo.render import Card, RenderableResult


class SessionSummaryPage(_BoundedPage[SessionSummary]):
    """Bounded newest-first page of recently updated sessions."""


@dataclass(frozen=True, repr=False)
class SessionInspection(RenderableResult):
    """Bounded immutable metadata snapshot for one historical session."""

    summary: SessionSummary
    frames: FrameSummaryPage
    recent_jobs: tuple[JobSummary, ...]

    def _repr_identity(self) -> str:
        return f"SessionInspection id={self.summary.id} name={self.summary.name}"

    def _card(self) -> Card:
        card = Card(
            identity=self._repr_identity(),
            available=(".summary", ".frames", ".recent_jobs", ".render()", ".show()"),
        ).status(
            f"frames={self.summary.frame_count} jobs={self.summary.job_count} "
            f"updated={self.summary.updated_at}"
        )
        if self.summary.question:
            card.field("question", self.summary.question)
        card.listing("recent_frames", (repr(item) for item in self.frames.items))
        card.listing("recent_jobs", (repr(item) for item in self.recent_jobs))
        if self.frames.has_more:
            card.field("frames", f"more available after {len(self.frames.items)} retained entries")
        return card


def recent_sessions(*, limit: int, cursor: str | None) -> SessionSummaryPage:
    """Return one bounded page of recently updated project sessions."""
    if not 1 <= limit <= 100:
        raise ValueError("session.recent limit must be within [1, 100]")
    after: tuple[str, str] | None = None
    if cursor is not None:
        updated_at, identity = decode_keyset_cursor(cursor)
        if not isinstance(updated_at, str):
            raise ValueError("session.recent cursor has an invalid sort key")
        after = (updated_at, identity)
    store = SessionStore()
    summaries = store.page_sessions(limit=limit, after=after)
    has_more = len(summaries) > limit
    items = tuple(summaries[:limit])
    next_cursor = None
    if has_more:
        last = items[-1]
        next_cursor = encode_keyset_cursor(last.updated_at, last.id)
    return SessionSummaryPage(
        items=items,
        limit=limit,
        has_more=has_more,
        next_cursor=next_cursor,
    )


def inspect_session(*, name: str, frame_limit: int, job_limit: int) -> SessionInspection:
    """Return a bounded session snapshot without resuming or touching it."""
    if not 1 <= frame_limit <= 100:
        raise ValueError("session.inspect frame_limit must be within [1, 100]")
    if not 1 <= job_limit <= 100:
        raise ValueError("session.inspect job_limit must be within [1, 100]")
    store = SessionStore()
    summary = store.session_summary(name)
    if summary is None:
        candidates = tuple(item.name for item in store.page_sessions(limit=10, after=None)[:10])
        raise SessionNotFoundError(
            message=f"analysis session {name!r} was not found in the current project",
            expected="an existing project session name",
            received=name,
            location="mv.session.inspect(name=...)",
            repair=AnalysisRepair(
                kind="inspect",
                action="Read mv.session.recent() and inspect one of the returned session names.",
                help_target=LiveHelpTarget(surface="analysis", canonical_id="session.recent"),
                candidates=candidates,
            ),
        )
    layout = PersistenceLayout(project_root=store.project_root, session_id=summary.id)
    frames = _read_frame_summary_page(
        store=store,
        project_root=store.project_root,
        session_id=summary.id,
        kind=None,
        evidence_status=None,
        limit=frame_limit,
        cursor=None,
    )
    jobs = _read_job_summaries(store=store, layout=layout, session_id=summary.id)
    return SessionInspection(summary=summary, frames=frames, recent_jobs=tuple(jobs[-job_limit:]))


__all__ = ["SessionInspection", "SessionSummaryPage", "inspect_session", "recent_sessions"]
