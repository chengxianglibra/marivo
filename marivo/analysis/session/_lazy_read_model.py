"""Immutable terminal projections for the Session production read surface."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from marivo.analysis._pages import _BoundedPage
from marivo.analysis.materialization.contracts import invalid
from marivo.render import Card, RenderableResult


def _identity(value: str) -> None:
    if (
        type(value) is not str
        or not value
        or len(value.encode()) > 4096
        or "\n" in value
        or "\r" in value
    ):
        raise invalid("invalid bounded runtime identity")


def _aware(value: datetime) -> None:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise invalid("runtime timestamp must be timezone-aware")


def _count(value: int) -> None:
    if type(value) is not int or value < 0:
        raise invalid("runtime count must be a non-negative integer")


def _page_fields(items: tuple[object, ...], limit: int, has_more: bool, cursor: str | None) -> None:
    if type(items) is not tuple or type(limit) is not int or not 1 <= limit <= 100:
        raise invalid("invalid immutable page collection or limit")
    if type(has_more) is not bool or len(items) > limit:
        raise invalid("invalid bounded page state")
    if cursor is not None and (type(cursor) is not str or not cursor or len(cursor) > 16_384):
        raise invalid("invalid bounded page cursor")
    if has_more != (cursor is not None):
        raise invalid("page continuation contradicts has_more")


@dataclass(frozen=True, repr=False, slots=True, kw_only=True)
class SessionSummary(RenderableResult):
    id: str
    name: str
    question: str | None
    created_at: datetime
    updated_at: datetime
    artifact_count: int

    def __post_init__(self) -> None:
        _identity(self.id)
        _identity(self.name)
        _aware(self.created_at)
        _aware(self.updated_at)
        _count(self.artifact_count)

    def _repr_identity(self) -> str:
        return f"SessionSummary id={self.id} name={self.name}"[:256]

    def _card(self) -> Card:
        return (
            Card(identity=self._repr_identity(), available=(".show()",))
            .field("question", self.question or "(none)")
            .field("created_at", self.created_at.isoformat())
            .field("updated_at", self.updated_at.isoformat())
            .field("artifacts", str(self.artifact_count))
        )


@dataclass(frozen=True, repr=False, slots=True)
class SessionSummaryPage(_BoundedPage[SessionSummary]):
    """Bounded newest-updated-first Session summaries."""

    def __post_init__(self) -> None:
        _page_fields(self.items, self.limit, self.has_more, self.next_cursor)
        if any(type(value) is not SessionSummary for value in self.items):
            raise invalid("Session page contains an unregistered summary value")


__all__: list[str] = []
