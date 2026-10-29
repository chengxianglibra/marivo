"""Detached immutable pages of completed Frame output."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Literal, TypeAlias

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from marivo.analysis.core.frame_interpretation import FrameAxisSchema, FrameComparison
from marivo.analysis.core.frame_interpretation import FrameKeyScalar as FrameKeyScalar
from marivo.analysis.core.frame_schema import FrameExportColumn
from marivo.render import Card, RenderableResult


def detached_rows(rows: pd.DataFrame) -> pd.DataFrame:
    """Copy selected Arrow buffers too; pandas deep copies retain their parents."""
    values: dict[str, pd.Series] = {}
    for name in rows.columns:
        column = rows[name]
        if isinstance(column.dtype, pd.ArrowDtype):
            array = pa.array(column, from_pandas=True)
            copied = pc.take(array, pa.array(range(len(array)), type=pa.int64()))
            values[name] = pd.Series(pd.arrays.ArrowExtensionArray(copied))
        else:
            values[name] = column.reset_index(drop=True).copy(deep=True)
    return pd.DataFrame(values, index=pd.RangeIndex(len(rows)))


@dataclass(frozen=True, slots=True, repr=False)
class FrameReadStats(RenderableResult):
    """Captured work counters for one data-page read.

    Acquired from FramePage.read_stats; direct construction is unsupported.
    Fields capture decoded data/coordinate rows and receipt-hash byte counts.

    Example:
        ``page.read_stats.show()``.

    Constraints:
        Counts include scanned records and repeated scans; they do not
        measure all disk I/O, elapsed time or peak memory.
    """

    data_rows_read: int
    coordinate_rows_read: int
    receipt_bytes_hashed: int

    def _repr_identity(self) -> str:
        return (
            f"FrameReadStats data_rows={self.data_rows_read} "
            f"coordinate_rows={self.coordinate_rows_read} bytes_hashed={self.receipt_bytes_hashed}"
        )

    def _card(self) -> Card:
        return (
            Card(identity=self._repr_identity(), available=(".show()",))
            .field("data_rows_read", str(self.data_rows_read))
            .field("coordinate_rows_read", str(self.coordinate_rows_read))
            .field("receipt_bytes_hashed", str(self.receipt_bytes_hashed))
        )


@dataclass(frozen=True, slots=True, repr=False)
class FramePage(RenderableResult):
    """One detached, ordered projection of a fixed Frame result.

    Acquired with ``frame.read_page(...)``; direct construction is unsupported.
    Fields contain immutable layout, complete keys, pagination and read statistics.

    Example:
        ``rows = fixed.read_page(limit=100).to_pandas()``.

    Constraints:
        Retains only this page, with no Frame, reader or complete audit.
        Coordinates are scoped to input_id; RangeIndex is not a business key.
    """

    input_id: str
    layout: tuple[FrameExportColumn, ...]
    coordinates: Mapping[str, tuple[FrameKeyScalar, ...]]
    limit: int
    total_count: int
    has_more: bool
    next_cursor: str | None
    read_stats: FrameReadStats
    _rows: pd.DataFrame = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "coordinates", MappingProxyType(dict(self.coordinates)))
        object.__setattr__(self, "_rows", detached_rows(self._rows))

    def __len__(self) -> int:
        """Return this page's actual row count without reading data."""
        return len(self._rows)

    def to_pandas(self) -> pd.DataFrame:
        """Return an isolated copy of this page's table.

        Args: None.
        Returns: A DataFrame in layout order, with empty attrs and a page-local RangeIndex.
        Example: ``rows = page.to_pandas()``.
        Constraints: Reads no files and does not change captured read_stats.
        """
        return detached_rows(self._rows)

    def _repr_identity(self) -> str:
        return (
            f"FramePage input={self.input_id[:24]} rows={len(self)} "
            f"total={self.total_count} has_more={self.has_more}"
        )

    def _card(self) -> Card:
        card = Card(
            identity=self._repr_identity(),
            available=(".layout", ".coordinates", ".read_stats", ".to_pandas()", ".show()"),
        ).field("limit", str(self.limit))
        if self.next_cursor is not None:
            card.field("next_cursor", self.next_cursor)
        return card


