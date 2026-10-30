"""Immutable facts captured at successful source-read completion boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from marivo.analysis.core.model import reject
from marivo.render import Card, RenderableResult


def _text(value: str) -> None:
    if type(value) is not str or not value or any(char in value for char in "\n\r\t"):
        reject(
            "a nonempty single-line source-read identity",
            repr(value),
            "Retain the captured source-read identity.",
            "frame.source_reads",
        )


def _utc(value: datetime) -> None:
    if type(value) is not datetime or value.tzinfo is not timezone.utc:
        reject(
            "a plain UTC-aware source-read instant",
            repr(value),
            "Preserve the captured instant in UTC.",
            "frame.source_reads",
        )


@dataclass(frozen=True, slots=True, repr=False)
class FrameSourceReadPeriod(RenderableResult):
    """Captured half-open time restriction, acquired through ``saved.source_reads``.

    Fields are the optional UTC lower bound, required UTC upper bound and original
    time-zone identity. A missing lower bound differs from an absent period.
    """

    start: datetime | None = field(
        metadata={
            "meaning": "Captured inclusive UTC lower bound, or None for a known period without a lower bound. An absent period is a separate state."
        }
    )
    end: datetime = field(
        metadata={
            "meaning": "Captured exclusive UTC upper bound of the original source-read period; fixed operations do not replace it with current result bounds."
        }
    )
    timezone: str = field(
        metadata={
            "meaning": "Original timezone identity used to interpret this captured period. Bounds remain UTC instants."
        }
    )

    def __post_init__(self) -> None:
        if self.start is not None:
            _utc(self.start)
        _utc(self.end)
        _text(self.timezone)
        if self.start is not None and self.start >= self.end:
            reject(
                "a half-open source-read period with start before end",
                repr((self.start, self.end)),
                "Retain the exact nonempty captured period.",
                "frame.source_reads",
            )

    def _repr_identity(self) -> str:
        return f"FrameSourceReadPeriod end={self.end.isoformat()} timezone={self.timezone[:48]}"

    def _card(self) -> Card:
        return (
            Card(identity=self._repr_identity(), available=(".start", ".end", ".timezone"))
            .field("start", "None" if self.start is None else self.start.isoformat())
            .field("end", self.end.isoformat())
            .field("timezone", self.timezone)
        )


@dataclass(frozen=True, slots=True, repr=False)
class FrameSourceReadScope(RenderableResult):
    """One source and optional captured period within an actual read event.

    Acquired from ``saved.source_reads[0].scopes``. No period means this record
    cannot state an explicit time restriction; it does not imply no constraints.
    """

    source: str = field(
        metadata={
            "meaning": "Non-secret captured source identifier, not an executable handle, URL or connection string."
        }
    )
    period: FrameSourceReadPeriod | None = field(
        metadata={
            "meaning": "Known original explicit time restriction, or None when no period was captured. None does not imply an unfiltered or all-time read; complete historical filters are not retained."
        }
    )

    def __post_init__(self) -> None:
        _text(self.source)
        if self.period is not None and type(self.period) is not FrameSourceReadPeriod:
            reject(
                "a captured FrameSourceReadPeriod or None",
                type(self.period).__name__,
                "Retain the typed captured source period.",
                "frame.source_reads",
            )

    def _repr_identity(self) -> str:
        return f"FrameSourceReadScope source={self.source[:100]!r}"

    def _card(self) -> Card:
        return (
            Card(identity=self._repr_identity(), available=(".source", ".period"))
            .field("source", self.source)
            .field("period", repr(self.period))
        )


@dataclass(frozen=True, slots=True, repr=False)
class FrameSourceRead(RenderableResult):
    """One successful actual read, acquired from ``saved.source_reads``.

    The immutable ID identifies the read event. Completion is UTC; scopes retain
    all known source bindings. Returned rows are the raw decoded count, or None
    when unavailable, and are not final Frame or scanned row counts.
    """

    read_id: str = field(
        metadata={
            "meaning": "Immutable actual-read event identity, preserved across inherited results and used for deduplication. Independent reads retain distinct IDs; no lookup by ID is provided."
        }
    )
    completed_at: datetime = field(
        metadata={
            "meaning": "UTC completion time at the successful read-processing boundary. It is distinct from evaluation/commit times and is not a database snapshot or pure network timestamp."
        }
    )
    scopes: tuple[FrameSourceReadScope, ...] = field(
        metadata={
            "meaning": "Nonempty ordered original source/time bindings for one actual read. Shared logical consumers do not create extra reads or multiply returned_rows; overlapping periods are not merged."
        }
    )
    returned_rows: int | None = field(
        metadata={
            "meaning": "Reliable raw returned-row count: 0 means a known empty read and None means unavailable. It is not scanned rows, business events or final Frame rows."
        }
    )

    def __post_init__(self) -> None:
        _text(self.read_id)
        _utc(self.completed_at)
        if (
            type(self.scopes) is not tuple
            or not self.scopes
            or any(type(scope) is not FrameSourceReadScope for scope in self.scopes)
            or len(set(self.scopes)) != len(self.scopes)
        ):
            reject(
                "a nonempty immutable tuple of distinct typed source-read scopes",
                repr(self.scopes),
                "Retain each actual source and time binding once in encounter order.",
                "frame.source_reads",
            )
        if self.returned_rows is not None and (
            type(self.returned_rows) is not int or self.returned_rows < 0
        ):
            reject(
                "a nonnegative integer raw returned-row count or None",
                repr(self.returned_rows),
                "Use the captured raw count, or None when it is unknown.",
                "frame.source_reads",
            )

    def _repr_identity(self) -> str:
        return f"FrameSourceRead read_id={self.read_id[:100]!r} scopes={len(self.scopes)}"

    def _card(self) -> Card:
        return (
            Card(
                identity=self._repr_identity(),
                available=(".read_id", ".completed_at", ".scopes", ".returned_rows"),
            )
            .field("read_id", self.read_id)
            .field("completed_at", self.completed_at.isoformat())
            .field("returned_rows", str(self.returned_rows))
            .table(
                ("source", "period"),
                ((scope.source, repr(scope.period)) for scope in self.scopes),
                row_count=len(self.scopes),
                label="scopes",
            )
        )


def merge_source_reads(*groups: tuple[FrameSourceRead, ...]) -> tuple[FrameSourceRead, ...]:
    """Merge completed inputs in encounter order, rejecting conflicting IDs."""
    merged: dict[str, FrameSourceRead] = {}
    for group in groups:
        for read in group:
            known = merged.get(read.read_id)
            if known is not None and known != read:
                reject(
                    "identical complete source-read facts for one read identity",
                    read.read_id,
                    "Preserve the exact original read record on every explicit input.",
                    "frame.source_reads",
                    canonical_id="analysis_frame.source_reads",
                )
            merged.setdefault(read.read_id, read)
    return tuple(merged.values())
