"""Bounded, side-effect-free reads over historical analysis sessions."""

from __future__ import annotations

from dataclasses import dataclass

from marivo.analysis._pages import _BoundedPage, decode_keyset_cursor, encode_keyset_cursor
from marivo.analysis.errors import AnalysisRepair, SessionNotFoundError
from marivo.analysis.session._read_model import RunPage
from marivo.analysis.session._store import SessionStore, SessionSummary
from marivo.introspection.live.model import LiveHelpTarget
from marivo.render import Card, RenderableResult


class SessionSummaryPage(_BoundedPage[SessionSummary]):
    """Bounded newest-first page of recently updated sessions."""


@dataclass(frozen=True, repr=False)
class SessionInspection(RenderableResult):
    """Bounded immutable metadata snapshot for one historical session."""

    summary: SessionSummary
    runs: RunPage

    def _repr_identity(self) -> str:
        return f"SessionInspection id={self.summary.id} name={self.summary.name}"

    def _card(self) -> Card:
        card = Card(
            identity=self._repr_identity(),
            available=(".summary", ".runs", ".show()"),
        ).status(
            f"artifacts={self.summary.frame_count} runs={self.summary.job_count} "
            f"updated={self.summary.updated_at}"
        )
        if self.summary.question:
            card.field("question", self.summary.question)
        card.listing("runs", (repr(item) for item in self.runs.items))
        if self.runs.has_more:
            card.field("runs", f"more available after {len(self.runs.items)} retained entries")
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


def inspect_session(*, name: str, run_limit: int, run_cursor: str | None) -> SessionInspection:
    """Return a bounded session snapshot without resuming or touching it."""
    if not 1 <= run_limit <= 100:
        raise ValueError("session.inspect run_limit must be within [1, 100]")
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
    store.validate_session_runtime_schema(summary.id)
    from marivo.analysis.session._runtime_reads import read_run_page

    runs = read_run_page(
        store=store,
        session_id=summary.id,
        limit=run_limit,
        cursor=run_cursor,
    )
    return SessionInspection(summary=summary, runs=runs)


__all__ = ["SessionInspection", "SessionSummaryPage", "inspect_session", "recent_sessions"]