class _PeriodRecord(RenderableResult):
    key: tuple[FrameKeyScalar, ...]

    def _key_identity(self) -> str:
        values = (
            f"{value.type}:{value.value}"
            if isinstance(value, (pa.Date32Scalar, pa.TimestampScalar, pa.DurationScalar))
            else repr(value)[:80]
            for value in self.key[:4]
        )
        text = ", ".join(values)
        return text[:200] + ("..." if len(text) > 200 or len(self.key) > 4 else "")

    def _repr_identity(self) -> str:
        return f"{type(self).__name__} key=({self._key_identity()})"

    def _card(self) -> Card:
        from dataclasses import fields, is_dataclass

        assert is_dataclass(self)
        card = Card(identity=self._repr_identity(), available=(".show()",))
        for item in fields(self):
            value = self._key_identity() if item.name == "key" else repr(getattr(self, item.name))
            card.field(item.name, value[:240] + ("..." if len(value) > 240 else ""))
        return card


@dataclass(frozen=True, slots=True, repr=False)
class FrameCalendarPeriod(_PeriodRecord):
    """One saved calendar period acquired from ``FramePeriodPage.items``.

    Fields retain the complete key, actual/nominal UTC bounds, original ordinals,
    timezone, label and saved civil boundaries. Example: ``page.items[0].show()``.
    Constraints: No current timezone rules or shift history are reconstructed.
    """

    key: tuple[FrameKeyScalar, ...]
    timezone: str
    start: datetime
    end: datetime
    label: bool | str | int | float
    ordinal: int
    nominal_ordinal: int
    nominal_start: datetime
    nominal_end: datetime
    nominal_start_civil: str
    nominal_end_civil: str
    actual_start_civil: str
    actual_end_civil: str
    kind: Literal["calendar"] = field(default="calendar", init=False)


@dataclass(frozen=True, slots=True, repr=False)
class FrameIntervalPeriod(_PeriodRecord):
    """One saved interval acquired from ``FramePeriodPage.items``.

    Fields retain its complete key, UTC bounds and boundary timezone.
    Example: ``page.items[0].show()``.
    Constraints: Unsaved nominal or civil boundaries are not inferred.
    """

    key: tuple[FrameKeyScalar, ...]
    timezone: str
    start: datetime
    end: datetime
    kind: Literal["interval"] = field(default="interval", init=False)


FramePeriod: TypeAlias = FrameCalendarPeriod | FrameIntervalPeriod


@dataclass(frozen=True, slots=True, repr=False)
class FramePairedPeriod(_PeriodRecord):
    """A saved pair and its complete owning key, acquired from a period page.

    Example: ``page.items[0].left``.
    Constraints: One endpoint may be absent; the saved pair does not record why.
    """

    key: tuple[FrameKeyScalar, ...]
    left: FramePeriod | None
    right: FramePeriod | None
    kind: Literal["paired"] = field(default="paired", init=False)


@dataclass(frozen=True, slots=True, repr=False)
class FrameComparisonPeriod(_PeriodRecord):
    """One consumed correspondence acquired from a time-comparison period page.

    Fields retain the owning axis key and actual current/baseline endpoints.
    Example: ``page.items[0].baseline``.
    Constraints: A missing baseline carries its saved reason, independently of row presence.
    """

    key: tuple[FrameKeyScalar, ...]
    current: FramePeriod
    baseline: FramePeriod | None
    absent_reason: Literal["period_absent", "cutoff_absent", "unmatched"] | None
    kind: Literal["comparison"] = field(default="comparison", init=False)


FramePeriodItem: TypeAlias = FramePeriod | FramePairedPeriod | FrameComparisonPeriod


@dataclass(frozen=True, slots=True, repr=False)
class FramePeriodPage(RenderableResult):
    """One bounded immutable page acquired with ``frame.read_periods(target)``.

    Fields capture the exact input, inert target, selected records and continuation.
    Example: ``page = fixed.read_periods(fixed.axes[0], limit=100)``.
    Constraints: Retains this page and its endpoints only; period keys join to
    ``read_page().coordinates`` and selected periods need not have current rows.
    """

    input_id: str
    target: FrameAxisSchema | FrameComparison
    items: tuple[FramePeriodItem, ...]
    limit: int
    total_count: int
    has_more: bool
    next_cursor: str | None
    read_stats: FrameReadStats

    def __len__(self) -> int:
        """Return this page's record count without reading data."""
        return len(self.items)

    def _repr_identity(self) -> str:
        return (
            f"FramePeriodPage input={self.input_id[:24]} items={len(self)} "
            f"total={self.total_count} has_more={self.has_more}"
        )

    def _card(self) -> Card:
        card = Card(
            identity=self._repr_identity(),
            available=(".target", ".items", ".read_stats", ".show()"),
        ).field("limit", str(self.limit))
        if self.next_cursor is not None:
            card.field("next_cursor", self.next_cursor)
        return card
